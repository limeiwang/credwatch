"""体检：找出“明明登录了却还是失败”的隐蔽原因。

来自真实用户踩过的坑：
- git 全局 url.insteadOf 里写死了旧令牌，令牌过期后 gh 重新登录也没用，git push 照样失败
- ~/.git-credentials 明文存着早已失效的令牌
- 设置了 GH_TOKEN 环境变量，悄悄覆盖了 gh 钥匙串里的新登录
- wrangler 把 OAuth 令牌明文写在 default.toml 里
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .core import EXPIRED, is_headless, run

TOKEN_RE = re.compile(r"(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")


def _mask(tok: str) -> str:
    return tok[:8] + "…" + tok[-4:]


def diagnose(check_tokens: bool = True) -> list[dict]:
    findings = []

    def add(level, title, fix, detail="", action=None):
        # action 是可自动执行的修复命令（含令牌，只在本机执行，不对外输出）
        findings.append({"level": level, "title": title, "fix": fix, "detail": detail,
                         "auto_fix": bool(action), "_action": action})

    # 1) git url 重写里嵌着令牌
    code, out, _ = run(["git", "config", "--global", "--get-regexp", r"^url\..*\.insteadof$"])
    if code == 0:
        for line in out.splitlines():
            m = TOKEN_RE.search(line)
            if m:
                state = ""
                if check_tokens:
                    from .adapters.github import inspect_token
                    c = inspect_token(m.group(1), "git url 重写", "")
                    state = "（已失效）" if c.status == EXPIRED else ""
                key = line.split()[0]
                section = key[: -len(".insteadof")]
                add("error" if state else "warn", f"git 全局配置里写死了令牌 {_mask(m.group(1))}{state}",
                    "credwatch doctor --fix（删除这条重写并改用 gh 管理 git 凭证）",
                    "令牌过期后，即使 gh 重新登录，git push 仍会用这个旧令牌并失败",
                    action=[["git", "config", "--global", "--remove-section", section],
                            ["gh", "auth", "setup-git"]])

    # 2) ~/.git-credentials 明文令牌
    gc = Path.home() / ".git-credentials"
    if gc.exists():
        toks = TOKEN_RE.findall(gc.read_text(errors="ignore"))
        for t in dict.fromkeys(toks):
            dead = False
            if check_tokens:
                from .adapters.github import inspect_token
                dead = inspect_token(t, "git-credentials", "").status == EXPIRED
            add("error" if dead else "warn",
                f"~/.git-credentials 明文保存了令牌 {_mask(t)}" + ("（已失效）" if dead else ""),
                "删掉该行后执行 gh auth setup-git，改用 gh 管理 git 凭证")

    # 3) GH_TOKEN 覆盖 gh 登录
    if os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN"):
        c, _, _ = run(["gh", "auth", "token"], env={"GH_TOKEN": "", "GITHUB_TOKEN": ""})
        if c == 0:
            add("warn", "环境变量 GH_TOKEN/GITHUB_TOKEN 会覆盖 gh 自己的登录",
                "交互使用时去掉 shell 配置里的 GH_TOKEN，只在 CI/bot 进程里注入",
                "你重新 gh auth login 之后，gh 仍会优先用这个环境变量里的旧令牌")

    # 4) wrangler 明文令牌
    from .adapters.cloudflare import wrangler_config_paths
    for p in wrangler_config_paths():
        if p.exists() and "oauth_token" in p.read_text(errors="ignore"):
            add("info", f"wrangler 的 OAuth 令牌明文存放在 {p}",
                "wrangler login --use-keyring（改存系统钥匙串）")
            break

    # 5) 远程机器登录提示
    if is_headless():
        add("info", "这是一台远程/无界面机器，浏览器回调式登录会卡住",
            "GitHub 用 gh auth login（设备码）；Cloudflare 用 wrangler login --device")
    return findings


def public(findings: list[dict]) -> list[dict]:
    return [{k: v for k, v in f.items() if not k.startswith("_")} for f in findings]


def fix(findings: list[dict]) -> list[str]:
    done = []
    for f in findings:
        for cmd in f.get("_action") or []:
            code, out, err = run(cmd)
            if code not in (0, 127):
                done.append(f"❌ {f['title']}：{(err or out).strip()[:120]}")
                break
        else:
            if f.get("_action"):
                done.append(f"✅ 已修复：{f['title']}")
    return done
