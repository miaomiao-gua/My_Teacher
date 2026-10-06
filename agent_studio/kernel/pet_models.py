"""桌宠形象清单：**扫描磁盘上现成的 Live2D 模型**，给学生一个「换形象」的清单。

为什么要扫盘而不是写死一个列表：
用户手上本来就有好几个模型（自己上传的、官方免费版、别的项目里的），
分布在 client/、桌面、Open-LLM-VTuber/ 这些地方。写死路径的话，他换一个模型我就得改一次代码；
扫盘 + 记住他的选择，他就能自己在界面上点着换。

三条硬要求（都是吃过亏的）：
1. **只读**：本模块绝不改动用户模型目录里的任何文件；
2. **稳**：id 不能随扫描顺序变（否则「记住的选择」下次就失效），所以 id 由模型所在
   目录的路径哈希得到，而不是数组下标；
3. **快**：扫盘结果缓存 60 秒（界面上换形象会连着问好几次，不能每次都全盘 rglob）。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .paths import data_root

# 扫盘深度上限：`Desktop` 这种目录往深了挖会很慢，而模型一般都是「目录/模型.model3.json」
_MAX_DEPTH = 4
_SCAN_TTL = 60.0
_cache: Dict[str, Any] = {"at": 0.0, "items": []}


def upload_root() -> Path:
    """学生自己上传的形象放这儿（可写、跟着 data/ 走，exe 形态也有效）"""
    return data_root() / "live2d_models"


def _roots() -> List[Path]:
    """到哪里找模型。按「越可能常用越靠前」排。"""
    from .config import CLIENT_DIR, LEGACY_ROOT

    home = Path.home()
    out = [
        upload_root(),                             # 学生自己上传的，排最前
        CLIENT_DIR / "static" / "models",          # 项目自带的（打包进 exe，最稳）
        LEGACY_ROOT / "client" / "static" / "models",   # 用户自己上传的（含 白-免费版）
        LEGACY_ROOT / "Open-LLM-VTuber" / "live2d-models",
        home / "Desktop",                          # 桌面上放着的免费模型（hiyori / 小熊貓 …）
        home / "Downloads",
    ]
    # 额外目录：写在 data/pet_model_dirs.json 里（一行一个路径），不用改代码
    extra = data_root() / "pet_model_dirs.json"
    if extra.is_file():
        try:
            raw = json.loads(extra.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                out = [Path(str(x)) for x in raw] + out
        except Exception:
            pass
    seen, uniq = set(), []
    for p in out:
        try:
            key = str(p.resolve()).lower()
        except Exception:
            continue
        if key not in seen and p.is_dir():
            seen.add(key)
            uniq.append(p)
    return uniq


# 这些目录名没有信息量，不该拿去当形象名字（hiyori 那套的目录就叫 runtime）
_GENERIC = {"runtime", "model", "models", "assets", "asset", "live2d", "src", "dist",
            "upload", "uploads", "files", "static", "public"}


def _clean_stem(stem: str) -> str:
    """`female_01Arkit_6.model3` → `female_01Arkit_6`（去掉文件格式后缀）"""
    return re.sub(r"\.(model3|model|json)$", "", stem, flags=re.I).strip()


def _ascii_slug(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-]+", "-", text).strip("-").lower()


def _name_candidates(dir_path: Path, model_file: Path) -> List[str]:
    """按「越有信息量越靠前」给候选名字：模型目录名 → 文件名 → 上级目录名"""
    out = [dir_path.name, _clean_stem(model_file.stem), dir_path.parent.name]
    return [c for c in out if c]


def _slug(dir_path: Path, model_file: Path) -> str:
    """稳定的 id（**不随扫描顺序变**，否则记住的选择下次就失效）。

    优先用有信息量的 ASCII 名字；中文名（白-免费版 / Q版小熊貓）没法当 URL 的一部分
    就直接用路径哈希 —— 界面上显示的是 name，id 只要稳和唯一就够了。
    """
    for cand in _name_candidates(dir_path, model_file):
        if cand.lower() in _GENERIC:
            continue
        s = _ascii_slug(cand)
        if len(s) >= 2:
            return s[:40]
    return "m" + hashlib.sha1(str(dir_path).encode("utf-8")).hexdigest()[:8]


def _display_name(dir_path: Path, model_file: Path) -> str:
    for cand in _name_candidates(dir_path, model_file):
        if cand.lower() not in _GENERIC:
            return cand[:40]
    return _clean_stem(model_file.stem)[:40] or "未命名形象"


def _describe(model_file: Path) -> Dict[str, Any]:
    """读 model3.json，把「有没有动作/表情」这些信息一起带上。

    这一步很关键：**不同模型的动作名完全不一样**（hiyori 是 Idle/Tap，别人可能是 idle_01），
    前端要按模型自己的清单去挑，不能写死。顺便告诉前端「这个模型一个动作都没有」，
    那种情况就得靠程序驱动参数来演绎（白-免费版就是这种）。
    """
    info: Dict[str, Any] = {
        "file": str(model_file),
        "dir": str(model_file.parent),
        "name": _display_name(model_file.parent, model_file),
        "has_motions": False,
        "motion_groups": [],
        "expressions": [],
        "textures": 0,
        "size_mb": 0.0,
    }
    try:
        data = json.loads(model_file.read_text(encoding="utf-8"))
    except Exception:
        return info
    refs = data.get("FileReferences") or {}
    motions = refs.get("Motions") or {}
    if isinstance(motions, dict):
        groups = [str(k) for k, v in motions.items() if isinstance(v, list) and v]
        info["motion_groups"] = groups
        info["has_motions"] = bool(groups)
    exprs = refs.get("Expressions") or []
    if isinstance(exprs, list):
        info["expressions"] = [str(e.get("Name")) for e in exprs if isinstance(e, dict) and e.get("Name")]
    tex = refs.get("Textures") or []
    info["textures"] = len(tex) if isinstance(tex, list) else 0
    try:
        info["size_mb"] = round(
            sum(p.stat().st_size for p in model_file.parent.rglob("*") if p.is_file()) / 1048576, 1
        )
    except Exception:
        pass
    return info


# 已知形象的「人话名字」：目录名是 female_01Arkit_6 / my_teacher，界面上不该这么显示
_FRIENDLY = {
    "my_teacher": "艾琳（老师）",
    "1786973032__-___": "白（免费版）",     # 中文目录名拿不了当 id，用的是路径哈希兜底
}

# 「白-免费版」这个形象：仓库里的目录名是中文，没法当 id（见 _slug），
# 所以按**显示名**认它 —— 换机器/重扫之后目录名可能不一样，靠名字认最稳。
_DEFAULT_MARKERS = ("白-免费版", "白 - 免费版", "白免费版")


def _friendly(model_id: str, name: str) -> str:
    return _FRIENDLY.get(model_id, name)


def scan_models(force: bool = False) -> List[Dict[str, Any]]:
    """扫出所有可用形象（带缓存）。每一项：id / name / dir / url / 动作与表情信息。"""
    now = time.time()
    if not force and _cache["items"] and now - _cache["at"] < _SCAN_TTL:
        return _cache["items"]

    items: List[Dict[str, Any]] = []
    seen_dirs = set()
    roots = _roots()
    for root_index, root in enumerate(roots):
        base_depth = len(root.parts)
        try:
            candidates = [p for p in root.rglob("*.model3.json") if len(p.parts) - base_depth <= _MAX_DEPTH]
        except Exception:
            continue
        for model_file in sorted(candidates):
            try:
                key = str(model_file.parent.resolve()).lower()
            except Exception:
                continue
            if key in seen_dirs:
                continue
            seen_dirs.add(key)
            info = _describe(model_file)
            info["id"] = _slug(model_file.parent, model_file)
            info["root"] = str(root)
            info["root_index"] = root_index          # 排清单时用：越靠前的目录越优先
            info["url"] = f"/agent/live2d/model/{info['id']}/{model_file.name}"
            items.append(info)

    # id 撞车就加序号（保证唯一）；排序时**保留目录优先级**：
    # 「学生自己上传的」永远排最前（他刚传完就该在下拉里第一眼看到）。
    items.sort(key=lambda x: (x.get("root_index", 99), x["id"], x["dir"].lower()))
    used: Dict[str, int] = {}
    for it in items:
        base = it["id"]
        if base in used:
            used[base] += 1
            it["id"] = f"{base}-{used[base]}"
            it["url"] = f"/agent/live2d/model/{it['id']}/{Path(it['file']).name}"
        else:
            used[base] = 0
        it["label"] = _friendly(it["id"], it["name"])
        try:
            it["uploaded"] = str(upload_root().resolve()).lower() in str(it["dir"]).lower()
        except Exception:
            it["uploaded"] = False
    _cache.update({"at": now, "items": items})
    return items


def find_model(model_id: str) -> Optional[Dict[str, Any]]:
    for it in scan_models():
        if it["id"] == model_id:
            return it
    return None


def model_file(model_id: str, rel: str) -> Optional[Path]:
    """把 (形象 id, 相对路径) 解析成一个**确定在模型目录内**的真实文件。"""
    from .session import safe_relpath

    item = find_model(model_id)
    if not item:
        return None
    base = Path(item["dir"]).resolve()
    target = (base / safe_relpath(rel)).resolve()
    if base not in target.parents:
        return None
    return target if target.is_file() else None


def _state_path() -> Path:
    return data_root() / "pet_model.json"


def pick_default(items: List[Dict[str, Any]], bundled_dir: Optional[Path] = None) -> str:
    """从扫到的清单里挑「没选过形象时用哪个」——**纯函数**，好脱离磁盘直接测。

    顺序（对应 default_model_id 的注释）：
      ① 项目自带的（打包进 exe，最稳）→ ② 「白-免费版」→ ③ 目录名 my_teacher → ④ 第一个
    """
    if not items:
        return ""
    if bundled_dir is not None:
        try:
            bundled = Path(bundled_dir).resolve()
            for it in items:
                if bundled in Path(it["dir"]).resolve().parents:
                    return it["id"]
        except Exception:
            pass
    for it in items:
        if any(m in str(it.get("name") or "") for m in _DEFAULT_MARKERS):
            return it["id"]
    for it in items:
        if Path(it["dir"]).name.lower() == "my_teacher":
            return it["id"]
    return items[0]["id"]


def default_model_id() -> str:
    """默认形象：① 项目自带的 → ② 「白-免费版」→ ③ my_teacher → ④ 第一个。

    ② 是学生点名要的：白-免费版体积小（12.5MB）、没有动作文件，靠程序演绎参数就够了。
    它只在**从没选过形象**时起作用 —— 选过之后 `current_model_id()` 读 data/pet_model.json，
    默认值就不参与（所以老用户不会被悄悄换脸）。

    踩过的坑：一开始用「路径里含 my_teacher」当条件，结果**所有**模型都命中 ——
    因为项目根目录本身就叫 `D:\\My_teacher`。判断要落在最后一级目录上。
    """
    from .config import CLIENT_DIR

    return pick_default(scan_models(), CLIENT_DIR / "static" / "models")


def current_model_id() -> str:
    p = _state_path()
    if p.is_file():
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            mid = str((raw or {}).get("id") or "")
            if mid and find_model(mid):
                return mid
        except Exception:
            pass
    return default_model_id()


def set_current_model(model_id: str) -> Optional[Dict[str, Any]]:
    """记住学生选的形象。选不存在的 id 就**保持原样**（不要把一个坏 id 写进去）。"""
    item = find_model(model_id)
    if not item:
        return None
    try:
        p = _state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps({"id": model_id}, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass
    return item


def current_model() -> Optional[Dict[str, Any]]:
    mid = current_model_id()
    return find_model(mid) if mid else None


# ---------------------------------------------------------------------------
# 学生上传自己的 Live2D 模型
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = 300 * 1024 * 1024      # 整个模型目录的上限
MAX_FILE_BYTES = 64 * 1024 * 1024         # 单个文件的上限（moc3 大的能到 10MB 以上）


def save_upload(folder: str, files: List[tuple]) -> Dict[str, Any]:
    """把学生选的模型目录写进 `data/live2d_models/<folder>/`。

    `files` 是 [(相对路径, 字节)]。规则：
    - **每个相对路径都要清洗**（去掉盘符、`..`、绝对路径）——这是上传接口，必须防穿越；
    - 必须包含 `.model3.json`，否则这堆文件不是模型，直接拒掉（别写一堆垃圾进磁盘）；
    - 总量/单文件都有上限，超了报清楚。
    返回 {ok, folder, files, model} 或抛 ValueError（消息给学生看）。
    """
    from .session import safe_relpath

    name = re.sub(r"[^\w\u4e00-\u9fff.\- ]+", "_", str(folder or "").strip()) or "my_model"
    name = name.strip(". ")[:60] or "my_model"
    base = (upload_root() / name).resolve()
    if upload_root().resolve() not in base.parents:
        raise ValueError("目录名不合法")

    if not files:
        raise ValueError("没有收到文件")

    total = 0
    written: List[str] = []
    model_json = None
    for rel, data in files:
        rel = safe_relpath(str(rel).replace("\\", "/"))
        if len(data) > MAX_FILE_BYTES:
            raise ValueError(f"单个文件太大：{rel}（{len(data)/1048576:.1f} MB）")
        total += len(data)
        if total > MAX_UPLOAD_BYTES:
            raise ValueError(f"整个模型太大（超过 {MAX_UPLOAD_BYTES // 1048576} MB）")
        target = (base / rel).resolve()
        if base not in target.parents:
            raise ValueError(f"路径不合法：{rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        written.append(rel)
        if rel.lower().endswith(".model3.json"):
            model_json = target

    if model_json is None:
        raise ValueError("这些文件里没有 .model3.json —— 请选**整个模型文件夹**（里面要有 .model3.json、.moc3 和贴图）")
    if not model_json.is_file():
        raise ValueError("模型主文件没写成功")

    scan_models(force=True)
    item = None
    for it in scan_models():
        try:
            if Path(it["dir"]).resolve() == base or Path(it["file"]).resolve() == model_json.resolve():
                item = it
                break
        except Exception:
            continue
    return {"ok": True, "folder": name, "dir": str(base), "files": len(written), "model": item}


def delete_upload(model_id: str) -> bool:
    """删掉一个**上传的**形象（只允许删 data/live2d_models 里的，用户自己的模型一律不动）"""
    import shutil

    item = find_model(model_id)
    if not item:
        return False
    target = Path(item["dir"]).resolve()
    root = upload_root().resolve()
    if root not in target.parents:
        return False                                  # 不是上传目录里的 → 拒绝删
    shutil.rmtree(target, ignore_errors=True)
    scan_models(force=True)
    return True


def catalogue() -> Dict[str, Any]:
    """给界面用的完整清单：当前选了谁 + 有哪些可选。"""
    items = scan_models()
    cur = current_model()
    return {
        "current": cur,
        "models": [
            {
                "id": it["id"],
                "name": it["name"],
                "label": it.get("label") or it["name"],
                "url": it["url"],
                "has_motions": it["has_motions"],
                "motion_groups": it["motion_groups"],
                "expressions": it["expressions"],
                "size_mb": it["size_mb"],
                "dir": it["dir"],
                "uploaded": str(upload_root().resolve()).lower() in it["dir"].lower(),
            }
            for it in items
        ],
    }
