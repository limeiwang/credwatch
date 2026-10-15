"""命令行入口：credwatch status | renew | watch | dashboard | mcp | init"""
from __future__ import annotations

import argparse
import json
import sys
import time
import webbrowser

from . import __version__, engine, report


def main(argv=None) -> int:
    for stream in (sys.stdin, sys.stdout, sys.stderr):  # Windows 默认 GBK，统一成 UTF-8
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass
    p = argparse.ArgumentParser(prog="credwatch", description="AI bot 的 CLI 授权到期看板与自动续期")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("status", help="查看所有授权状态（默认命令）")
    s.add_argument("--json", action="store_true", help="输出 JSON，方便脚本和 agent 读取")

    r = sub.add_parser("renew", help="续期凭证")
    r.add_argument("id", nargs="?", help="凭证 id 或名称；不填则续期所有可自动续期且将过期的")

    w = sub.add_parser("watch", help="常驻巡检：检查 → 自动续期 → 告警")
    w.add_argument("--interval", type=int, default=3600, help="巡检间隔秒数，默认 3600")
    w.add_argument("--once", action="store_true", help="只跑一轮（适合放进 cron）")
    w.add_argument("--html", help="每轮把看板写到这个 HTML 文件")

    d = sub.add_parser("dashboard", help="生成 HTML 看板")
    d.add_argument("-o", "--output", default=str(engine.HOME / "dashboard.html"))
    d.add_argument("--open", action="store_true", help="生成后用浏览器打开")

    pf = sub.add_parser("preflight", help="长任务开跑前检查：授权能否撑过整个任务")
    pf.add_argument("--for", dest="duration", default="2h", help="预计任务时长，如 90m / 3h / 1d")
    pf.add_argument("--platform", action="append", help="只检查某平台，可重复，如 --platform github")
    pf.add_argument("--json", action="store_true")

    dr = sub.add_parser("doctor", help="体检：找出“登录了还是失败”的隐蔽原因")
    dr.add_argument("--fix", action="store_true", help="自动修复能修的项")
    sub.add_parser("statusline", help="输出一行状态，给 Claude Code 状态栏 / shell 提示符用")
    sub.add_parser("mcp", help="以 MCP 服务器方式运行（给 Claude Code / Cursor 用）")
    sub.add_parser("setup", help="傻瓜式设置向导：登录、手机提醒、到期提醒，一步步问")
    ad = sub.add_parser("add", help="加一条到期提醒，如：credwatch add \"OpenAI API Key\" 2026-12-31")
    ad.add_argument("name", nargs="?")
    ad.add_argument("date", nargs="?", help="2026-12-31，或 90 表示 90 天后")
    ad.add_argument("--renew", default="", help="到时候怎么续（可选）")
    rm = sub.add_parser("remove", help="删掉一条到期提醒")
    rm.add_argument("name")
    sub.add_parser("init", help="生成默认配置文件")
    sub.add_parser("test-notify", help="发送一条测试告警")

    a = p.parse_args(argv)
    cmd = a.cmd or "status"

    if cmd == "init":
        engine.load_config()
        print(f"配置文件：{engine.CONFIG}")
        return 0

    if cmd == "setup":
        from .wizard import run as wizard
        return wizard()

    if cmd == "add":
        from .wizard import add_reminder, ask, parse_date
        name = a.name or ask("叫什么（如 OpenAI API Key）：")
        when = parse_date(a.date or ask("哪天到期（如 2026-12-31，或 90 表示 90 天后）："))
        if not name or when is None:
            print("没看懂。示例：credwatch add \"OpenAI API Key\" 2026-12-31")
            return 2
        add_reminder(name, when, a.renew)
        return 0

    if cmd == "remove":
        from . import configfile
        n = configfile.remove_custom(a.name)
        print(f"✅ 已删除 {a.name}" if n else f"没找到叫 {a.name} 的提醒")
        return 0 if n else 1

    if cmd == "mcp":
        from .mcp_server import serve
        serve()
        return 0

    if cmd == "statusline":
        try:
            sys.stdin.read() if not sys.stdin.isatty() else None  # Claude Code 会从 stdin 传会话信息
        except Exception:
            pass
        print(report.statusline(engine.cached_check()))
        return 0

    if cmd == "preflight":
        from .core import parse_duration
        res = engine.preflight(parse_duration(a.duration), a.platform)
        if a.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        elif not res["checked"]:
            print("⚪ 没发现需要检查的授权（可用 credwatch init 后在配置里添加）")
        elif res["go"]:
            print(f"✅ 可以开跑：{len(res['checked'])} 项授权都能撑过 {a.duration}")
        else:
            print(f"⛔ 先别跑：有 {len(res['blockers'])} 项授权撑不过 {a.duration}")
            for b in res["blockers"]:
                print(f"  • {b['id']}（剩 {b['remaining']}）→ {b['fix']}")
        return 0 if res["go"] else 1

    if cmd == "doctor":
        from .doctor import diagnose, fix
        found = diagnose()
        if a.fix:
            res = fix(found)
            print("\n".join(res) or "没有可自动修复的项")
            return 0
        if not found:
            print("✅ 没发现隐患")
            return 0
        icon = {"error": "🔴", "warn": "🟡", "info": "💡"}
        for f in found:
            print(f"{icon[f['level']]} {f['title']}")
            if f["detail"]:
                print(f"   {f['detail']}")
            print(f"   修复：{f['fix']}")
        if any(f["auto_fix"] for f in found):
            print("\n运行 credwatch doctor --fix 一键修复标记项")
        return 1 if any(f["level"] == "error" for f in found) else 0

    if cmd == "status":
        creds = engine.check_all()
        if getattr(a, "json", False):
            print(json.dumps([c.to_dict() for c in creds], ensure_ascii=False, indent=2))
        else:
            print(report.terminal(creds))
        return 1 if any(c.status in ("expired", "missing") for c in creds) else 0

    if cmd == "renew":
        creds = engine.check_all()
        targets = ([engine.find(creds, a.id)] if a.id else
                   [c for c in creds if c.auto_renewable and c.status in ("expiring", "expired")])
        if a.id and targets[0] is None:
            print(f"找不到 {a.id}。可选：" + ", ".join(c.id for c in creds))
            return 2
        if not targets:
            print("没有需要续期的凭证 ✅")
            return 0
        rc = 0
        for c in targets:
            ok, msg = engine.renew(c)
            print(("✅ " if ok else "🙋 ") + f"{c.name}: {msg}")
            rc |= 0 if ok else 1
        return rc

    if cmd == "dashboard":
        from pathlib import Path
        out = Path(a.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.html_page(engine.check_all()), encoding="utf-8")
        print(f"看板已生成：{out}")
        if a.open:
            webbrowser.open(out.resolve().as_uri())
        return 0

    if cmd == "test-notify":
        from .wizard import send_test
        res = send_test()
        print("\n".join(res) or "没有可用的提醒渠道，运行 credwatch setup 接入飞书等")
        return 0

    if cmd == "watch":
        while True:
            res = engine.cycle()
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{stamp}]\n" + report.terminal(res["credentials"]))
            for x in res["renewed"]:
                print(("✅ 自动续期 " if x["ok"] else "❌ 续期失败 ") + f"{x['id']}: {x['message']}")
            if res["alerts"]:
                print(f"已告警 {len(res['alerts'])} 条 {res['sent']}")
            if a.html:
                from pathlib import Path
                Path(a.html).write_text(report.html_page(res["credentials"]), encoding="utf-8")
            if a.once:
                return 0
            time.sleep(a.interval)
    return 0


if __name__ == "__main__":
    sys.exit(main())
