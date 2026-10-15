# 用户与需求调研（2026-10）

## 目标用户
1. 在 VPS / 远程机 / 容器里跑 Claude Code、Codex、Cursor agent 的独立开发者（核心）
2. 用 GitHub Actions + PAT / App token 跑长任务的团队
3. 从远程机部署 Cloudflare Workers 的开发者

## 真实痛点（证据）
- 长任务中途授权过期，多 agent 一起挂掉、留下半截改动：anthropics/claude-code #37468、#12447；claude-code-action #716（GitHub App token 固定 1 小时）
- 误报：钥匙串超时、偶发 401 被当成“过期”，让用户白白重新登录：claude-code #67055、#67087；wrangler 把断网误报为登录过期：workers-sdk #15886
- 重新登录了仍失败：git 全局 url 重写里写死了旧令牌（Cursor 论坛 2026-07）；GH_TOKEN 覆盖 gh 登录
- 远程机登录难：wrangler 回调 localhost:8976 打不开（workers-sdk #2874、#5679），现有 wrangler login --device
- PAT 到期无感知：gh CLI 团队拒绝做到期提醒（cli/cli #11934）；很多人手写 workflow 每周探测 PAT
- 有人每天被迫重新登录 wrangler（answeroverflow 2026）

## 竞品
- TokenTimer、ExpiryGuard、Infrawrench Expiry Radar、SE Validator：面向团队/云端资产，手填或接云密钥库
- 空白点：本机 CLI 登录态 + 面向 agent（MCP、开跑前检查、状态栏），没人做

## 由此做的体验改动（v0.2）
- preflight：长任务开跑前 go/no-go
- 误报治理：重试 + “无法确认”独立状态
- doctor / doctor --fix：旧令牌、环境变量覆盖、明文存储
- 场景化续期指引：远程机自动给设备码登录
- 阶梯提醒 7/3/1 天
- statusline：Claude Code 状态栏常驻显示
