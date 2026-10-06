"""课程存储 —— 备课结果落盘、课程列表、学习进度。

跟老项目同一套目录约定（这样两边的课程可以互相搬）：

    data/lessons/<课程目录>/
        config.json          课程元信息（主题、创建时间、单元数、owner）
        syllabus.json        备课生成的完整教案（大纲 / 单元 / 知识点 / 公式 / 测验）
        progress.json        学习进度（当前单元、已完成单元、测验成绩）
        knowledge_graph.json 知识图谱（由 lesson_prep.generate_knowledge_graph 生成）
        section_N/unit.md    第 N 课的教学资料正文（给「按需取资料」用）
        learners/            学生画像（由 learner_model 管理，位于 lessons/ 下）

为什么要把单元要点也写成 `section_N/unit.md`：
新项目是独立项目，**可能没有教材 PDF**。老项目的 `load_unit_context` 读的就是
`section_N/*.md`，所以备课时顺手把这一课的资料正文生成出来，
「讲第 3 课时按需取资料」这条路在新项目里同样走得通，而不是非要依赖外部教材。
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# 让同目录的扁平模块（learner_model / knowledge_graph …）可以被导入
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

DATA_DIR = _HERE.parent / "data"
LESSONS_DIR = DATA_DIR / "lessons"

import knowledge_graph as kg  # noqa: E402


def _write_json(path: Path, data: Any) -> None:
    """原子写：先写临时文件再替换，避免断电/中断留下半个 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def build_folder_name(topic: str) -> str:
    """课程目录名：时间戳 + 主题（中文原样保留，只清掉路径非法字符）。"""
    safe = re.sub(r"[^\w\u4e00-\u9fa5]+", "_", (topic or "course").strip())[:40].strip("_")
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{safe or 'course'}"


def unit_markdown(unit: Dict[str, Any], index: int) -> str:
    """把一课的教案内容渲染成 Markdown 资料正文。"""
    title = unit.get("title") or f"第 {index} 课"
    lines: List[str] = [f"# {title}", ""]
    if unit.get("summary"):
        lines += [f"**本课要点**：{unit['summary']}", ""]
    if unit.get("target"):
        lines += [f"**能力目标**：{unit['target']}", ""]

    key_points = unit.get("key_points") or []
    if key_points:
        lines.append("## 核心知识点")
        lines += [f"- {kp}" for kp in key_points]
        lines.append("")

    formulas = unit.get("core_formulas") or []
    if formulas:
        lines.append("## 核心公式 / 概念")
        for f in formulas:
            if isinstance(f, dict):
                name = f.get("name") or ""
                formula = f.get("formula") or ""
                variables = f.get("variables") or ""
                lines.append(f"- **{name}**：`{formula}`" + (f"（{variables}）" if variables else ""))
            else:
                lines.append(f"- {f}")
        lines.append("")

    modules = unit.get("modules") or []
    if modules:
        lines.append("## 讲解模块")
        for i, m in enumerate(modules, 1):
            if not isinstance(m, dict):
                continue
            lines.append(f"### {i}. {m.get('title') or ''}")
            for key, label in (("concept", "概念"), ("example", "例子"),
                              ("anchor", "记忆锚点"), ("interaction", "互动")):
                if m.get(key):
                    lines.append(f"- {label}：{m[key]}")
            lines.append("")

    contrasts = unit.get("contrasts") or []
    if contrasts:
        lines.append("## 易混淆概念")
        for c in contrasts:
            if isinstance(c, dict):
                lines.append(f"- **{c.get('a')}** vs **{c.get('b')}**：{c.get('difference')}")
        lines.append("")

    gateways = unit.get("gateway_questions") or []
    if gateways:
        lines.append("## 本课通关问题（课堂上用来检验理解）")
        lines += [f"{i}. {q}" for i, q in enumerate(gateways, 1)]
        lines.append("")

    prereq = unit.get("prerequisites") or []
    if prereq:
        lines += ["## 前置知识", *[f"- {p}" for p in prereq], ""]

    contribution = unit.get("contribution_to_target")
    if contribution:
        lines += ["## 本课对课程目标的贡献", contribution, ""]

    return "\n".join(lines).strip() + "\n"


def create_course(plan: Dict[str, Any], owner: Optional[str] = None) -> Dict[str, Any]:
    """把备课结果落盘成一门课程。返回 {folder, path, unit_count, units}。"""
    topic = str(plan.get("topic") or "").strip() or "未命名课程"
    folder = build_folder_name(topic)
    root = LESSONS_DIR / folder
    root.mkdir(parents=True, exist_ok=True)

    units = [u for u in (plan.get("units") or []) if isinstance(u, dict)]
    for i, unit in enumerate(units, 1):
        section = root / f"section_{i}"
        section.mkdir(parents=True, exist_ok=True)
        (section / "unit.md").write_text(unit_markdown(unit, i), encoding="utf-8")

    _write_json(root / "syllabus.json", plan)
    _write_json(
        root / "config.json",
        {
            "topic": topic,
            "owner": owner,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "unit_count": len(units),
            "course_target": plan.get("course_target") or "",
            "acceptance_criteria": plan.get("acceptance_criteria") or "",
            "source": "agent_studio",
        },
    )
    _write_json(
        root / "progress.json",
        {"current_unit": 1, "completed_units": [], "quiz_scores": [], "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
    )

    # 知识图谱：失败不影响课程创建（课程本身已经能用了）
    graph_ok = False
    try:
        from lesson_prep import generate_knowledge_graph

        graph = generate_knowledge_graph(plan)
        kg.save_knowledge_graph(folder, graph or {})
        graph_ok = bool(graph)
    except Exception as exc:  # noqa: BLE001
        print(f"[course] 知识图谱生成失败（不影响课程使用）：{exc}", flush=True)

    return {
        "folder": folder,
        "path": str(root),
        "topic": topic,
        "unit_count": len(units),
        "graph_ok": graph_ok,
        "units": [
            {"index": i, "title": u.get("title") or f"第 {i} 课",
             "summary": u.get("summary") or "", "key_points": (u.get("key_points") or [])[:4]}
            for i, u in enumerate(units, 1)
        ],
    }


def list_courses() -> List[Dict[str, Any]]:
    """列出本项目里的课程（按创建时间倒序）。"""
    if not LESSONS_DIR.exists():
        return []
    out: List[Dict[str, Any]] = []
    for child in LESSONS_DIR.iterdir():
        if not child.is_dir() or child.name == "learners":
            continue
        cfg = _read_json(child / "config.json") or {}
        if not cfg and not (child / "syllabus.json").exists():
            continue
        out.append(
            {
                "folder": child.name,
                "topic": cfg.get("topic") or child.name,
                "unit_count": cfg.get("unit_count") or 0,
                "created_at": cfg.get("created_at") or "",
                "owner": cfg.get("owner"),
                "has_graph": (child / "knowledge_graph.json").exists(),
            }
        )
    out.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return out


def load_course(folder: str) -> Dict[str, Any]:
    """读一门课程的 config / syllabus / progress。"""
    root = LESSONS_DIR / folder
    return {
        "folder": folder,
        "exists": root.exists(),
        "config": _read_json(root / "config.json") or {},
        "syllabus": _read_json(root / "syllabus.json") or {},
        "progress": _read_json(root / "progress.json") or {},
    }


def set_progress(folder: str, unit_index: Optional[int] = None, completed: bool = False,
                 quiz_score: Optional[float] = None) -> Dict[str, Any]:
    """更新学习进度：当前单元 / 标记完成 / 记一次测验成绩。"""
    root = LESSONS_DIR / folder
    if not root.exists():
        raise FileNotFoundError(f"课程不存在：{folder}")
    progress = _read_json(root / "progress.json") or {
        "current_unit": 1, "completed_units": [], "quiz_scores": []
    }
    if unit_index is not None:
        progress["current_unit"] = int(unit_index)
    if completed and unit_index is not None:
        done = set(progress.get("completed_units") or [])
        done.add(int(unit_index))
        progress["completed_units"] = sorted(done)
        progress["current_unit"] = max(int(unit_index) + 1, progress.get("current_unit") or 1)
    if quiz_score is not None:
        scores = progress.get("quiz_scores") or []
        scores.append({"unit": unit_index, "score": float(quiz_score),
                       "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        progress["quiz_scores"] = scores[-50:]
    progress["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _write_json(root / "progress.json", progress)
    return progress


def next_unit_index(folder: str) -> int:
    """下一课应该讲第几单元。"""
    progress = load_course(folder).get("progress") or {}
    current = int(progress.get("current_unit") or 1)
    total = int((load_course(folder).get("config") or {}).get("unit_count") or 0)
    if total:
        current = min(current, total)
    return max(1, current)
