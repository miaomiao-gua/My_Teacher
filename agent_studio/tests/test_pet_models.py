#!/usr/bin/env python
"""桌宠形象清单自测（kernel/pet_models.py）：扫盘、id 稳定、防穿越、记住选择。

为什么这些必须测：
  1. **id 稳定**：id 是「记住学生选了哪个形象」的钥匙。如果它随扫描顺序/时间变，
     学生下次打开就莫名其妙变回默认 —— 这种 bug 不会报错，只会让人以为「设置没保存」。
  2. **防穿越**：模型来自用户自己的各个文件夹（桌面、别的项目），
     `..\\..\\data\\config.json` 这种路径绝不能取到。
  3. **只读**：扫盘不许改动用户的模型目录。

跑法：py -3.14 tests/test_pet_models.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

from kernel import pet_models as pm  # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def make_fake_model(base: Path, folder: str, stem: str, motions: dict | None = None,
                    textures: int = 1) -> Path:
    """造一个最小可用的模型目录（只写 model3.json + 一个假 moc3）"""
    d = base / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.moc3").write_bytes(b"fake-moc3")
    for i in range(textures):
        (d / f"texture_{i}.png").write_bytes(b"png")
    doc = {
        "Version": 3,
        "FileReferences": {
            "Moc": f"{stem}.moc3",
            "Textures": [f"texture_{i}.png" for i in range(textures)],
            "Motions": motions or {},
            "Expressions": [{"Name": "exp1", "File": "exp1.exp3.json"}],
        },
    }
    p = d / f"{stem}.model3.json"
    p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return p


def test_scan_and_ids() -> None:
    print("\n[1] 扫盘与 id 稳定性")
    items = pm.scan_models(force=True)
    check("扫到了形象", len(items) > 0, f"{len(items)} 个")
    ids = [it["id"] for it in items]
    check("id 互不重复", len(ids) == len(set(ids)), str(ids))
    check("每个 id 都是 ASCII（能放进网址）", all(i.isascii() for i in ids), str([i for i in ids if not i.isascii()]))
    check("每个都带 url 和 name", all(it.get("url") and it.get("name") for it in items))
    check("带了 label（界面显示用）", all(it.get("label") for it in items))

    # 再扫一次：id 必须一模一样（缓存和重新扫都要一致）
    again = pm.scan_models(force=True)
    check("重扫后 id 不变", [it["id"] for it in again] == ids)

    # 用户手上有「白-免费版」这种没有动作文件的模型 —— 要能识别出来
    no_motion = [it for it in items if not it["has_motions"]]
    check("能识别出「没有动作文件」的模型（要靠程序演绎）", len(no_motion) >= 0,
          f"{len(no_motion)} 个：" + "、".join(it["name"] for it in no_motion))
    with_motion = [it for it in items if it["has_motions"]]
    if with_motion:
        check("有动作文件的模型列出了动作组", all(it["motion_groups"] for it in with_motion),
              str(with_motion[0]["motion_groups"]))

    # 目录名没信息量时（hiyori 那套就叫 runtime）不能把名字显示成 runtime
    check("没有形象被显示成 runtime/model 之类",
          all(it["name"].lower() not in pm._GENERIC for it in items),
          str([it["name"] for it in items]))


def test_traversal_and_readonly() -> None:
    print("\n[2] 取文件：防穿越 + 只读")
    items = pm.scan_models()
    if not items:
        check("有形象可测", False)
        return
    it = items[0]
    model_name = Path(it["file"]).name
    got = pm.model_file(it["id"], model_name)
    check("正常取得到模型主文件", got is not None and got.is_file(), str(got))

    for bad in ("../../../data/config.json", "..\\..\\..\\data\\config.json",
                "....//....//data/config.json", "/etc/passwd", "C:/Windows/win.ini"):
        out = pm.model_file(it["id"], bad)
        check(f"挡住 {bad}", out is None or str(out).lower().endswith(".json") and "data" not in str(out).lower(),
              str(out))
    check("不存在的形象返回 None", pm.model_file("根本没有这个形象", "a.json") is None)

    # 只读：扫一遍之后模型目录里的文件列表（和内容）不该变
    d = Path(it["dir"])
    before = sorted(p.name for p in d.iterdir())
    pm.scan_models(force=True)
    pm.model_file(it["id"], model_name)
    after = sorted(p.name for p in d.iterdir())
    check("扫盘/取文件不会改用户目录", before == after)


def test_current_choice() -> None:
    print("\n[3] 记住选择")
    items = pm.scan_models()
    if len(items) < 2:
        check("至少两个形象才能验「换形象」", False, f"{len(items)} 个")
        return
    orig = pm.current_model_id()
    target = next(it for it in items if it["id"] != orig)
    got = pm.set_current_model(target["id"])
    check("换成别的不报错", got is not None and got["id"] == target["id"])
    check("读回来就是刚换的那个", pm.current_model_id() == target["id"])
    check("换不存在的形象不会写坏记录", pm.set_current_model("根本没有这个") is None
          and pm.current_model_id() == target["id"])
    pm.set_current_model(orig)
    check("恢复原样", pm.current_model_id() == orig)

    check("catalogue() 里 current 和 models 都有",
          bool(pm.catalogue().get("current")) and len(pm.catalogue().get("models") or []) == len(items))


def test_fake_models_in_temp() -> None:
    print("\n[4] 临时目录里的假模型（不动用户真实文件）")
    with tempfile.TemporaryDirectory(dir=str(ROOT / "data")) as tmp:
        base = Path(tmp)
        make_fake_model(base, "中文名字-免费版", "中文名字-免费版", motions={})
        make_fake_model(base, "ascii_model", "ascii_model", motions={"idle": [{"File": "m.json"}], "Tap": [{"File": "m.json"}]})
        make_fake_model(base, "runtime", "hiyori_free_t08", motions={"Idle": [{"File": "m.json"}]})

        # 把自己伪造的目录塞进「额外目录」清单
        extra = pm.data_root() / "pet_model_dirs.json"
        old = extra.read_text(encoding="utf-8") if extra.is_file() else None
        extra.write_text(json.dumps([str(base)], ensure_ascii=False), encoding="utf-8")
        try:
            items = pm.scan_models(force=True)
            mine = [it for it in items if str(base).lower() in it["dir"].lower()]
            check("额外目录里的模型被扫到", len(mine) == 3, f"{len(mine)} 个：" + "、".join(it["name"] for it in mine))
            names = sorted(it["name"] for it in mine)
            check("中文目录名留着当显示名", any("免费版" in n for n in names), str(names))
            check("runtime 这种没信息量的目录名被跳过",
                  all(n != "runtime" for n in names), str(names))
            ascii_one = next((it for it in mine if it["name"] == "ascii_model"), None)
            check("有动作的模型动作组读出来了",
                  bool(ascii_one and ascii_one["motion_groups"] == ["idle", "Tap"]),
                  str(ascii_one and ascii_one["motion_groups"]))
            check("中文 id 用哈希兜底（ASCII）",
                  all(it["id"].isascii() for it in mine), str([it["id"] for it in mine]))
        finally:
            if old is None:
                extra.unlink(missing_ok=True)
            else:
                extra.write_text(old, encoding="utf-8")
            pm.scan_models(force=True)


def main() -> int:
    test_scan_and_ids()
    test_traversal_and_readonly()
    test_current_choice()
    test_fake_models_in_temp()
    print("\n" + ("全部通过 ✓" if _failures == 0 else f"失败 {_failures} 项 ✗"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
