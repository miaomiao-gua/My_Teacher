#!/usr/bin/env python
"""上传自己的 Live2D 模型 + 语音（TTS）自测。

为什么必须测：
  1. 上传接口是**往磁盘写文件**的入口 —— 路径穿越（`../../`）、超大文件、写了一堆
     根本不是模型的文件，都必须挡住。这是安全边界，不是功能细节。
  2. 「只许删上传的」：学生自己电脑上的模型目录**一个字都不能动**。
  3. TTS 要真的产出音频（Windows 本机嗓子），provider=off 时要给出人话提示而不是崩。

跑法：py -3.14 tests/test_pet_upload_tts.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "course"))

# 用临时数据目录，绝不碰学生真实的 data/（上传测试会往里写文件）
_SCRATCH = ROOT / "data" / "_upload_test"
shutil.rmtree(_SCRATCH, ignore_errors=True)
_SCRATCH.mkdir(parents=True, exist_ok=True)
for _n in (".secret_key", "settings.json"):
    _src = ROOT / "data" / _n
    if _src.is_file():
        shutil.copy2(_src, _SCRATCH / _n)
# 配置**去掉密文字段**再放进临时目录：这几个用例不需要真密钥，
# 而带着复制的密文跑会在日志里刷一排「密钥解密失败」，把真正的问题淹掉。
try:
    _cfg_src = ROOT / "data" / "config.json"
    if _cfg_src.is_file():
        _cfg = json.loads(_cfg_src.read_text(encoding="utf-8"))
        _cfg = {k: v for k, v in _cfg.items()
                if not (isinstance(v, str) and v.startswith("enc$"))}
        (_SCRATCH / "config.json").write_text(json.dumps(_cfg, ensure_ascii=False, indent=2), encoding="utf-8")
except Exception:
    pass
os.environ["AGENT_STUDIO_DATA"] = str(_SCRATCH)

from kernel import pet_models as pm  # noqa: E402
from kernel import tts  # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def fake_model_files(stem: str = "mymodel") -> list:
    """造一堆"像模型"的文件：.model3.json + .moc3 + 贴图 + 物理"""
    model3 = {
        "Version": 3,
        "FileReferences": {
            "Moc": f"{stem}.moc3",
            "Textures": [f"{stem}.2048/texture_00.png"],
            "Physics": f"{stem}.physics3.json",
            "Motions": {"idle": [{"File": "motions/idle_01.motion3.json"}]},
        },
    }
    return [
        (f"{stem}.model3.json", json.dumps(model3, ensure_ascii=False).encode()),
        (f"{stem}.moc3", b"moc3" * 500),
        (f"{stem}.2048/texture_00.png", b"\x89PNG" + b"x" * 2000),
        (f"{stem}.physics3.json", b"{}"),
        ("motions/idle_01.motion3.json", b"{}"),
    ]


def test_upload_ok() -> None:
    print("\n[1] 正常上传：整目录写进去 → 立刻能被扫到、能取文件")
    r = pm.save_upload("测试模型A", fake_model_files("mymodel"))
    check("上传成功并返回模型信息", r.get("ok") and r.get("model"), str(r.get("folder")))
    item = r["model"]
    check("扫出来的是它", item and item["name"], str(item and item["name"]))
    check("读到了动作组", item and item["motion_groups"] == ["idle"], str(item and item["motion_groups"]))
    check("标成「上传的」", pm.catalogue()["models"] and any(
        m["uploaded"] and m["id"] == item["id"] for m in pm.catalogue()["models"]))
    got = pm.model_file(item["id"], "mymodel.moc3")
    check("能取到 moc3（换形象时要用）", got is not None and got.is_file(), str(got))
    check("它排在清单最前面（上传的优先）", pm.scan_models()[0]["id"] == item["id"],
          str([m["id"] for m in pm.scan_models()[:3]]))


def test_upload_rejects() -> None:
    print("\n[2] 该拒的必须拒")
    try:
        pm.save_upload("x", [])
        check("空上传被拒", False)
    except ValueError as exc:
        check("空上传被拒", "没有收到文件" in str(exc), str(exc))

    try:
        pm.save_upload("x", [("readme.txt", b"hello")])
        check("没有 .model3.json 的被拒（别往磁盘写垃圾）", False)
    except ValueError as exc:
        check("没有 .model3.json 的被拒（别往磁盘写垃圾）", "model3.json" in str(exc), str(exc))

    # 路径穿越：文件名会被清洗（`..` 被丢掉），关键是**绝不能落到 upload_root 外面**
    r = pm.save_upload("穿越测试", [("../../evil.txt", b"x"), ("m.model3.json", b"{}")])
    outside = ROOT / "data" / "evil.txt"
    outside2 = REPO_EVIL = (ROOT.parent / "evil.txt")
    check("`../../` 没有写到上传目录外面",
          not outside.is_file() and not outside2.is_file(),
          f"{outside.is_file()} / {outside2.is_file()}")
    landed = list((pm.upload_root() / "穿越测试").rglob("evil.txt"))
    check("清洗后的文件确实落在上传目录内（没有写到别处）",
          all(str(pm.upload_root().resolve()).lower() in str(p).lower() for p in landed),
          str(landed))
    check("清洗后仍然完成了上传", bool(r.get("ok")))

    big = [("m.model3.json", b"{}"), ("big.bin", b"0" * (pm.MAX_FILE_BYTES + 1))]
    try:
        pm.save_upload("超大", big)
        check("单文件超限被拒", False)
    except ValueError as exc:
        check("单文件超限被拒", "太大" in str(exc), str(exc))


def test_delete_only_uploads() -> None:
    print("\n[3] 删除：只许删上传的，用户自己的模型一个都不许动")
    uploaded = pm.save_upload("待删除", fake_model_files("todel"))["model"]
    check("删上传的可以", pm.delete_upload(uploaded["id"]) is True)
    check("删完就不在清单里了", all(m["id"] != uploaded["id"] for m in pm.scan_models()))

    # 找一个**不是上传的**模型（用户自己的，比如白-免费版）
    others = [m for m in pm.scan_models() if not m["uploaded"]]
    if others:
        target = others[0]
        before = Path(target["dir"]).is_dir()
        check(f"删「{target['name']}」（不是上传的）会被拒", pm.delete_upload(target["id"]) is False)
        check("而且目录完好无损", before and Path(target["dir"]).is_dir())
    else:
        print("  ·  这台机器上没有「非上传」的模型可测，跳过")


def test_tts() -> None:
    print("\n[4] 语音")
    voices = tts.voices()
    check("能列出系统嗓子", len(voices["windows"]) >= 1,
          str([v["name"] for v in voices["windows"]][:3]))
    check("给了云端音色候选", len(voices["cloud"]) >= 4)

    cfg = {"provider": "windows", "windows_voice": 0, "speed": 1.0, "volume": 100}
    data = tts._windows_wav("测试一下声音。", cfg)
    check("本机语音真产出 wav（离线不用 key）", data[:4] == b"RIFF" and len(data) > 1000, f"{len(data)} 字节")

    # provider=off 时要说人话
    import asyncio

    from kernel import settings_api as sa

    snaps = sa._cfg_path().read_bytes()
    try:
        sa.write_state({"tts_provider": "off"})
        try:
            asyncio.run(tts.synthesize("你好"))
            check("关掉语音时给的是人话提示（不是崩）", False, "居然没报错")
        except RuntimeError as exc:
            check("关掉语音时给的是人话提示（不是崩）", "语音没开" in str(exc), str(exc))
        sa.write_state({"tts_provider": "windows"})
        info = tts.self_check()
        check("self_check 说本机语音就绪", info["ready"] and info["provider"] == "windows", str(info))
        data2, mime = asyncio.run(tts.synthesize("你好呀"))
        check("synthesize 走通了（返回 wav）", mime == "audio/wav" and data2[:4] == b"RIFF", mime)
    finally:
        sa._cfg_path().write_bytes(snaps)


def main() -> int:
    test_upload_ok()
    test_upload_rejects()
    test_delete_only_uploads()
    test_tts()
    shutil.rmtree(_SCRATCH, ignore_errors=True)
    print("\n" + ("全部通过 ✓" if _failures == 0 else f"失败 {_failures} 项 ✗"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
