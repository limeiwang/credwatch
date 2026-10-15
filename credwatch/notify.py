"""告警推送：电脑弹窗（默认开启）+ 飞书 / 钉钉 / 企业微信 / Slack / Telegram / 通用 webhook。

新增渠道：在 CHANNELS 里加一项（向导会自动出现这个选项），再在 send() 里实现发送。
"""
from __future__ import annotations

import json
import urllib.parse

from .core import STATUS_ICON, STATUS_LABEL, Credential, http_json


# 向导自动根据这里生成菜单：label、要填的字段、去哪拿
CHANNELS = [
    {"id": "feishu", "label": "飞书", "fields": [("feishu_webhook", "webhook 地址")],
     "help": "飞书建一个只有你的群 → 群设置 → 群机器人 → 添加机器人 → 自定义机器人 → 复制 webhook 地址"},
    {"id": "wecom", "label": "企业微信", "fields": [("wecom_webhook", "webhook 地址")],
     "help": "企业微信群（需 3 人以上）→ 右上角 … → 添加群机器人 → 新创建 → 复制 webhook 地址"},
    {"id": "dingtalk", "label": "钉钉", "fields": [("dingtalk_webhook", "webhook 地址")],
     "help": "钉钉群设置 → 机器人 → 添加机器人 → 自定义 → 安全设置选“自定义关键词”填 credwatch → 复制 webhook"},
    {"id": "telegram", "label": "Telegram", "fields": [("telegram_bot_token", "Bot Token"), ("telegram_chat_id", "Chat ID")],
     "help": "找 @BotFather 建机器人拿 Token；给机器人发条消息后找 @userinfobot 拿你的 Chat ID"},
    {"id": "slack", "label": "Slack", "fields": [("slack_webhook", "webhook 地址")],
     "help": "api.slack.com/apps → 新建 App → Incoming Webhooks → 复制地址"},
    {"id": "webhook", "label": "自定义 webhook（JSON POST）", "fields": [("webhook", "地址")],
     "help": "任何能接收 JSON POST 的地址"},
]


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


def _ok(status) -> str:
    return "成功" if isinstance(status, int) and 200 <= status < 300 else f"失败（HTTP {status}）"


def send(cfg: dict, alerts: list[Credential], renewed: list[dict]) -> list[str]:
    text = compose(alerts, renewed)
    results = []
    if cfg.get("desktop", True):  # 零配置：默认就弹电脑通知
        from . import desktop
        first = text.splitlines()[1] if len(text.splitlines()) > 1 else text
        results.append(desktop.show("credwatch 凭证提醒", first + ("\n…" if len(alerts) > 1 else "")))
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
                results.append(f"{key}: {_ok(fn(cfg[key]))}")
            except Exception as e:
                results.append(f"{key}: 失败 {e}")
    if cfg.get("telegram_bot_token") and cfg.get("telegram_chat_id"):
        url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
        try:
            results.append(f"telegram: {_ok(_post(url, {'chat_id': cfg['telegram_chat_id'], 'text': text}))}")
        except Exception as e:
            results.append(f"telegram: 失败 {e}")
    return results
