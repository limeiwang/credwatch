"""Cloudflare：wrangler OAuth 登录态 + CLOUDFLARE_API_TOKEN。

wrangler 登录后把 oauth_token / refresh_token / expiration_time 写在本地
default.toml 里。access token 有效期很短（约 1 小时），但只要 refresh_token
还在，wrangler 下次执行命令时就会自动刷新 —— 所以这里把它判定为“可自动续期”，
renew 时调用一次 `wrangler whoami` 触发刷新。
"""
from __future__ import annotations

import os
import platform
import shutil
import tomllib
from pathlib import Path

from ..core import (EXPIRED, MISSING, OK, UNKNOWN, Credential, http_json, is_headless, now, parse_time, run)

NET_ERRORS = ("enotfound", "etimedout", "econnrefused", "econnreset", "fetch failed",
              "getaddrinfo", "network", "socket hang up", "certificate")


def login_hint() -> str:
    # 远程/无界面机器上 localhost:8976 回调打不开，用设备码登录
    return "wrangler login --device（在手机或电脑浏览器输入验证码即可）" if is_headless() else "wrangler login"


def wrangler_config_paths() -> list[Path]:
    home = Path.home()
    paths = []
    if os.environ.get("WRANGLER_CONFIG"):
        paths.append(Path(os.environ["WRANGLER_CONFIG"]))
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        paths.append(Path(xdg) / ".wrangler/config/default.toml")
    paths.append(home / ".config/.wrangler/config/default.toml")
    if platform.system() == "Darwin":
        paths.append(home / "Library/Preferences/.wrangler/config/default.toml")
    if platform.system() == "Windows" and os.environ.get("APPDATA"):
        paths.append(Path(os.environ["APPDATA"]) / "xdg.config/.wrangler/config/default.toml")
    paths.append(home / ".wrangler/config/default.toml")
    return paths


LABEL = "Cloudflare"
TOOL = "wrangler"
INSTALL_HINT = "npm install -g wrangler"


def login_cmd() -> list[str]:
    return wrangler_cmd() + ["login"]


def wrangler_cmd() -> list[str]:
    if shutil.which("wrangler"):
        return ["wrangler"]
    return ["npx", "--yes", "wrangler"]


def _check_wrangler() -> list[Credential]:
    path = next((p for p in wrangler_config_paths() if p.exists()), None)
    if path is None:
        return []
    cred = Credential(platform="cloudflare", name="wrangler CLI", kind="OAuth",
                      renew_hint=login_hint(), detail=str(path))
    try:
        cfg = tomllib.loads(path.read_text())
    except Exception as e:
        cred.status, cred.detail = UNKNOWN, f"配置无法解析：{e}"
        return [cred]
    if cfg.get("api_token"):
        cred.kind = "API Token（写在 wrangler 配置里）"
        return [_inspect_api_token(cfg["api_token"], "wrangler 配置 api_token")]
    if not cfg.get("oauth_token"):
        cred.status = MISSING
        return [cred]
    cred.scopes = list(cfg.get("scopes", []))
    access_exp = parse_time(cfg.get("expiration_time"))
    has_refresh = bool(cfg.get("refresh_token"))
    cred.auto_renewable = has_refresh
    if has_refresh:
        # access token 只有约 1 小时，本身就是短命的；只要 refresh_token 在，
        # 对用户来说就是“可用且自动续”，不按 access token 的倒计时报警
        cred.status, cred.expires_at = OK, None
        cred.detail = "refresh_token 可用，access token 会自动刷新" + (
            "" if access_exp and access_exp > now() else "（当前 access token 已过期，下次调用即刷新）")
    elif access_exp and access_exp > now():
        cred.status, cred.expires_at = OK, access_exp
        cred.detail = "access token 有效，但没有 refresh_token，过期后需重新登录"
    else:
        cred.status, cred.expires_at = EXPIRED, access_exp
        cred.detail = "access token 过期且没有 refresh_token，需要重新 wrangler login"
    return [cred]


def _inspect_api_token(token: str, name: str) -> Credential:
    cred = Credential(platform="cloudflare", name=name, kind="API Token",
                      renew_hint="到 https://dash.cloudflare.com/profile/api-tokens 轮换令牌")
    try:
        status, _, body = http_json("https://api.cloudflare.com/client/v4/user/tokens/verify",
                                    {"Authorization": f"Bearer {token}", "User-Agent": "credwatch"})
    except Exception as e:
        cred.status, cred.detail = UNKNOWN, f"无法连接 Cloudflare：{e}"
        return cred
    result = body.get("result") or {} if isinstance(body, dict) else {}
    if status == 200 and result.get("status") == "active":
        cred.status = OK
        cred.expires_at = parse_time(result.get("expires_on"))
        if cred.expires_at is None:
            cred.detail = "该令牌未设置过期时间"
    elif status in (400, 401, 403) or result.get("status") in ("disabled", "expired"):
        cred.status, cred.detail = EXPIRED, f"令牌无效（{result.get('status') or status}）"
    else:
        cred.status, cred.detail = UNKNOWN, f"Cloudflare 返回 {status}"
    return cred


def check() -> list[Credential]:
    creds = _check_wrangler()
    for var in ("CLOUDFLARE_API_TOKEN", "CF_API_TOKEN"):
        tok = os.environ.get(var)
        if tok:
            creds.append(_inspect_api_token(tok, f"环境变量 {var}"))
    return creds


def renew(cred: Credential) -> tuple[bool, str]:
    if cred.name != "wrangler CLI" or not cred.auto_renewable:
        return False, f"需要人工处理：{cred.renew_hint}"
    code, out, err = run(wrangler_cmd() + ["whoami"], timeout=120)
    text = (out + err).lower()
    if code == 0 and "not authenticated" not in text:
        return True, "已通过 refresh_token 刷新 wrangler 登录态"
    if any(k in text for k in NET_ERRORS):
        # wrangler 会把网络失败误报成“登录过期”，这里分开：凭证没动，稍后自动重试
        return False, "连不上 Cloudflare 认证服务器，登录态没有动，下一轮会自动重试（不用重新登录）"
    return False, f"refresh_token 已失效，请执行：{login_hint()}"
