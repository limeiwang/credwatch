"""credwatch setup：傻瓜式向导。每一步都能直接回车跳过，全程不用碰配置文件。

平台和通知渠道都是从 adapters / notify.CHANNELS 自动生成的：
以后加一个新平台或新渠道，这里不用改。
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
import subprocess
import sys

from . import configfile, desktop, engine, notify, report
from .adapters import BUILTIN
from .core import MISSING, EXPIRED


def ask(prompt: str, default: str = "") -> str:
    try:
        v = input(prompt).strip()
    except EOFError:
        v = ""
    return v or default


def yes(prompt: str, default: bool = True) -> bool:
    v = ask(f"{prompt} [{'Y/n' if default else 'y/N'}] ").lower()
    return default if not v else v in ("y", "yes", "是", "好", "要")


def parse_date(s: str):
    """接受 2026-12-31 / 2026/12/31 / 90（天后）/ 90天。"""
    s = s.strip()
    m = re.fullmatch(r"(\d+)\s*天?", s)
    if m:
        return dt.date.today() + dt.timedelta(days=int(m.group(1)))
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        try:
            return dt.date(*map(int, m.groups()))
        except ValueError:
            return None
    return None


def step_logins() -> None:
    print("\n① 检查登录")
    creds = engine.check_all()
    for name, mod in BUILTIN.items():
        label, tool = getattr(mod, "LABEL", name), getattr(mod, "TOOL", name)
        mine = [c for c in creds if c.platform == name]
        if any(c.status not in (MISSING, EXPIRED) for c in mine):
            acct = next((c.account for c in mine if c.account), "")
            print(f"  ✅ {label} 已登录" + (f"（{acct}）" if acct else ""))
            continue
        if not shutil.which(tool):
            print(f"  ⚪ 没装 {tool}，跳过 {label}（用得到时再装：{getattr(mod, 'INSTALL_HINT', '')}）")
            continue
        if yes(f"  {label} 还没登录，现在登录吗？", True):
            cmd = mod.login_cmd()
            cmd[0] = shutil.which(cmd[0]) or cmd[0]
            print("  → 会打开浏览器；如果终端显示一次性验证码，把它填进网页即可")
            subprocess.call(cmd)


def step_notify() -> None:
    print("\n② 提醒方式")
    if desktop.available():
        print("  ✅ 电脑弹窗提醒已开启（不用设置）")
    cfg = engine.load_config().get("notify", {})
    done = [c["label"] for c in notify.CHANNELS if all(cfg.get(k) for k, _ in c["fields"])]
    if done:
        print("  ✅ 已接入：" + "、".join(done))
    menu = "  ".join(f"{i}) {c['label']}" for i, c in enumerate(notify.CHANNELS, 1))
    print(f"  还想推到手机吗？ {menu}  回车) 不用")
    pick = ask("  选一个编号：")
    if not pick.isdigit() or not 1 <= int(pick) <= len(notify.CHANNELS):
        return
    ch = notify.CHANNELS[int(pick) - 1]
    print(f"  怎么拿：{ch['help']}")
    for _ in range(3):
        vals = {}
        for key, label in ch["fields"]:
            vals[key] = ask(f"  粘贴{label}：")
            if not vals[key]:
                print("  已跳过")
                return
        for k, v in vals.items():
            configfile.set_key("notify", k, v)
        res = send_test(only=ch)
        print("  " + "；".join(res))
        if any("成功" in r for r in res):
            print(f"  ✅ {ch['label']} 已接入，去看看是否收到测试消息")
            return
        if not yes("  没发成功，重新粘贴一次？", True):
            return


def send_test(only: dict | None = None) -> list[str]:
    from .core import EXPIRING, Credential, now
    demo = Credential(platform="demo", name="测试凭证", account="you", status=EXPIRING,
                      expires_at=now() + dt.timedelta(days=2), renew_hint="credwatch renew")
    cfg = dict(engine.load_config().get("notify", {}))
    if only:  # 只测刚填的渠道，不弹电脑通知
        cfg = {k: cfg.get(k) for k, _ in only["fields"]} | {"desktop": False}
    return notify.send(cfg, [demo], [])


def step_reminders() -> None:
    print("\n③ 到期提醒（API Key、证书、域名…任何有到期日的东西）")
    while yes("  要加一条吗？", False):
        name = ask("  叫什么（如 OpenAI API Key）：")
        if not name:
            break
        when = None
        while when is None:
            when = parse_date(ask("  哪天到期（如 2026-12-31，或填 90 表示 90 天后）："))
            if when is None:
                print("  没看懂这个日期，再试一次")
        renew = ask("  到时候怎么续（可回车跳过）：")
        add_reminder(name, when, renew)


def add_reminder(name: str, when: dt.date, renew: str = "") -> str:
    replaced = configfile.add_custom(name, expires_at=when.isoformat(), renew=renew)
    msg = f"{'🔁 已更新' if replaced else '✅ 已添加'}提醒：{name}，{when.isoformat()} 到期"
    print("  " + msg)
    return msg


def run() -> int:
    if not sys.stdin or not sys.stdin.isatty():
        print("credwatch setup 需要在终端里运行（这里不是交互终端，已跳过）。")
        return 0
    print("credwatch 设置向导（每一步都可以直接回车跳过）")
    step_logins()
    step_notify()
    step_reminders()
    print("\n④ 完成，当前状态：\n")
    print(report.terminal(engine.check_all()))
    print("\n之后会每 30 分钟自动检查，快到期时提醒你。随时可以重新运行 credwatch setup。")
    return 0
