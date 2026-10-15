# credwatch Windows 一键安装
# 用法（PowerShell 里粘贴这一行）：
#   irm https://raw.githubusercontent.com/limeiwang/credwatch/main/install.ps1 | iex
$ErrorActionPreference = "Continue"  # 原生命令写 stderr 不中断；关键步骤自己检查
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
function Step($n, $msg) { Write-Host "`n[$n/6] $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "  OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

# ---------- 1. Python ----------
Step 1 "检查 Python 3.11+"
function Find-Python {
    foreach ($c in @(@("py", "-3"), @("python"), @("python3"))) {
        $exe = Get-Command $c[0] -ErrorAction SilentlyContinue
        if (-not $exe -or $exe.Source -like "*WindowsApps*") { continue }  # 跳过应用商店占位符
        $args_ = @($c | Select-Object -Skip 1)
        try {
            $v = & $c[0] @args_ -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
            if ($v -and [version]$v -ge [version]"3.11") { return ,$c }
        } catch {}
    }
    return $null
}
$py = Find-Python
if (-not $py) {
    Warn "没找到 Python 3.11+，正在用 winget 安装 Python 3.12（可能弹出确认框）"
    winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements --silent
    Refresh-Path
    $py = Find-Python
    if (-not $py) { throw "Python 安装后仍找不到。请到 python.org 安装 3.12，勾选 Add to PATH，再重新运行本脚本。" }
}
$pyArgs = @($py | Select-Object -Skip 1)
Ok ("Python " + (& $py[0] @pyArgs -c "import sys;print(sys.version.split()[0])"))

# ---------- 2. 安装 credwatch ----------
Step 2 "安装 credwatch"
& $py[0] @pyArgs -m pip install --user --quiet --upgrade pipx
& $py[0] @pyArgs -m pipx ensurepath | Out-Null
& $py[0] @pyArgs -m pipx install --force "https://github.com/limeiwang/credwatch/archive/refs/heads/main.zip" | Out-Null
Refresh-Path
$cw = Join-Path $env:USERPROFILE ".local\bin\credwatch.exe"
if (-not (Test-Path $cw)) {
    $found = Get-Command credwatch -ErrorAction SilentlyContinue
    if ($found) { $cw = $found.Source } else { throw "安装完成但没找到 credwatch.exe" }
}
Ok "已安装：$cw"

# ---------- 3. 配置 ----------
Step 3 "生成配置文件"
& $cw init

# ---------- 4. 接入 Claude Code ----------
Step 4 "接入 Claude Code（MCP + 状态栏）"
if (Get-Command claude -ErrorAction SilentlyContinue) {
    $list = ""
    try { $list = (claude mcp list 2>&1 | Out-String) } catch {}
    if ($list -match "credwatch") { Ok "MCP 已存在，跳过" }
    else { claude mcp add --scope user credwatch -- "$cw" mcp | Out-Null; Ok "已添加 MCP 服务 credwatch" }
} else {
    Warn "没检测到 Claude Code，跳过 MCP。装好 Claude Code 后重新运行本脚本即可。"
}
$claudeDir = Join-Path $env:USERPROFILE ".claude"
$settings = Join-Path $claudeDir "settings.json"
New-Item -ItemType Directory -Force -Path $claudeDir | Out-Null
$cfg = $null
if (Test-Path $settings) {
    try { $cfg = Get-Content $settings -Raw -Encoding UTF8 | ConvertFrom-Json } catch { Warn "settings.json 不是合法 JSON，跳过状态栏配置" ; $cfg = "skip" }
}
if (-not $cfg) { $cfg = [pscustomobject]@{} }
if ($cfg -eq "skip") { } elseif ($cfg.PSObject.Properties.Name -contains "statusLine") {
    Warn "你已经有自定义状态栏，没有覆盖。想换成 credwatch，把 statusLine.command 改成：credwatch statusline"
} else {
    if (Test-Path $settings) { Copy-Item $settings "$settings.bak" -Force }
    $cfg | Add-Member -NotePropertyName statusLine -NotePropertyValue ([pscustomobject]@{ type = "command"; command = "`"$cw`" statusline" })
    $json = $cfg | ConvertTo-Json -Depth 20
    [System.IO.File]::WriteAllText($settings, $json, (New-Object System.Text.UTF8Encoding($false)))
    Ok "状态栏已配置（原文件备份为 settings.json.bak）"
}

# ---------- 5. 后台巡检 ----------
Step 5 "设置每 30 分钟后台巡检（不弹窗）"
$home_ = Join-Path $env:USERPROFILE ".credwatch"
New-Item -ItemType Directory -Force -Path $home_ | Out-Null
$vbs = Join-Path $home_ "run-hidden.vbs"
"CreateObject(""WScript.Shell"").Run """"""$cw"""" watch --once"", 0, False" | Set-Content -Path $vbs -Encoding ASCII
schtasks /create /f /tn "credwatch" /sc minute /mo 30 /tr "wscript.exe `"$vbs`"" | Out-Null
Ok "计划任务 credwatch 已创建（任务计划程序里可以看到）"

# ---------- 6. 看一眼 ----------
Step 6 "当前授权状态"
& $cw
Write-Host "`n全部完成。常用：credwatch dashboard --open（网页看板）、credwatch preflight --for 3h、credwatch doctor --fix" -ForegroundColor Green
Write-Host "要接收飞书/钉钉提醒：用记事本打开 $home_\config.toml 填 webhook，再运行 credwatch test-notify" -ForegroundColor Green
