# credwatch · AI bot 的 CLI 授权看板

像看 Token 余量一样，看清 bot 机器上每个 CLI 授权还剩多久；能续的让 AI 自动续，续不了的提前通知你。

零依赖，Python 3.11+。

## 安装
```bash
pip install .          # 或：pipx install .
credwatch              # 查看状态
```

## 现在支持
| 平台 | 检测内容 | 到期时间 | 自动续期 |
|---|---|---|---|
| GitHub · gh CLI | 登录态、账号、scopes，实测令牌是否有效 | 读 API 返回的过期时间（PAT） | 否，失效时给出 `gh auth login` |
| GitHub · `GITHUB_TOKEN`/`GH_TOKEN` | 同上，识别 classic / fine-grained PAT | 是 | 否（PAT 只能网页重建） |
| Cloudflare · wrangler | 本地 OAuth 配置、refresh_token、scopes | access token 自动刷新 | 是，`wrangler whoami` 触发刷新并保活 |
| Cloudflare · `CLOUDFLARE_API_TOKEN` | 调官方 verify 接口 | 是（`expires_on`） | 否 |
| 任意 CLI（自定义） | 自定义检查命令 / 手填到期日 | 可选 | 可选（配置 `renew` + `auto=true`） |

## 常用命令
```bash
credwatch status            # 终端看板；--json 给脚本/agent 用
credwatch renew             # 续期所有可自动续期的凭证
credwatch renew "wrangler CLI"
credwatch dashboard --open  # 生成 HTML 看板
credwatch watch             # 常驻：每小时检查 → 自动续期 → 推送告警
credwatch watch --once      # 跑一轮，适合 cron
credwatch preflight --for 3h   # 长任务开跑前：授权撑不撑得过 3 小时？能续的先续
credwatch doctor            # 体检：登录了还是 401/403？查写死的旧令牌、被覆盖的登录
credwatch doctor --fix      # 一键修复能修的项
credwatch statusline        # 一行状态，给 Claude Code 状态栏 / shell 提示符
credwatch test-notify       # 测试告警渠道
credwatch init              # 生成 ~/.credwatch/config.toml
```

`status` 有失效/未登录时退出码为 1，可直接放进 CI 或 agent 的前置检查。

## 告警渠道
编辑 `~/.credwatch/config.toml`，填飞书、钉钉、企业微信、Slack、Telegram 或通用 webhook 中任意一个。阶梯提醒：剩 7 天、3 天、1 天各提醒一次，已失效的每 24 小时提醒一次，不刷屏。

## 像 Token 余量一样常驻显示
Claude Code 的 `~/.claude/settings.json`：
```json
{ "statusLine": { "type": "command", "command": "credwatch statusline" } }
```
状态栏会一直显示 `🔐🟢 4 项授权正常`，出问题时变成 `🔐🟡 OpenAI Key 剩 2天3小时`。5 分钟缓存，不拖慢终端。

## 不误报
- 网络抖动、GitHub 偶发 401、钥匙串读取超时都会重试，仍失败则标为“无法确认”，不会叫你白白重新登录
- wrangler 刷新时断网，会提示“登录态没动，下一轮自动重试”，而不是让你重新登录
- 远程/SSH/容器机器自动给出设备码登录方式（`gh auth login`、`wrangler login --device`），不用折腾端口转发

## 让 AI 自己管：MCP
Claude Code：
```bash
claude mcp add credwatch -- credwatch mcp
```
Cursor / 其它客户端（`mcp.json`）：
```json
{ "mcpServers": { "credwatch": { "command": "credwatch", "args": ["mcp"] } } }
```
提供四个工具：
- `preflight`：长任务开跑前调用，返回 go / no-go，能续的先续上
- `check_credentials`：返回所有授权的状态与剩余时间
- `renew_credential`：能续就续；续不了返回 `needs_user: true` 和需要你执行的命令
- `diagnose_auth`：遇到 401/403 但用户说“我登录了”时调用，找隐蔽原因

建议在 `CLAUDE.md` / `AGENTS.md` 里加一句：
> 开始超过 30 分钟的任务前先调用 credwatch 的 preflight；git push 或部署报 401/403 时调用 diagnose_auth，不要反复让用户重新登录。

## 后台常驻
cron（每小时一轮）：
```
0 * * * * credwatch watch --once --html ~/.credwatch/dashboard.html
```
systemd / launchd 直接跑 `credwatch watch` 也可以。

## 安全
- 令牌只在本机读取，只发给对应平台的官方接口，不上传任何第三方
- 不保存令牌副本；`state.json` 只记录告警去重状态；体检结果里的令牌一律打码
- 需要浏览器授权或 2FA 的续期一律交给人，不做绕过

## 测试
```bash
python3 tests/test_credwatch.py   # 用假的 gh / wrangler / GitHub API，不碰真实账号
```
