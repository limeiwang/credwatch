"""终端看板与 HTML 看板。"""
from __future__ import annotations

import html
import unicodedata

from .core import STATUS_ICON, STATUS_LABEL, Credential, now

BAR = 20


def _width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad(s: str, w: int) -> str:
    return s + " " * max(0, w - _width(s))


def _bar(c: Credential, horizon_days: float = 30) -> str:
    rem = c.remaining
    if rem is None:
        if c.status == "ok":
            return ("↻ 自动续期" if c.auto_renewable else "∞ 不过期").ljust(BAR - 4)
        return "?".ljust(BAR)
    frac = max(0.0, min(1.0, rem.total_seconds() / (horizon_days * 86400)))
    n = round(frac * BAR)
    return "█" * n + "░" * (BAR - n)


def headline(creds: list[Credential]) -> str:
    bad = [c for c in creds if c.status in ("expired", "missing")]
    soon = [c for c in creds if c.status == "expiring"]
    unk = [c for c in creds if c.status == "unknown"]
    if not bad and not soon:
        auto = sum(1 for c in creds if c.auto_renewable)
        tail = f"，其中 {auto} 项自动续期中" if auto else ""
        return f"✅ {len(creds)} 项授权都正常{tail}" + (f"（{len(unk)} 项暂时无法确认）" if unk else "")
    parts = []
    if bad:
        parts.append(f"🔴 {len(bad)} 项已失效")
    if soon:
        first = min(soon, key=lambda c: c.remaining)
        parts.append(f"🟡 {len(soon)} 项将过期（最早：{first.name}，剩 {first.to_dict()['remaining_human']}）")
    return "，".join(parts)


def statusline(creds: list[dict]) -> str:
    """一行字，给 Claude Code 状态栏 / shell 提示符用。"""
    bad = [c for c in creds if c["status"] in ("expired", "missing")]
    soon = sorted((c for c in creds if c["status"] == "expiring"), key=lambda c: c["remaining_seconds"])
    if bad:
        return f"🔐🔴 {bad[0]['name']} 已失效" + (f" +{len(bad) - 1}" if len(bad) > 1 else "")
    if soon:
        return f"🔐🟡 {soon[0]['name']} 剩 {soon[0]['remaining_human']}"
    return f"🔐🟢 {len(creds)} 项授权正常" if creds else "🔐 未发现授权"


def terminal(creds: list[Credential]) -> str:
    if not creds:
        return "没有发现任何 CLI 授权。可在 ~/.credwatch/config.toml 添加 [[custom]] 条目。"
    rows = []
    for c in creds:
        d = c.to_dict()
        rows.append([f"{STATUS_ICON[c.status]} {STATUS_LABEL[c.status]}", c.name, c.account or "-",
                     _bar(c), d["remaining_human"], "自动" if c.auto_renewable else "人工"])
    head = ["状态", "凭证", "账号", "剩余（30天刻度）", "剩余时间", "续期"]
    widths = [max(_width(r[i]) for r in rows + [head]) for i in range(len(head))]
    out = [headline(creds), "",
           "  ".join(_pad(h, w) for h, w in zip(head, widths)),
           "  ".join("─" * w for w in widths)]
    out += ["  ".join(_pad(v, w) for v, w in zip(r, widths)) for r in rows]
    todo = [c for c in creds if c.status in ("expiring", "expired", "missing") and not c.auto_renewable]
    if todo:
        out.append("\n需要你处理：")
        out += [f"  • {c.name}: {c.renew_hint}" for c in todo]
    unk = [c for c in creds if c.status == "unknown"]
    if unk:
        out.append("\n暂时无法确认（不一定是过期）：")
        out += [f"  • {c.name}: {c.detail}" for c in unk]
    return "\n".join(out)


def html_page(creds: list[Credential]) -> str:
    color = {"ok": "#16a34a", "expiring": "#d97706", "expired": "#dc2626",
             "unknown": "#6b7280", "missing": "#374151"}
    cards = []
    for c in creds:
        d = c.to_dict()
        rem = c.remaining
        pct = 100 if rem is None and c.status == "ok" else (
            0 if rem is None else max(0, min(100, rem.total_seconds() / (30 * 86400) * 100)))
        cards.append(f"""
<div class="card">
  <div class="top"><span class="dot" style="background:{color[c.status]}"></span>
    <b>{html.escape(c.name)}</b><span class="tag">{STATUS_LABEL[c.status]}</span></div>
  <div class="acct">{html.escape(c.account or '未知账号')} · {html.escape(c.kind or '')}</div>
  <div class="bar"><div style="width:{pct:.0f}%;background:{color[c.status]}"></div></div>
  <div class="rem">{html.escape(d['remaining_human'])}{' · 到期 ' + c.expires_at.astimezone().strftime('%Y-%m-%d %H:%M') if c.expires_at else ''}</div>
  <div class="meta">{'🤖 可自动续期' if c.auto_renewable else '🙋 需人工续期'}</div>
  {f'<code>{html.escape(c.renew_hint)}</code>' if c.renew_hint and c.status != 'ok' else ''}
  {f'<div class="detail">{html.escape(c.detail)}</div>' if c.detail else ''}
</div>""")
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="300"><title>credwatch 凭证看板</title>
<style>
body{{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f5f5f4;margin:0;padding:32px;color:#1c1917}}
h1{{margin:0 0 4px;font-size:22px}} .head{{font-size:16px;margin:8px 0 4px}} .sub{{color:#78716c;margin-bottom:24px;font-size:13px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px}}
.card{{background:#fff;border-radius:14px;padding:18px;box-shadow:0 1px 3px rgba(0,0,0,.06)}}
.top{{display:flex;align-items:center;gap:8px}} .dot{{width:10px;height:10px;border-radius:50%}}
.tag{{margin-left:auto;font-size:12px;color:#57534e}} .acct{{color:#78716c;font-size:13px;margin:6px 0 12px}}
.bar{{height:8px;background:#e7e5e4;border-radius:4px;overflow:hidden}} .bar div{{height:100%}}
.rem{{font-size:15px;margin-top:10px;font-weight:600}} .meta{{font-size:12px;color:#57534e;margin-top:6px}}
code{{display:block;background:#f5f5f4;padding:6px 8px;border-radius:6px;margin-top:8px;font-size:12px;word-break:break-all}}
.detail{{font-size:12px;color:#a8a29e;margin-top:6px;word-break:break-all}}
</style></head><body><h1>🔐 凭证看板</h1>
<div class="head">{html.escape(headline(creds))}</div>
<div class="sub">最后检查 {now().astimezone().strftime('%Y-%m-%d %H:%M:%S')} · 进度条以 30 天为满格 · 每 5 分钟自动刷新页面</div>
<div class="grid">{''.join(cards) or '<p>没有发现任何授权</p>'}</div></body></html>"""
