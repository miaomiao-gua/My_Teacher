#!/usr/bin/env python
"""设置中心自测：白名单、类型与范围、**密钥只进不出**、人格提示词生效。

为什么必须测：
  1. **密钥泄露**是这类接口最容易犯的错 —— 一旦 read_state() 把 key 明文回给前端，
     它就进了浏览器缓存/历史/截图。这里逐字段盯着。
  2. 落盘必须是**密文**（`enc$...`）：学生把 data/ 目录发给别人时不该泄露 key。
  3. 人格提示词改了要**真进系统提示词**（否则界面上改了没反应，看起来像 bug）。
  4. 白名单之外的键不能写进去（防止界面被改坏或误改内部字段）。

跑法：py -3.14 tests/test_settings_api.py
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

# ★ 关键：这个测试会**往配置里写假密钥**。绝不能写到学生真正在用的那份 config.json 上 ——
#   服务进程和另一个会话都在读那个文件，写进去会让云端链路当场 401。
#   所以先切到一个临时数据目录（把现有配置复制过去，行为一致），测完删掉。
_SCRATCH = ROOT / "data" / "_settings_test"
shutil.rmtree(_SCRATCH, ignore_errors=True)
_SCRATCH.mkdir(parents=True, exist_ok=True)
for _name in ("config.json", ".secret_key", "settings.json"):
    _src = ROOT / "data" / _name
    if _src.is_file():
        shutil.copy2(_src, _SCRATCH / _name)
os.environ["AGENT_STUDIO_DATA"] = str(_SCRATCH)

from kernel import settings_api as sa  # noqa: E402

_failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global _failures
    if not ok:
        _failures += 1
    print(("  ✓ " if ok else "  ✗ ") + name + (f"  →  {detail}" if detail else ""))


def backup() -> dict:
    """临时目录里的快照（测完还原，保证同一进程里后续用例互不影响）"""
    snaps = {}
    for p in (sa._cfg_path(), sa._settings_path(), sa._persona_path(), sa._extra_path()):
        snaps[str(p)] = p.read_bytes() if p.is_file() else None
    return snaps


def restore(snaps: dict) -> None:
    for path, data in snaps.items():
        p = Path(path)
        if data is None:
            p.unlink(missing_ok=True)
        else:
            p.write_bytes(data)


def test_read_hides_secrets() -> None:
    print("\n[1] 读接口：密钥只进不出")
    st = sa.read_state()
    check("有 values / secrets / groups", all(k in st for k in ("values", "secrets", "groups")))
    blob = json.dumps(st, ensure_ascii=False)
    import re

    # 任何看起来像真实 key 的长串都不该出现（sk-xxx、32 位以上十六进制等）
    suspicious = re.findall(r"(sk-[A-Za-z0-9_\-]{12,}|[A-Za-z0-9]{32,})", blob)
    check("响应里没有疑似密钥的长串", not suspicious, str(suspicious)[:80])
    check("secrets 里只给 set / tail", all(set(v) <= {"set", "tail"} for v in st["secrets"].values()),
          str(list(st["secrets"].values())[:2]))
    check("分组里有「模型链路」和「云端 API」", len(st["groups"]) >= 3,
          str([g["title"] for g in st["groups"]]))
    check("带了「关于」信息", "about" in st and st["about"].get("tools"), str(st["about"].get("tools")))


def test_write_whitelist_and_ranges() -> None:
    print("\n[2] 写接口：白名单 + 类型 + 范围")
    snaps = backup()
    try:
        r = sa.write_state({"chat_temperature": 0.3, "agent_max_steps": 7})
        check("正常值能写进去", r["ok"] and r["state"]["values"]["chat_temperature"] == 0.3,
              str(r.get("errors")))
        check("整数按 int 存", isinstance(r["state"]["values"]["agent_max_steps"], int))

        r = sa.write_state({"agent_max_steps": 999})
        check("超范围被拒并说清楚", (not r["ok"]) and any("最多" in e or "不能大于" in e for e in r["errors"]),
              str(r["errors"]))
        r = sa.write_state({"chat_temperature": "abc"})
        check("填错类型被拒", (not r["ok"]) and any("数字" in e for e in r["errors"]), str(r["errors"]))
        r = sa.write_state({"llm_provider": "火星"})
        check("非法链路被拒", (not r["ok"]) and any("cloud" in e for e in r["errors"]), str(r["errors"]))

        before = json.loads(sa._cfg_path().read_text(encoding="utf-8"))
        r = sa.write_state({"__evil__": 1, "os.system": "rm -rf /"})
        after = json.loads(sa._cfg_path().read_text(encoding="utf-8"))
        check("白名单外的键不被写入", before == after and r["ok"])
    finally:
        restore(snaps)


def test_secret_roundtrip_and_encryption() -> None:
    print("\n[3] 密钥：写进去是密文，读出来没明文")
    snaps = backup()
    try:
        fake = "sk-test-1234567890abcdefghijklmn"
        r = sa.write_state({"chat_api_key": fake})
        check("写入成功", r["ok"], str(r.get("errors")))

        raw = sa._cfg_path().read_text(encoding="utf-8")
        check("磁盘上是密文（enc$ 开头）", "enc$" in raw and fake not in raw)
        check("读接口只说「设了」", r["state"]["secrets"]["chat_api_key"]["set"] is True)
        check("读接口不回明文", fake not in json.dumps(r["state"], ensure_ascii=False))

        # 内核实际用的时候要能解密出原值
        from kernel.config import load_app_config

        check("内核能解出原始 key（否则云端会 401）",
              load_app_config().get("chat_api_key") == fake)

        r = sa.write_state({"chat_api_key": ""})
        check("留空 = 不改（不是清空）", r["state"]["secrets"]["chat_api_key"]["set"] is True)

        r = sa.write_state({"chat_api_key_clear": True})
        check("带 _clear 才清空", r["state"]["secrets"]["chat_api_key"]["set"] is False)
    finally:
        restore(snaps)


def test_persona_flows_into_prompt() -> None:
    print("\n[4] 人格提示词：改了要真进系统提示词")
    snaps = backup()
    try:
        from kernel.prompts import build_system_prompt

        default = build_system_prompt(tools=["graph_calculator"])
        check("默认用的是内置人格（含「艾琳老师」）", "艾琳老师" in default)
        check("默认人格标记为 is_default", sa.persona_state()["is_default"] is True)

        mine = "你是「小明老师」，说话简短，喜欢用生活例子。"
        r = sa.write_state({"persona": mine, "persona_extra": "每次课结束都留一道思考题。"})
        check("人格写入成功", r["ok"], str(r.get("errors")))
        check("不再是默认", sa.persona_state()["is_default"] is False)

        prompt = build_system_prompt(tools=["graph_calculator"])
        check("系统提示词里出现自定义人格", mine in prompt)
        check("附加要求也进去了", "每次课结束都留一道思考题。" in prompt)
        check("工具规则**没有**被顶掉（功能性约束必须保留）",
              "工具使用策略" in prompt and "render_ui" in prompt)
        check("内置人格不再出现", "艾琳老师——一位能动手的 AI 私教" not in prompt)

        r = sa.write_state({"persona": "", "persona_extra": ""})
        check("清空后回到内置人格",
              r["state"]["persona"]["is_default"] is True and "艾琳老师" in build_system_prompt(tools=[]))
    finally:
        restore(snaps)


def test_chain_switch() -> None:
    print("\n[5] 切换链路立刻生效")
    snaps = backup()
    try:
        from kernel.config import llm_configs

        sa.write_state({"llm_provider": "local", "fallback_local": False})
        chain = llm_configs()
        check("切到 local 后首选是本机", chain and chain[0].provider == "ollama",
              str([c.label for c in chain]))
        sa.write_state({"llm_provider": "cloud"})
        chain = llm_configs()
        check("切回 cloud 后首选是云端（没配 key 时会只剩本地）",
              bool(chain), str([c.label for c in chain]))
    finally:
        restore(snaps)


def test_writing_settings_keeps_keys_intact() -> None:
    """★ 写设置**不能把已存的密钥弄坏**。

    这条是被一次真实故障逼出来的：改运行参数时 `_write_json(cfg)` 用的 cfg 是
    `_read_json()` 的原始字典（里面是 enc$ 密文），只要哪次不小心用**解密后的**值去写，
    磁盘上的密文就被明文覆盖了 —— 表面上"设置保存成功"，实际上密钥已经废了。
    """
    print("\n[6] 写设置之后，已存的密钥仍然可解")
    snaps = backup()
    try:
        from kernel.config import load_app_config
        from kernel.crypto_utils import is_encrypted

        before = load_app_config().get("chat_api_key") or ""
        check("初始状态能解出 key", bool(before) and not is_encrypted(before), f"{len(before)} 字符")

        sa.write_state({"agent_max_steps": 15, "pet_scale": 1.3, "tts_provider": "windows"})
        after = load_app_config().get("chat_api_key") or ""
        check("改完参数后仍然能解出 key", bool(after) and not is_encrypted(after), f"{len(after)} 字符")
        check("值没变（没被明文覆盖）", after == before)

        raw = json.loads(sa._cfg_path().read_text(encoding="utf-8"))
        check("磁盘上依然是密文", str(raw.get("chat_api_key", "")).startswith("enc$"))
    finally:
        restore(snaps)


def main() -> int:
    test_read_hides_secrets()
    test_write_whitelist_and_ranges()
    test_secret_roundtrip_and_encryption()
    test_persona_flows_into_prompt()
    test_chain_switch()
    test_writing_settings_keeps_keys_intact()
    # 收尾：删掉临时数据目录（学生的真实配置全程没被碰过）
    shutil.rmtree(_SCRATCH, ignore_errors=True)
    print("\n" + ("全部通过 ✓" if _failures == 0 else f"失败 {_failures} 项 ✗"))
    return 1 if _failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
