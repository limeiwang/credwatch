"""核心数据模型与公共工具。"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import shutil
import subprocess
from typing import Optional

UTC = dt.timezone.utc

# 状态枚举
OK = "ok"              # 正常
EXPIRING = "expiring"  # 即将过期
EXPIRED = "expired"    # 已过期 / 失效
UNKNOWN = "unknown"    # 无法判断
MISSING = "missing"    # 未登录 / 未配置

STATUS_LABEL = {
    OK: "正常",
    EXPIRING: "将过期",
    EXPIRED: "已失效",
    UNKNOWN: "未知",
    MISSING: "未登录",
}
STATUS_ICON = {OK: "🟢", EXPIRING: "🟡", EXPIRED: "🔴", UNKNOWN: "⚪", MISSING: "⚫"}
SEVERITY = {OK: 0, UNKNOWN: 1, MISSING: 1, EXPIRING: 2, EXPIRED: 3}


def now() -> dt.datetime:
    return dt.datetime.now(UTC)


def parse_time(value) -> Optional[dt.datetime]:
    """尽量宽容地解析各平台返回的时间字符串。"""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, UTC)
    s = str(value).strip()
    if s.endswith(" UTC"):
        s = s[:-4] + "+00:00"
    s = s.replace("Z", "+00:00")
    for fmt in (None, "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%d"):
        try:
            t = dt.datetime.fromisoformat(s) if fmt is None else dt.datetime.strptime(s, fmt)
            return t if t.tzinfo else t.replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def humanize(delta: Optional[dt.timedelta]) -> str:
    if delta is None:
        return "不过期"
    secs = int(delta.total_seconds())
    sign = "" if secs >= 0 else "已过 "
    secs = abs(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    if d:
        txt = f"{d}天{h}小时"
    elif h:
        txt = f"{h}小时{m}分"
    else:
        txt = f"{m}分钟"
    return sign + txt


@dataclasses.dataclass
class Credential:
    platform: str                 # github / cloudflare / custom...
    name: str                     # 显示名，如 "gh CLI (github.com)"
    account: str = ""             # 账号
    kind: str = ""                # oauth / pat / api_token ...
    expires_at: Optional[dt.datetime] = None
    status: str = UNKNOWN
    scopes: list = dataclasses.field(default_factory=list)
    auto_renewable: bool = False  # 是否可无人值守续期
    renew_hint: str = ""          # 人工续期指令
    detail: str = ""
    checked_at: dt.datetime = dataclasses.field(default_factory=now)

    @property
    def id(self) -> str:
        return f"{self.platform}:{self.name}"

    @property
    def remaining(self) -> Optional[dt.timedelta]:
        return None if self.expires_at is None else self.expires_at - now()

    def apply_threshold(self, warn: dt.timedelta) -> "Credential":
        """根据剩余时间修正状态（仅在适配器判定为 ok 时）。"""
        rem = self.remaining
        if self.status == OK and rem is not None:
            if rem.total_seconds() <= 0:
                self.status = EXPIRED
            elif rem <= warn:
                self.status = EXPIRING
        return self

    def to_dict(self) -> dict:
        rem = self.remaining
        return {
            "id": self.id,
            "platform": self.platform,
            "name": self.name,
            "account": self.account,
            "kind": self.kind,
            "status": self.status,
            "status_label": STATUS_LABEL[self.status],
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "remaining_seconds": int(rem.total_seconds()) if rem else None,
            "remaining_human": ("自动续期中" if rem is None and self.auto_renewable and self.status == OK else humanize(rem)),
            "scopes": self.scopes,
            "auto_renewable": self.auto_renewable,
            "renew_hint": self.renew_hint,
            "detail": self.detail,
            "checked_at": self.checked_at.isoformat(),
        }


def is_headless() -> bool:
    """SSH 远程、容器、无图形界面的 bot 机器。"""
    if os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_TTY") or os.environ.get("CODESPACES"):
        return True
    if os.path.exists("/.dockerenv"):
        return True
    import platform
    if platform.system() == "Linux" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return True
    return False


def run(cmd: list[str], timeout: int = 30, env: Optional[dict] = None) -> tuple[int, str, str]:
    """执行命令，命令不存在时返回 127。"""
    if not shutil.which(cmd[0]):
        return 127, "", f"{cmd[0]}: command not found"
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, **(env or {})}, stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def http_json(url: str, headers: dict, method: str = "GET", data: Optional[bytes] = None, timeout: int = 15):
    """返回 (status, headers, json_or_text)。"""
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, headers=headers, method=method, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            hdrs = {k.lower(): v for k, v in r.headers.items()}
            status = r.status
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="ignore")
        hdrs = {k.lower(): v for k, v in e.headers.items()}
        status = e.code
    try:
        return status, hdrs, json.loads(body)
    except ValueError:
        return status, hdrs, body


def parse_duration(s: str):
    """'90m' / '2h' / '1d' / '45' (分钟) -> timedelta"""
    s = str(s).strip().lower()
    unit = {"m": 60, "h": 3600, "d": 86400}.get(s[-1:], None)
    num = float(s[:-1]) if unit else float(s)
    return dt.timedelta(seconds=num * (unit or 60))
