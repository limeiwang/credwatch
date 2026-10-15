"""端到端测试：用假的 gh / wrangler 与本地假 GitHub API，不碰真实账号。"""
import datetime as dt, json, os, stat, sys, threading, tempfile, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
TMP = Path(tempfile.mkdtemp())
os.environ["CREDWATCH_HOME"] = str(TMP / "home")
os.environ["HOME"] = str(TMP / "user")
for v in ("GITHUB_TOKEN", "GH_TOKEN", "CLOUDFLARE_API_TOKEN", "CF_API_TOKEN", "XDG_CONFIG_HOME"):
    os.environ.pop(v, None)
EXP = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S UTC")

class FakeGH(BaseHTTPRequestHandler):
    def do_GET(self):
        tok = self.headers.get("Authorization", "").split()[-1]
        if tok.startswith("ghp_dead"):
            self.send_response(401); self.end_headers(); self.wfile.write(b"{}"); return
        self.send_response(200)
        self.send_header("x-oauth-scopes", "repo, workflow")
        if tok == "ghp_expiring":
            self.send_header("github-authentication-token-expiration", EXP)
        self.end_headers(); self.wfile.write(json.dumps({"login": "meiwang"}).encode())
    def log_message(self, *a): pass

srv = HTTPServer(("127.0.0.1", 0), FakeGH)
threading.Thread(target=srv.serve_forever, daemon=True).start()
os.environ["CREDWATCH_GITHUB_API"] = f"http://127.0.0.1:{srv.server_port}"

# 假 gh 与 wrangler
BIN = TMP / "bin"; BIN.mkdir()
(BIN / "gh").write_text("#!/bin/sh\nif [ \"$1\" = api ]; then printf 'HTTP/2.0 200 OK\\r\\nX-Oauth-Scopes: repo, gist\\r\\n\\r\\n{\"login\":\"viagh\"}'; exit 0; fi\nif [ \"$2\" = status ]; then echo 'github.com'; echo '  ✓ Logged in to github.com account meiwang'; exit 0; fi\nif [ -n \"$FAKE_KEYCHAIN_FAIL\" ]; then exit 1; fi\necho gho_valid\n")
(BIN / "wrangler").write_text(f"#!/bin/sh\npython3 -c \"import pathlib,datetime as d;p=pathlib.Path('{TMP}/user/.config/.wrangler/config/default.toml');t=(d.datetime.now(d.timezone.utc)+d.timedelta(hours=1)).isoformat();p.write_text('oauth_token=\\\"new\\\"\\nrefresh_token=\\\"r\\\"\\nexpiration_time=\\\"'+t+'\\\"\\n')\"\necho 'You are logged in'\n")
for f in BIN.iterdir(): f.chmod(f.stat().st_mode | stat.S_IEXEC)
os.environ["PATH"] = f"{BIN}:{os.environ['PATH']}"
WCFG = TMP / "user/.config/.wrangler/config/default.toml"; WCFG.parent.mkdir(parents=True)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from credwatch import engine, report, mcp_server  # noqa
from credwatch.core import EXPIRED, EXPIRING, OK  # noqa

def by(creds, name): return next(c for c in creds if c.name == name)

class T(unittest.TestCase):
    def setUp(self):
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).isoformat()
        WCFG.write_text(f'oauth_token="a"\nrefresh_token="r"\nexpiration_time="{past}"\nscopes=["workers:write"]\n')
        for v in ("GITHUB_TOKEN", "GH_TOKEN"): os.environ.pop(v, None)
        engine.STATE.unlink(missing_ok=True)

    def test_github_gh_ok(self):
        c = by(engine.check_all(), "gh CLI (github.com)")
        self.assertEqual((c.status, c.account, c.scopes), (OK, "meiwang", ["repo", "workflow"]))

    def test_github_pat_expiring_and_dead(self):
        os.environ["GITHUB_TOKEN"] = "ghp_expiring"; os.environ["GH_TOKEN"] = "ghp_dead"
        creds = engine.check_all()
        self.assertEqual(by(creds, "环境变量 GITHUB_TOKEN").status, EXPIRING)
        self.assertEqual(by(creds, "环境变量 GH_TOKEN").status, EXPIRED)
        self.assertEqual(creds[0].status, EXPIRED)  # 最严重的排最前

    def test_wrangler_refreshable_then_renew(self):
        c = by(engine.check_all(), "wrangler CLI")
        self.assertTrue(c.auto_renewable); self.assertEqual(c.status, OK)
        ok, msg = engine.renew(c); self.assertTrue(ok, msg)
        c2 = by(engine.check_all(), "wrangler CLI")
        self.assertIn("refresh_token", c2.detail)

    def test_wrangler_no_refresh_is_expired(self):
        past = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).isoformat()
        WCFG.write_text(f'oauth_token="a"\nexpiration_time="{past}"\n')
        self.assertEqual(by(engine.check_all(), "wrangler CLI").status, EXPIRED)

    def test_custom_and_alert_dedup(self):
        engine.CONFIG.parent.mkdir(parents=True, exist_ok=True)
        soon = (dt.date.today() + dt.timedelta(days=2)).isoformat()
        engine.CONFIG.write_text(f'[general]\nwarn_days=7\nplatforms=["github"]\n[[custom]]\nname="OpenAI Key"\nexpires_at="{soon}"\nrenew="轮换"\n[[custom]]\nname="Broken CLI"\ncheck="false"\n')
        r1 = engine.cycle(send=False)
        self.assertEqual({c.name for c in r1["alerts"]}, {"OpenAI Key", "Broken CLI"})
        self.assertEqual(engine.cycle(send=False)["alerts"], [])  # 24h 内不重复提醒
        engine.CONFIG.unlink()

    def test_reports_and_mcp(self):
        creds = engine.check_all()
        self.assertIn("gh CLI", report.terminal(creds))
        self.assertIn("<html", report.html_page(creds))
        init = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(init["result"]["serverInfo"]["name"], "credwatch")
        tools = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(tools["result"]["tools"]), 4)
        res = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                                 "params": {"name": "check_credentials", "arguments": {}}})
        self.assertIn("wrangler CLI", res["result"]["content"][0]["text"])
        rn = mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                                "params": {"name": "renew_credential", "arguments": {"id": "github:gh CLI (github.com)"}}})
        self.assertTrue(json.loads(rn["result"]["content"][0]["text"])["needs_user"])

    def test_keychain_glitch_is_not_expired(self):
        os.environ["FAKE_KEYCHAIN_FAIL"] = "1"
        try:
            c = by(engine.check_all(), "gh CLI (github.com)")
        finally:
            os.environ.pop("FAKE_KEYCHAIN_FAIL")
        self.assertEqual(c.status, "unknown")  # 已登录但读钥匙串失败 → 不误报过期

    def test_python_network_blocked_falls_back_to_gh(self):
        from credwatch.adapters import github as gha
        old = gha.API; gha.API = "http://127.0.0.1:9"  # 模拟 Python 直连 GitHub 超时（国内网络常见）
        try:
            c = by(engine.check_all(), "gh CLI (github.com)")
        finally:
            gha.API = old
        self.assertEqual((c.status, c.account, c.scopes), (OK, "viagh", ["repo", "gist"]))

    def test_preflight(self):
        os.environ["GITHUB_TOKEN"] = "ghp_expiring"  # 剩 3 天
        from credwatch.core import parse_duration
        self.assertTrue(engine.preflight(parse_duration("2h"), ["github"])["go"])
        res = engine.preflight(parse_duration("4d"), ["github"])
        self.assertFalse(res["go"]); self.assertEqual(res["blockers"][0]["id"], "github:环境变量 GITHUB_TOKEN")

    def test_statusline(self):
        os.environ["GH_TOKEN"] = "ghp_dead"
        engine.CACHE.unlink(missing_ok=True)
        line = report.statusline(engine.cached_check())
        self.assertIn("🔴", line); self.assertIn("GH_TOKEN", line)
        engine.CACHE.unlink(missing_ok=True)

    def test_escalating_reminders(self):
        engine.CONFIG.parent.mkdir(parents=True, exist_ok=True)
        def cfg(days):
            exp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=days)).isoformat()
            engine.CONFIG.write_text(f'[general]\nplatforms=[]\n[notify]\nremind_days=[7,3,1]\n[[custom]]\nname="K"\nexpires_at="{exp}"\n')
        cfg(6); self.assertEqual(len(engine.cycle(send=False)["alerts"]), 1)
        cfg(5); self.assertEqual(len(engine.cycle(send=False)["alerts"]), 0)   # 同一档位不重复
        cfg(2.5); self.assertEqual(len(engine.cycle(send=False)["alerts"]), 1) # 跌破 3 天档再提醒
        engine.CONFIG.unlink()

    def test_doctor_finds_stale_git_rewrite(self):
        import subprocess
        subprocess.run(["git", "config", "--global", "url.https://x-access-token:ghp_dead0000000000000000000000@github.com/.insteadOf",
                        "https://github.com/"], check=True)
        from credwatch.doctor import diagnose, fix, public
        f = public(diagnose())
        hit = [x for x in f if "写死了令牌" in x["title"]]
        self.assertTrue(hit); self.assertEqual(hit[0]["level"], "error"); self.assertIn("doctor --fix", hit[0]["fix"])
        self.assertNotIn("ghp_dead0000000000000000000000", json.dumps(f))  # 令牌不明文输出
        self.assertTrue(any("已修复" in x for x in fix(diagnose())))
        self.assertFalse([x for x in public(diagnose()) if "写死了令牌" in x["title"]])

    def test_wrangler_network_failure_not_logout(self):
        from credwatch.adapters import cloudflare
        (BIN / "wrangler.bak").write_bytes((BIN / "wrangler").read_bytes())
        (BIN / "wrangler").write_text("#!/bin/sh\necho 'fetch failed: getaddrinfo ENOTFOUND dash.cloudflare.com' >&2\nexit 1\n")
        try:
            ok, msg = engine.renew(by(engine.check_all(), "wrangler CLI"))
        finally:
            (BIN / "wrangler").write_bytes((BIN / "wrangler.bak").read_bytes())
        self.assertFalse(ok); self.assertIn("不用重新登录", msg)

if __name__ == "__main__":
    unittest.main(verbosity=2)
