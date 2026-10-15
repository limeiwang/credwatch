"""告警推送：飞书 / 钉钉 / 企业微信 / Slack / Telegram / 通用 webhook。"""
from __future__ import annotations

import json
import urllib.parse

from .core import STATUS_ICON, STATUS_LABEL, Credential, http_json


def compose(alerts: list[Credential], renewed: list[dict]) -> str:
    lines = ["🔐 credwatch 凭证提醒"]
    for c in alerts:
        lines.append(f"{STATUS_ICON[c.status]} {c.name} {c.account and '(' + c.account + ')'}"
                     f" — {STATUS_LABEL[c.status]}，剩余 {c.to_dict()['remaining_human']}")
        if c.renew_hint:
            lines.append(f"   ↳ 续期：{c.renew_hint}")
    for r in renewed:
        lines.append(("✅ 已自动续期 " if r["ok"] else "❌ 自动续期失败 ") + f"{r['id']}：{r['message']}")
    return "\n".join(lines)


def _post(url: str, payload: dict) -> int:
    status, _, _ = http_json(url, {"Content-Type": "application/json", "User-Agent": "credwatch"},
                             method="POST", data=json.dumps(payload).encode())
    return status


def send(cfg: dict, alerts: list[Credential], renewed: list[dict]) -> list[str]:
    text = compose(alerts, renewed)
    results = []
    targets = {
        "feishu_webhook": lambda u: _post(u, {"msg_type": "text", "content": {"text": text}}),
        "dingtalk_webhook": lambda u: _post(u, {"msgtype": "text", "text": {"content": text}}),
        "wecom_webhook": lambda u: _post(u, {"msgtype": "text", "text": {"content": text}}),
        "slack_webhook": lambda u: _post(u, {"text": text}),
        "webhook": lambda u: _post(u, {"text": text,
                                       "alerts": [c.to_dict() for c in alerts], "renewed": renewed}),
    }
    for key, fn in targets.items():
        if cfg.get(key):
            try:
                results.append(f"{key}: {fn(cfg[key])}")
            except Exception as e:
                results.append(f"{key}: 失败 {e}")
    if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
        try:
            results.append(f"telegram: {_post(url, {'chat_id': cfg['telegram_chat_id'], 'text': text})}")
        except Exception as e:
            results.append(f"telegram: 失败 {e}")
    return results
