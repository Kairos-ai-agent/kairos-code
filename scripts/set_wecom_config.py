#!/usr/bin/env python
"""交互式填写企业微信「自建应用」的 5 个配置值。

用法（在仓库目录下）：
    .venv/Scripts/python.exe scripts/set_wecom_config.py
    .venv/Scripts/python.exe scripts/set_wecom_config.py --data-dir "C:/Users/你/AppData/Local/kairos-code/data"

特点：
  * 输入是**隐藏的**（不回显、不进命令历史）
  * 只打印长度和位置，**绝不打印值本身**
  * 直接写进 Kairos 的 settings.json（走正规的 SettingsStore，不会写坏格式）
  * 已存在的值：直接回车 = 保持不变

5 个值在企微后台哪里拿（https://work.weixin.qq.com/）：
  corp_id          我的企业 → 企业信息 → 最下面「企业 ID」
  corp_secret      应用管理 → 自建 → 你的应用 → Secret（点「查看」）
  agent_id         同一个应用详情页 → AgentId
  token            应用 → 接收消息 → 设置 API 接收 → 「随机获取」
  encoding_aes_key 同上，43 个字符
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _read_hidden(prompt: str) -> str:
    """优先隐藏输入；没有终端时（管道/重定向）退回可见输入，避免死等。"""
    if sys.stdin.isatty():
        return getpass.getpass(prompt)
    print(prompt, end="", flush=True)
    return input()


def _ask(label: str, current: str) -> str:
    hint = f"（当前已设置，长度 {len(current)}；直接回车保持不变）" if current else "（必填）"
    while True:
        val = _read_hidden(f"  {label} {hint}: ").strip()
        if not val:
            if current:
                return current
            print("    ✗ 这项不能为空，请重新输入")
            continue
        if label == "encoding_aes_key" and len(val) != 43:
            print(f"    ✗ EncodingAESKey 必须是 43 个字符（你输入了 {len(val)} 个），请检查")
            continue
        return val


def main() -> int:
    ap = argparse.ArgumentParser(description="填写企业微信自建应用配置（输入被隐藏，不会打印值）")
    ap.add_argument("--data-dir", default=None,
                    help="Kairos 数据目录（默认：环境变量 KAIROS_DATA_DIR 或仓库的 data/）")
    args = ap.parse_args()

    from kairos.settings_store import SettingsStore

    path = (Path(args.data_dir) / "settings.json") if args.data_dir else None
    store = SettingsStore(path) if path else SettingsStore()
    cur = store.get().wecom

    print("=" * 66)
    print("  企业微信「自建应用」配置填写")
    print("  输入不回显；只显示长度；不会打印任何密钥内容")
    print("=" * 66)
    print(f"  写入位置：{path or '(默认数据目录)'}")
    print("")

    corp_id = _ask("corp_id", cur.corp_id)
    corp_secret = _ask("corp_secret", cur.corp_secret)
    agent_id = _ask("agent_id", cur.agent_id)
    token = _ask("token", cur.token)
    aes_key = _ask("encoding_aes_key", cur.encoding_aes_key)

    store.update({"wecom": {
        "corp_id": corp_id,
        "corp_secret": corp_secret,
        "agent_id": agent_id,
        "token": token,
        "encoding_aes_key": aes_key,
        "enabled": True,
    }})

    after = store.get().wecom
    print("")
    print("=" * 66)
    print("  ✓ 已写入（下面只显示长度，值本身没有打印）")
    print("=" * 66)
    for name in ("corp_id", "corp_secret", "agent_id", "token", "encoding_aes_key"):
        v = getattr(after, name)
        print(f"    {name:20s} 长度 {len(v):3d}  {'✓' if v else '✗ 空'}")
    print(f"    {'enabled':20s} {after.enabled}")
    print("")
    print("  下一步：")
    print("    1) 重启 Kairos（让回调路由读到新配置）")
    print("    2) 企微后台 → 应用 → 「接收消息」→ 设置 API 接收")
    print("         URL = <能被企微访问到的地址>/api/wecom/webhook")
    print("         加解密方式 = 安全模式")
    print("         Token / EncodingAESKey 填成跟这里一样")
    print("    3) 保存时企微会发一个 GET 验证请求，通过即成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
