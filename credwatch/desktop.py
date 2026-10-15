"""电脑系统通知：零配置的默认提醒渠道（Windows 弹窗 / macOS 通知中心 / Linux notify-send）。"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess

_WIN = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:CW_TITLE)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:CW_BODY)) | Out-Null
$app = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def available() -> bool:
    if os.environ.get("CREDWATCH_DESKTOP") == "0":
        return False
    sysname = platform.system()
    if sysname == "Windows":
        return bool(shutil.which("powershell"))
    if sysname == "Darwin":
        return bool(shutil.which("osascript"))
    return bool(shutil.which("notify-send")) and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def show(title: str, body: str) -> str:
    """弹一条系统通知，返回结果说明。"""
    if not available():
        return "desktop: 跳过（这台机器没有图形界面）"
    body = body[:400]
    sysname = platform.system()
    try:
        if sysname == "Windows":
            p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _WIN],
                               env={**os.environ, "CW_TITLE": title, "CW_BODY": body},
                               capture_output=True, timeout=30, creationflags=0x08000000)
        elif sysname == "Darwin":
            esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')
            p = subprocess.run(["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'],
                               capture_output=True, timeout=30)
        else:
            p = subprocess.run(["notify-send", title, body], capture_output=True, timeout=30)
        return "desktop: 已弹出" if p.returncode == 0 else f"desktop: 失败 {p.stderr.decode(errors='ignore')[:120]}"
    except Exception as e:
        return f"desktop: 失败 {e}"
