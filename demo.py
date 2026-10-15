"""用模拟数据生成看板截图素材（不访问真实账号）。"""
import datetime as dt, sys
from credwatch.core import Credential, OK, EXPIRING, EXPIRED, now
from credwatch import report
d = dt.timedelta
creds = [
 Credential("github","环境变量 GITHUB_TOKEN","meiwang","fine-grained PAT",now()-d(hours=3),EXPIRED,["repo"],False,"到 https://github.com/settings/tokens 重新生成后更新该环境变量","令牌已失效或被撤销 (401)"),
 Credential("custom","OpenAI Key","","custom",now()+d(days=2,hours=4),EXPIRING,[],False,"到控制台轮换后更新 .env"),
 Credential("cloudflare","wrangler CLI","meiwang@cf","OAuth",None,OK,["workers:write"],True,"wrangler login","access token 有效，refresh_token 可自动续"),
 Credential("cloudflare","环境变量 CLOUDFLARE_API_TOKEN","","API Token",now()+d(days=21),OK,[],False,"轮换令牌"),
 Credential("github","gh CLI (github.com)","meiwang","OAuth (gh CLI)",None,OK,["repo","workflow"],False,"gh auth login","该令牌未设置过期时间"),
]
for c in creds: c.apply_threshold(d(days=7))
if sys.argv[1:] == ["html"]: print(report.html_page(creds))
else: print(report.terminal(creds))
