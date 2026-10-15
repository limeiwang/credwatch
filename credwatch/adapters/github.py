"""GitHub：gh CLI 登录态 + 环境变量里的 GITHUB_TOKEN / GH_TOKEN。

到期时间来源：GitHub API 对带过期时间的 PAT 会返回响应头
`github-authentication-token-expiration`；gh 的 OAuth token 默认不过期，
但可能被撤销，所以用 /user 接口实测是否有效。
"""
from __future__ import annotations

import os
import re

import time

from ..core import EXPIRED, MISSING, OK, UNKNOWN, Credential, http_json, is_headless, parse_time, run

API = os.environ.get("CREDWATCH_GITHUB_API", "https://api.github.com")


def _kind(token: str) -> str:
    for prefix, kind in (("github_pat_", "fine-grained PAT"), ("ghp_", "classic PAT"),
                         ("gho_", "OAuth (gh CLI)"), ("ghu_", "GitHub App 用户令牌"),
                         ("ghs_", "GitHub App 安装令牌")):
        if token.startswith(prefix):
            return kind
    return "token"


def inspect_token(token: str, name: str, renew_hint: str, auto: bool = False) -> Credential:
    cred = Credential(platform="github", name=name, kind=_kind(token), renew_hint=renew_hint,
                      auto_renewable=auto)
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "User-Agent": "credwatch"}
    # 偶发的 401/5xx/网络抖动很常见，重试一次再下结论，避免“假过期”把人带偏
    status, hdrs, body, err = None, {}, None, None
    for attempt in range(2):
        try:
            status, hdrs, body = http_json(f"{API}/user", headers)
            err = None
        except Exception as e:
            err = e
        if err is None and status not in (401, 500, 502, 503, 504):
            break
        if attempt == 0:
            time.sleep(1.5)
    if err is not None:
        cred.status, cred.detail = UNKNOWN, f"连不上 GitHub（{err}），凭证本身可能没问题"
        return cred
    if status == 401:
        cred.status, cred.detail = EXPIRED, "令牌已失效或被撤销（连续两次 401）"
        return cred
    if status != 200:
        cred.status, cred.detail = UNKNOWN, f"GitHub 返回 {status}"
        return cred
    cred.status = OK
    cred.account = body.get("login", "") if isinstance(body, dict) else ""
    scopes = hdrs.get("x-oauth-scopes", "")
    cred.scopes = [s.strip() for s in scopes.split(",") if s.strip()]
    cred.expires_at = parse_time(hdrs.get("github-authentication-token-expiration"))
    if cred.expires_at is None:
        cred.detail = "该令牌未设置过期时间"
    return cred


def check() -> list[Credential]:
    creds: list[Credential] = []

    # 1) gh CLI 登录态（可能有多个 host）
    code, out, err = run(["gh", "auth", "status"])
    text = out + err
    if code == 127:
        pass  # 没装 gh，跳过
    else:
        hosts = re.findall(r"^\s*([\w.-]+\.[\w.-]+)\s*$", text, re.M) or ["github.com"]
        logged_in = "logged in" in text.lower()
        for host in dict.fromkeys(hosts):
            name = f"gh CLI ({host})"
            hint = f"gh auth login --hostname {host}"
            if is_headless():
                hint += "（会给一次性验证码，用手机打开 github.com/login/device 输入即可）"
            # 屏蔽环境变量，读的才是 gh 自己保存的登录态；钥匙串偶尔超时，重试一次
            tok = ""
            for _ in range(2):
                c, out_tok, _ = run(["gh", "auth", "token", "--hostname", host], timeout=15,
                                    env={"GH_TOKEN": "", "GITHUB_TOKEN": "", "GH_ENTERPRISE_TOKEN": ""})
                tok = out_tok.strip() if c == 0 else ""
                if tok:
                    break
            if not tok:
                if logged_in:
                    creds.append(Credential(platform="github", name=name, status=UNKNOWN, renew_hint=hint,
                                            detail="gh 显示已登录，但读取钥匙串失败或超时，多半不是真过期"))
                else:
                    creds.append(Credential(platform="github", name=name, status=MISSING,
                                            renew_hint=hint, detail="gh 未登录"))
                continue
            creds.append(inspect_token(tok, name, hint))

    # 2) 环境变量里的令牌（agent 最常用的方式）
    for var in ("GITHUB_TOKEN", "GH_TOKEN"):
        tok = os.environ.get(var)
        if tok:
            creds.append(inspect_token(
                tok, f"环境变量 {var}",
                "到 https://github.com/settings/tokens 重新生成后更新该环境变量"))
    return creds


def renew(cred: Credential) -> tuple[bool, str]:
    # gh 的 OAuth token 不过期；失效只能重新走浏览器授权，无法无人值守
    return False, f"GitHub 需要人工授权，请执行：{cred.renew_hint}"
