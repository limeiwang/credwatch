"""自定义凭证：任何平台都能接入。

在配置文件里写：
[[custom]]
name = "Vercel CLI"
check = "vercel whoami"          # 可选：退出码 0 视为有效
expires_at = "2026-12-31"        # 可选：已知到期日（如 PAT）
renew = "vercel login"           # 可选：续期命令（auto = true 时会自动执行）
auto = false
"""
from __future__ import annotations

import shlex

from ..core import EXPIRED, OK, UNKNOWN, Credential, parse_time, run


def check(items: list[dict]) -> list[Credential]:
    creds = []
    for it in items:
        cred = Credential(platform=it.get("platform", "custom"), name=it["name"],
                          kind=it.get("kind", "custom" if it.get("check") else "reminder"), renew_hint=it.get("renew", ""),
                          auto_renewable=bool(it.get("auto")) and bool(it.get("renew")),
                          expires_at=parse_time(it.get("expires_at")))
        if it.get("check"):
            code, out, err = run(shlex.split(it["check"]))
            if code == 0:
                cred.status = OK
                cred.account = out.strip().splitlines()[-1][:60] if out.strip() else ""
            elif code == 127:
                cred.status, cred.detail = UNKNOWN, "检查命令不存在"
            else:
                cred.status, cred.detail = EXPIRED, (err or out).strip()[:200]
        else:
            cred.status = OK if cred.expires_at else UNKNOWN
        creds.append(cred)
    return creds


def renew(cred: Credential) -> tuple[bool, str]:
    if not cred.auto_renewable:
        return False, f"需要人工处理：{cred.renew_hint or '未配置续期方式'}"
    code, out, err = run(shlex.split(cred.renew_hint), timeout=300)
    return code == 0, (out or err).strip()[:300] or f"退出码 {code}"
