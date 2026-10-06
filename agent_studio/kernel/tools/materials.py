"""找教材 / 下教材 —— 让 Agent 自己去网上把教材找回来。

三步走，**中间必须有人点头**：

    1) search_materials  去真实搜索引擎找候选（不是凭记忆编链接）
    2) （模型把候选列给学生看，问「要下哪个」）      ← 用户确认在这一步
    3) download_material 下载 + 转 Markdown，放进课程的 section_N/

为什么要"真搜索"而不是让模型凭记忆给链接：
模型给的书名和链接经常看着很像真的，点开却是 404 或不相干的东西 ——
学生被骗一次就不再信了。所以这里的搜索走真实搜索引擎，
并且**每个候选都先探一次能不能打开**（拿到了多大、什么类型），只把活得链接端上去。

安全护栏（都在 course/file_utils.py 里，老项目验证过）：
    · 只允许 http/https，且禁止内网地址（防 SSRF：别让它把内网接口当教材下回来）
    · 只允许常见文档后缀（.pdf/.docx/.pptx/.txt/.md/.html…）
    · 下载超时 + 大小上限
"""

from __future__ import annotations

import re
import sys
import urllib.parse
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from ..config import DATA_DIR, ROOT
from ..registry import ToolContext, ToolResult, tool

# 课程数据层（下载/转换工具都在里面，老项目原样移植过来的）
COURSE_DIR = ROOT / "course"
if str(COURSE_DIR) not in sys.path:
    sys.path.insert(0, str(COURSE_DIR))

import storage  # noqa: E402
from file_utils import ALLOWED_EXTENSIONS, convert_document_to_markdown, download_resource, safe_url_check  # noqa: E402

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
SEARCH_TIMEOUT = 20
PROBE_TIMEOUT = 12
MAX_CANDIDATES = 8
MAX_DOWNLOAD_BYTES = 60 * 1024 * 1024          # 60MB：教材 PDF 够用，防手滑拖个电影

# 相关度打分：搜索引擎对「9709 教材」这种查询经常混进游戏/购物站点，
# 端给学生之前先按「像不像教材」排个序，把明显不相关的丢掉。
_GOOD_DOMAINS = (
    "pep.com.cn", "moe.gov.cn", "edu.cn", "gov.cn", "cie.org.uk", "cambridgeinternational.org",
    "edexcel.com", "qualifications.pearson.com", "ocr.org.uk", "aqa.org.uk", "ccea.org.uk",
    "openstax.org", "ocw.mit.edu", "khanacademy.org", "coursera.org", "edx.org",
    "wenku.baidu.com", "docin.com", "book118.com", "cnki.net", "zxxk.com", "21cnjy.com",
    "baidu.com", "sohu.com", "csdn.net", "gitee.com", "github.com",
)
_BAD_DOMAINS = (
    "poki.com", "4399.com", "7k7k.com", "taobao.com", "tmall.com", "jd.com", "pinduoduo.com",
    "douyin.com", "kuaishou.com", "weibo.com", "bilibili.com", "youtube.com", "netflix.com",
    "trip.com", "ctrip.com", "meituan.com", "dianping.com",
)
_GOOD_WORDS = ("教材", "课本", "教科书", "大纲", "真题", "试题", "讲义", "课件", "题集", "考纲",
               "syllabus", "textbook", "past paper", "specification", "revision", "worksheet", "pdf")


def _domain(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def score_candidate(item: Dict[str, Any], kind: str = "") -> int:
    """给候选打「像不像教学资料」的分。负数表示明显不相关。"""
    url = item.get("url") or ""
    text = f"{item.get('title','')} {item.get('snippet','')}".lower()
    score = 0
    if item.get("is_document") or item.get("type") in ("pdf", "docx", "doc", "pptx", "ppt"):
        score += 3
    host = _domain(url)
    if any(d in host for d in _GOOD_DOMAINS):
        score += 2
    if any(d in host for d in _BAD_DOMAINS):
        score -= 6
    if any(w in text for w in _GOOD_WORDS):
        score += 2
    if kind and kind in text:
        score += 1
    # 标题/网址里出现查询里的关键词（去掉空格后粗匹配）
    for token in re.split(r"\s+", (item.get("query") or "")):
        if len(token) >= 3 and token.lower() in text:
            score += 1
    return score


# ---------------------------------------------------------------------------
# 搜索：走真实搜索引擎
# ---------------------------------------------------------------------------
# 为什么默认用 Bing 中国而不是 DuckDuckGo：
# 实测（本机网络）DuckDuckGo / gnu.org 这类境外站点**连不上**（ConnectTimeout），
# 而 cn.bing.com、baidu.com、人民教育出版社官网都正常。
# 也就是说「搜索引擎选谁」取决于部署环境 —— 所以这里做成**按顺序试**，
# 谁先给出结果就用谁，全失败就如实告诉模型「搜不了，你自己推荐并用 check_link 验」。
def _unwrap_bing(href: str) -> str:
    """Bing 有时把结果包成 /ck/a?...&u=a1<base64url>，要还原出真实地址。"""
    if "bing.com/ck/a" not in href:
        return href
    m = re.search(r"[?&]u=a1([^&]+)", href)
    if not m:
        return href
    raw = m.group(1)
    pad = "=" * (-len(raw) % 4)
    try:
        import base64

        return base64.urlsafe_b64decode(raw + pad).decode("utf-8", "replace")
    except Exception:
        return href


def _search_bing(query: str, limit: int) -> List[Dict[str, str]]:
    resp = requests.get(
        "https://cn.bing.com/search",
        params={"q": query, "ensearch": "0"},
        headers=UA,
        timeout=SEARCH_TIMEOUT,
    )
    resp.raise_for_status()
    html = resp.text
    out: List[Dict[str, str]] = []
    # 每条结果是一个 <li class="b_algo">…</li>
    for block in re.findall(r'<li class="b_algo".*?</li>', html, re.S):
        m = re.search(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', block, re.S)
        if not m:
            continue
        url = _unwrap_bing(m.group(1).strip())
        title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
        snip = ""
        ms = re.search(r'<p[^>]*>(.*?)</p>', block, re.S)
        if ms:
            snip = re.sub(r"<[^>]+>", "", ms.group(1)).strip()
        if not url.startswith("http") or not title:
            continue
        out.append({"title": title[:120], "url": url, "snippet": snip[:200]})
        if len(out) >= limit:
            break
    return out


def _search_baidu(query: str, limit: int) -> List[Dict[str, str]]:
    """百度兜底：它的结果链接是跳转地址，需要跟一次重定向才能拿到真实 URL。"""
    resp = requests.get(
        "https://www.baidu.com/s", params={"wd": query}, headers=UA, timeout=SEARCH_TIMEOUT
    )
    resp.raise_for_status()
    out: List[Dict[str, str]] = []
    blocks = re.findall(r'<h3[^>]*class="[^"]*t[^"]*"[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', resp.text, re.S)
    for href, title in blocks:
        title = re.sub(r"<[^>]+>", "", title).strip()
        if not href.startswith("http") or not title:
            continue
        real = href
        if "baidu.com/link?" in href:
            try:
                r2 = requests.head(href, headers=UA, timeout=PROBE_TIMEOUT, allow_redirects=True)
                real = r2.url or href
            except Exception:
                real = href
        out.append({"title": title[:120], "url": real, "snippet": ""})
        if len(out) >= limit:
            break
    return out


def web_search(query: str, limit: int = MAX_CANDIDATES) -> List[Dict[str, str]]:
    """真实搜索，返回 [{title, url, snippet}]。按顺序试多个引擎，全失败则抛最后一个异常。"""
    engines = (
        ("bing", _search_bing),
        ("baidu", _search_baidu),
    )
    last_exc: Optional[Exception] = None
    for name, fn in engines:
        try:
            results = fn(query, limit)
            if results:
                return results
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            print(f"[materials] {name} 搜索失败：{type(exc).__name__}: {exc}", flush=True)
            continue
    if last_exc is not None:
        raise last_exc
    return []


def _ext_of(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    return Path(path).suffix.lower()


def _looks_like_document(url: str, content_type: str = "") -> bool:
    ext = _ext_of(url)
    if ext in ALLOWED_EXTENSIONS:
        return True
    ct = (content_type or "").lower()
    return any(k in ct for k in ("pdf", "word", "document", "presentation", "text/plain", "octet-stream"))


def probe_link(url: str) -> Dict[str, Any]:
    """探一次链接：能不能打开、什么类型、多大。**只用来判断，不下载正文。**"""
    info: Dict[str, Any] = {"url": url, "ok": False, "type": _ext_of(url).lstrip(".") or "webpage",
                            "bytes": 0, "note": ""}
    if not safe_url_check(url):
        info["note"] = "地址不合法或指向内网（已拦下）"
        return info
    try:
        # 先 HEAD（省流量），有些站点不支持再退回 GET 只读头部
        resp = requests.head(url, headers=UA, timeout=PROBE_TIMEOUT, allow_redirects=True)
        if resp.status_code >= 400 or "content-length" not in {k.lower() for k in resp.headers}:
            resp = requests.get(url, headers=UA, timeout=PROBE_TIMEOUT, stream=True, allow_redirects=True)
            info["bytes"] = int(resp.headers.get("content-length") or 0)
            resp.close()
        else:
            info["bytes"] = int(resp.headers.get("content-length") or 0)
        info["ok"] = resp.status_code < 400
        ctype = resp.headers.get("content-type", "")
        if not _looks_like_document(url, ctype):
            info["type"] = (ctype.split(";")[0] or "webpage").strip()
        info["status"] = resp.status_code
    except Exception as exc:  # noqa: BLE001
        info["note"] = f"{type(exc).__name__}: {exc}"[:120]
    return info


def _human_size(n: int) -> str:
    if not n:
        return "大小未知"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n/1:.1f}{unit}" if False else f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.0f}B"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
@tool(
    "search_materials",
    "去网上**真实搜索**教学资料 / 教材 / 课件 / 真题（会逐个验证链接能不能打开）。"
    "学生说「帮我找教材」「有没有 PDF 版」「找点题练」时用它。"
    "**它只负责找和列出来，不会下载**——把候选编号列给学生，等他确认要哪个再调 download_material。",
    {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜什么，尽量具体，如「人教版 高中数学 必修一 PDF 教材」"},
            "kind": {"type": "string", "description": "资料类型提示（可选）：教材 / 课件 / 习题 / 真题 / 大纲"},
            "limit": {"type": "integer", "description": "候选数量，默认 6，最多 8"},
        },
        "required": ["query"],
    },
    timeout=90,
    produces=["ui"],
)
async def search_materials(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    query = str(args.get("query") or "").strip()
    if not query:
        return ToolResult.fail("请给出要搜的关键词（query）。")
    kind = str(args.get("kind") or "").strip()
    limit = max(1, min(int(args.get("limit") or 6), MAX_CANDIDATES))
    full_query = f"{query} {kind}".strip() if kind and kind not in query else query

    await ctx.push(_notice(f"正在搜索「{full_query}」…"))

    # 两路一起搜：原查询 + 限定 PDF。
    # 只搜原查询时，引擎会混进游戏/购物站；加上 filetype:pdf 往往能直接命中教材本体。
    queries = [full_query]
    if any(k in full_query for k in ("教材", "课本", "教科书", "真题", "试题", "讲义", "课件", "大纲")) \
            or kind in ("教材", "课件", "习题", "真题"):
        queries.append(f"{full_query} filetype:pdf")

    raw_results: List[Dict[str, Any]] = []
    errors: List[str] = []
    for q in queries:
        try:
            for item in web_search(q, limit=limit * 2):
                item["query"] = full_query
                raw_results.append(item)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    # 去重（同域名同标题只留一条）
    seen = set()
    deduped: List[Dict[str, Any]] = []
    for item in raw_results:
        key = (item.get("url", "").split("?")[0], item.get("title", "")[:30])
        if key in seen:
            continue
        seen.add(key)
        item["is_document"] = _ext_of(item.get("url", "")) in ALLOWED_EXTENSIONS
        deduped.append(item)

    if not deduped:
        detail = ("；".join(errors[:2]) if errors else "没有结果")
        return ToolResult.fail(
            f"搜索没成功（{detail}）。"
            "请改用你已有的知识推荐 2~3 个**你能确认存在的官方来源**（考试局官网、人教社、开放教材站点），"
            "把链接用 `check_link` 验一遍再给学生，并说明「我没能联网确认，请你点开看看」。"
        )

    # 逐个探活 + 打分：只把「能打开且像教学资料」的端给学生（死链和游戏站比没有更糟）
    verified: List[Dict[str, Any]] = []
    for item in deduped:
        info = probe_link(item["url"])
        if not info.get("ok"):
            continue
        item["type"] = info.get("type")
        item["bytes"] = info.get("bytes")
        item["score"] = score_candidate(item, kind)
        verified.append(item)

    if not verified:
        return ToolResult.fail(
            "搜到的链接**一个都打不开**（可能是网络受限或结果过期），所以不端给学生看。"
            "可以换关键词再搜一次，或让你基于已知的官方来源给出建议并说明无法验证。"
        )

    # 明显不相关的（游戏/购物站）直接丢掉；一个都不剩就**如实说搜不到**，
    # 而不是把游戏站当成教材端给学生（实测见过：搜「9709 教材」返回 Poki 游戏页）。
    relevant = [v for v in verified if v.get("score", 0) > 0]
    neutral = [v for v in verified if v.get("score", 0) == 0]
    junk_count = len([v for v in verified if v.get("score", 0) < 0])
    if junk_count:
        print(f"[materials] 剔除了 {junk_count} 个明显不相关的候选（域名黑名单/负分）", flush=True)
    final_list = (relevant or neutral)[:limit]
    if not final_list:
        return ToolResult.fail(
            "搜到的东西**全是游戏/购物这类无关页面**，一个像教材的都没有（关键词可能太泛）。"
            "请如实告诉学生这次没搜到，并建议换更具体的说法："
            "例如「人教版 高中数学 必修一 教材」，或考纲类加上考试局与代码（「CAIE 9709 Pure Mathematics 1 syllabus PDF」）。"
        )
    unreliable = not relevant
    dropped_junk = junk_count

    rows = []
    for i, item in enumerate(final_list, 1):
        rows.append([
            str(i),
            item["title"][:58],
            item.get("type") or "网页",
            _human_size(int(item.get("bytes") or 0)),
            item["url"][:70],
        ])
    unreliable = len(relevant) == 0
    head = {
        "type": "callout",
        "tone": "warning" if unreliable else "info",
        "title": "这些结果可能不太相关" if unreliable else "先确认再下载",
        "content": ("搜索没找到像样的教材（关键词可能太泛）。建议换个更具体的说法，或直接告诉老师你手上有什么教材。"
                    if unreliable else
                    "下面是**验证过能打开**的候选。告诉老师你要哪一个（说编号就行），他才会下载。"),
    }
    spec = {
        "title": f"找到 {len(final_list)} 个候选（还没下载）",
        "items": [
            head,
            {"type": "table", "columns": ["编号", "标题", "类型", "大小", "链接"], "rows": rows,
             "types": ["num", "text", "badge", "text", "text"]},
        ],
    }
    listing = "\n".join(
        f"  {i}. {item['title'][:70]}｜{item.get('type')}｜{_human_size(int(item.get('bytes') or 0))}\n     {item['url']}"
        for i, item in enumerate(final_list, 1)
    )
    warn = ("\n⚠️ 注意：这批结果**看起来都不太像教材**（可能是关键词太泛）。"
            "先把这一点如实告诉学生，并建议换更具体的说法（例如加上教材全名、出版社、或「PDF」）。"
            if unreliable else "")
    return ToolResult(
        summary=(
            f"找到 {len(final_list)} 个候选（已逐个验证可打开，**尚未下载**"
            + (f"，另有 {dropped_junk} 个明显不相关的已剔除" if dropped_junk else "")
            + f"）：\n{listing}{warn}\n"
            "下一步：把候选说给学生（用编号），**问他确认要哪个**（或想要别的类型/更权威的来源）。"
            "他确认后再调 `download_material`。不要跳过确认直接下。"
        ),
        ui=spec,
        data={"query": full_query, "candidates": final_list, "unreliable": unreliable},
    )


@tool(
    "check_link",
    "验证一个具体链接能不能打开、是什么类型、多大（不下载）。"
    "当你想推荐某个你已知的教材来源（官网/开放教材）时，先用它验一遍再给学生。",
    {"type": "object", "properties": {"url": {"type": "string", "description": "要验证的链接"}}, "required": ["url"]},
    timeout=30,
)
async def check_link(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    url = str(args.get("url") or "").strip()
    if not url:
        return ToolResult.fail("请给出要验证的 url。")
    info = probe_link(url)
    if not info.get("ok"):
        reason = info.get("note") or f"HTTP {info.get('status')}"
        return ToolResult.fail(f"这个链接打不开或不可用：{url}（{reason}）。别把它推荐给学生。")
    return ToolResult(
        summary=f"链接可用：{url}｜类型 {info.get('type')}｜{_human_size(int(info.get('bytes') or 0))}",
        data=info,
    )


def _target_dir(ctx: ToolContext, unit_index: int = 0) -> tuple[Path, str]:
    """下载到哪：有课程就放进那一课的 section_N（这样讲课时能读到）；没课程就放会话工作目录。"""
    folder = getattr(ctx.session, "lesson_folder", None)
    if folder:
        try:
            index = unit_index or storage.next_unit_index(folder)
        except Exception:
            index = unit_index or 1
        section = DATA_DIR / "lessons" / folder / f"section_{index}"
        section.mkdir(parents=True, exist_ok=True)
        return section, f"课程《{folder}》第 {index} 课"
    return ctx.session.workspace, "本次对话的工作目录（还没有绑定课程）"


@tool(
    "download_material",
    "**在用户确认之后**下载一份资料，并自动转换成 Markdown 存进当前课程（没有课程时存进对话工作目录）。"
    "只下载 http/https 的常见文档，内网地址会被拒绝。",
    {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "要下载的链接（来自 search_materials 的候选）"},
            "filename": {"type": "string", "description": "保存的文件名（可选），如 '人教版必修一.pdf'"},
            "unit_index": {"type": "integer", "description": "存到第几课的资料目录，默认当前进度那一课"},
        },
        "required": ["url"],
    },
    timeout=180,
    produces=["artifact"],
)
async def download_material(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    url = str(args.get("url") or "").strip()
    filename = str(args.get("filename") or "").strip()
    try:
        unit_index = int(args.get("unit_index") or 0)
    except Exception:
        unit_index = 0
    if not url:
        return ToolResult.fail("请给出要下载的 url。")
    if not safe_url_check(url):
        return ToolResult.fail(
            "这个地址不允许下载（只支持 http/https 的公开地址，内网地址会被拒绝）。"
            "如果这是一个校园网/内网资源，让学生自己下载后上传给我。"
        )

    # 先探一次：把明显不是文档、或者死活打不开的挡在下载之前
    info = probe_link(url)
    if not info.get("ok"):
        return ToolResult.fail(f"这个链接打不开（{info.get('note') or info.get('status')}），没有下载。")
    size = int(info.get("bytes") or 0)
    if size and size > MAX_DOWNLOAD_BYTES:
        return ToolResult.fail(f"这个文件有 {_human_size(size)}，超过上限（{_human_size(MAX_DOWNLOAD_BYTES)}），已放弃下载。")
    if not _looks_like_document(url, str(info.get("type") or "")):
        return ToolResult.fail(
            f"这个链接看着不是文档（类型 {info.get('type')}）。可以先用 `fetch_page` 读它的正文，"
            "或者换一个 PDF / Word 的链接。"
        )

    target_dir, where = await _as_thread(_target_dir, ctx, unit_index)
    await ctx.push(_notice(f"正在下载…（{_human_size(size)}）"))

    safe_name = re.sub(r'[\\/:*?"<>|]+', "_", filename)[:80] if filename else ""
    try:
        path = await _as_thread(
            download_resource,
            {"url": url, "title": safe_name or Path(urllib.parse.urlparse(url).path).name or "material"},
            target_dir,
            "material",
        )
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"下载失败：{type(exc).__name__}: {exc}")

    converted = ""
    try:
        if path.suffix.lower() in (".pdf", ".docx", ".doc", ".pptx", ".ppt"):
            markdown = await _as_thread(convert_document_to_markdown, path)
            if markdown and markdown.strip():
                md_path = path.with_suffix(".md")
                md_path.write_text(markdown, encoding="utf-8")
                converted = f"，并已转成 Markdown（{md_path.name}，{len(markdown)} 字）"
    except Exception as exc:  # noqa: BLE001
        converted = f"（转 Markdown 失败：{type(exc).__name__}: {exc}）"

    rel = str(path.relative_to(DATA_DIR)) if str(path).startswith(str(DATA_DIR)) else path.name
    size_txt = _human_size(path.stat().st_size) if path.exists() else _human_size(size)
    return ToolResult(
        summary=(
            f"下载完成：`{path.name}`（{size_txt}）已存到{where}{converted}。\n"
            f"路径：{rel}\n"
            "接下来：告诉学生下好了、放在哪一课；讲那一课时用 `lesson_context`(part=unit) 就能读到它的内容。"
            "不要在正文里贴长段原文。"
        ),
        artifacts=[{
            "path": path.name, "name": path.name,
            "kind": path.suffix.lstrip("."), "url": ctx.session.file_url(path.name),
            "bytes": path.stat().st_size if path.exists() else size,
        }],
        data={"saved": str(path), "converted": bool(converted and converted.startswith("，"))},
    )


@tool(
    "fetch_page",
    "读一个网页的正文（去标签后的纯文本，截断），用来判断这份资料讲不讲得对、值不值得下。不保存文件。",
    {"type": "object", "properties": {"url": {"type": "string", "description": "网页地址"}}, "required": ["url"]},
    timeout=60,
)
async def fetch_page(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    url = str(args.get("url") or "").strip()
    if not url:
        return ToolResult.fail("请给出 url。")
    if not safe_url_check(url):
        return ToolResult.fail("这个地址不允许抓取（只支持 http/https 的公开地址）。")
    try:
        resp = await _as_thread(requests.get, url, headers=UA, timeout=SEARCH_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        return ToolResult.fail(f"抓取失败：{type(exc).__name__}: {exc}")
    html = resp.text or ""
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;?", " ", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    if not text:
        return ToolResult.fail("这个页面没有可读文本（可能是纯图片或需要登录）。")
    snippet = text[:4000]
    return ToolResult(
        summary=f"页面正文（前 {len(snippet)} 字，已去标签）：\n{snippet}",
        data={"url": url, "chars": len(text)},
    )


@tool(
    "list_materials",
    "列出当前课程已经下载过的资料（文件与转换后的 Markdown）。学生问「教材下了吗」「我们有哪些资料」时用它。",
    {"type": "object", "properties": {}, "required": []},
)
async def list_materials(ctx: ToolContext, args: Dict[str, Any]) -> ToolResult:
    folder = getattr(ctx.session, "lesson_folder", None)
    if not folder:
        return ToolResult.fail("当前还没有绑定课程。可以先 `prepare_course` 备课，或直接在对话里问我要资料。")
    root = DATA_DIR / "lessons" / folder
    items: List[Dict[str, Any]] = []
    for section in sorted(root.glob("section_*")):
        for f in sorted(section.iterdir()):
            if f.is_file() and f.name != "unit.md":
                items.append({"unit": section.name, "name": f.name, "bytes": f.stat().st_size})
    if not items:
        return ToolResult(summary=f"课程《{folder}》还没有下载任何资料（只有备课时生成的教案）。")
    listing = "\n".join(f"  - {i['unit']}/{i['name']}（{_human_size(i['bytes'])}）" for i in items[:30])
    return ToolResult(
        summary=f"课程《{folder}》已有的资料：\n{listing}\n讲课时用 `lesson_context`(part=unit) 就能读到。",
        data={"materials": items},
    )


# ---------------------------------------------------------------------------
def _notice(message: str):
    from .. import events

    return events.notice(message, level="info")


async def _as_thread(fn, *fn_args):
    import asyncio

    return await asyncio.to_thread(fn, *fn_args)
