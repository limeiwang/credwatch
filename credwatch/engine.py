"""配置加载、统一检查、续期、告警去重。"""
from __future__ import annotations

import datetime as dt
import json
import os
import tomllib
from pathlib import Path

from . import notify
from .adapters import BUILTIN, custom
from .core import EXPIRED, EXPIRING, MISSING, SEVERITY, Credential, now

HOME = Path(os.environ.get("CREDWATCH_HOME", Path.home() / ".credwatch"))
CONFIG = HOME / "config.toml"
STATE = HOME / "state.json"

DEFAULT_CONFIG = """# credwatch 配置
[general]
warn_days = 7            # 剩余不足多少天标黄并告警
auto_renew = true        # watch 模式下自动续期可续的凭证
platforms = ["github", "cloudflare"]

[notify]
# 任选其一或多个，留空则只在终端输出
# feishu_webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/xxx"
# dingtalk_webhook = "https://oapi.dingtalk.com/robot/send?access_token=xxx"
# wecom_webhook = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"
# slack_webhook = "https://hooks.slack.com/services/xxx"
# telegram_bot_token = "123:abc"
# telegram_chat_id = "123456"
# webhook = "https://example.com/hook"   # 通用 JSON POST
remind_days = [7, 3, 1]  # 阶梯提醒：剩 7 天、3 天、1 天各提醒一次，不刷屏
repeat_hours = 24        # 已失效/未登录的凭证，每隔多久再提醒一次

# 自定义凭证示例
# [[custom]]
# name = "Vercel CLI"
# check = "vercel whoami"
# renew = "vercel login"
# auto = false

# [[custom]]
# name = "OpenAI Key（手动记录）"
# expires_at = "2026-12-31"
# renew = "到控制台轮换后更新 .env"
"""


def load_config() -> dict:
    if not CONFIG.exists():
        HOME.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(DEFAULT_CONFIG, encoding="utf-8")
    return tomllib.loads(CONFIG.read_text(encoding="utf-8"))


def _adapter(cred: Credential):
    return BUILTIN.get(cred.platform, custom)


def check_all(cfg: dict | None = None) -> list[Credential]:
    cfg = cfg or load_config()
    g = cfg.get("general", {})
    warn = dt.timedelta(days=float(g.get("warn_days", 7)))
    creds: list[Credential] = []
    for name in g.get("platforms", list(BUILTIN)):
        mod = BUILTIN.get(name)
        if not mod:
            continue
        try:
            creds += mod.check()
        except Exception as e:  # 单个平台出错不影响其它
            from .core import UNKNOWN
            creds.append(Credential(platform=name, name=name, status=UNKNOWN, detail=f"检查出错：{e}"))
    creds += custom.check(cfg.get("custom", []))
    for c in creds:
        c.apply_threshold(warn)
    creds.sort(key=lambda c: (-SEVERITY[c.status], c.remaining or dt.timedelta(days=99999)))
    return creds


def renew(cred: Credential) -> tuple[bool, str]:
    return _adapter(cred).renew(cred)


def find(creds: list[Credential], key: str) -> Credential | None:
    key = key.lower()
    for c in creds:
        if c.id.lower() == key or c.name.lower() == key:
            return c
    matches = [c for c in creds if key in c.id.lower()]
    return matches[0] if len(matches) == 1 else None


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def cycle(cfg: dict | None = None, do_renew: bool | None = None, send: bool = True) -> dict:
    """一轮完整巡检：检查 → 自动续期 → 复查 → 告警（去重）。"""
    cfg = cfg or load_config()
    auto = cfg.get("general", {}).get("auto_renew", True) if do_renew is None else do_renew
    creds = check_all(cfg)
    renewed = []
    if auto:
        for c in creds:
            # 可自动续期的凭证每轮都“保活”一次，避免 refresh_token 因长期不用而失效
            if c.auto_renewable:
                ok, msg = renew(c)
                renewed.append({"id": c.id, "ok": ok, "message": msg})
        if renewed:
            creds = check_all(cfg)

    alerts = []
    state = _load_state()
    ncfg = cfg.get("notify", {})
    repeat = dt.timedelta(hours=float(ncfg.get("repeat_hours", 24)))
    milestones = sorted((float(x) for x in ncfg.get("remind_days", [7, 3, 1])), reverse=True)
    t = now()
    for c in creds:
        if c.status not in (EXPIRING, EXPIRED, MISSING):
            state.pop(c.id, None)
            continue
        last = state.get(c.id, {})
        last_at = dt.datetime.fromisoformat(last["at"]) if last.get("at") else None
        # 当前处在哪个提醒档位（剩余天数跌破的最小档）
        level = None
        if c.status == EXPIRING and c.remaining is not None:
            days = c.remaining.total_seconds() / 86400
            passed = [m for m in milestones if days <= m]
            level = min(passed) if passed else max(milestones, default=7)
        if last.get("status") == c.status:
            if c.status == EXPIRING and last.get("level") == level:
                continue  # 同一档位只提醒一次
            if c.status != EXPIRING and last_at and t - last_at < repeat:
                continue
        alerts.append(c)
        state[c.id] = {"status": c.status, "level": level, "at": t.isoformat()}

    sent = []
    if send and alerts:
        sent = notify.send(cfg.get("notify", {}), alerts, renewed)
    HOME.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    return {"credentials": creds, "renewed": renewed, "alerts": alerts, "sent": sent}


def preflight(duration: dt.timedelta, platforms: list[str] | None = None,
              cfg: dict | None = None) -> dict:
    """长任务开跑前的体检：这些授权能不能撑过整个任务？能续的先续上。"""
    cfg = cfg or load_config()
    creds = check_all(cfg)
    if platforms:
        creds = [c for c in creds if c.platform in platforms]
    items, renewed = [], False
    for c in creds:
        rem = c.remaining
        enough = c.status == "ok" and (rem is None or rem > duration)
        if not enough and c.auto_renewable:
            ok, _ = renew(c)
            renewed = renewed or ok
        items.append(c)
    if renewed:
        creds = [c for c in check_all(cfg) if not platforms or c.platform in platforms]
        items = creds
    blockers = []
    for c in items:
        rem = c.remaining
        if c.status in ("expired", "missing") or (rem is not None and rem <= duration):
            blockers.append({"id": c.id, "status": c.status, "remaining": c.to_dict()["remaining_human"],
                             "fix": c.renew_hint})
    return {"go": not blockers, "duration_minutes": int(duration.total_seconds() // 60),
            "blockers": blockers, "checked": [c.id for c in items]}


CACHE = HOME / "cache.json"


def cached_check(ttl_seconds: int = 300) -> list[dict]:
    """给状态栏这类高频调用用：5 分钟内直接读缓存，不打 API。"""
    try:
        data = json.loads(CACHE.read_text())
        if (now() - dt.datetime.fromisoformat(data["at"])).total_seconds() < ttl_seconds:
            return data["creds"]
    except Exception:
        pass
    creds = [c.to_dict() for c in check_all()]
    HOME.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"at": now().isoformat(), "creds": creds}, ensure_ascii=False))
    return creds
