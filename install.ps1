# credwatch Windows 一键安装
# 用法（PowerShell 里粘贴这一行）：
#   irm https://raw.githubusercontent.com/limeiwang/credwatch/main/install.ps1 -OutFile $env:TEMP\credwatch-install.ps1; powershell -NoProfile -ExecutionPolicy Bypass -File $env:TEMP\credwatch-install.ps1
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
    $cands = @(@("py", "-3"), @("python"), @("python3"))
    foreach ($d in @("$env:LOCALAPPDATA\Programs\Python\Python313", "$env:LOCALAPPDATA\Programs\Python\Python312",
                     "$env:LOCALAPPDATA\Programs\Python\Python311", "C:\Program Files\Python313", "C:\Program Files\Python312")) {
        if (Test-Path "$d\python.exe") { $cands += ,@("$d\python.exe") }
    }
    foreach ($c in $cands) {
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
    Warn "没找到 Python 3.11+，正在自动安装 Python 3.12"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Python.Python.3.12 --source winget --scope user --accept-package-agreements --accept-source-agreements --silent
        Refresh-Path
        $py = Find-Python
    }
    if (-not $py) {
        Warn "winget 不可用，改为直接下载官方安装包（先试国内镜像，再试 python.org）"
        $exeFile = Join-Path $env:TEMP "python-3.12.7-amd64.exe"
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        foreach ($u in @("https://registry.npmmirror.com/-/binary/python/3.12.7/python-3.12.7-amd64.exe",
                         "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe")) {
            try {
                Invoke-WebRequest $u -OutFile $exeFile -UseBasicParsing -TimeoutSec 300
                if ((Get-Item $exeFile).Length -gt 10MB) { break }
            } catch { Warn "下载失败：$u" }
        }
        if (Test-Path $exeFile) {
            Write-Host "  ..  正在静默安装 Python（约 1 分钟）"
            Start-Process $exeFile -ArgumentList "/quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1" -Wait
            Refresh-Path
            $py = Find-Python
        }
    }
    if (-not $py) { throw "Python 安装后仍找不到。请到 python.org 安装 3.12，勾选 Add to PATH，再重新运行本脚本。" }
}
$pyArgs = @($py | Select-Object -Skip 1)
Ok ("Python " + (& $py[0] @pyArgs -c "import sys;print(sys.version.split()[0])"))

# ---------- 2. 安装 credwatch ----------
Step 2 "安装 credwatch"
$mirror = "https://pypi.tuna.tsinghua.edu.cn/simple"
& $py[0] @pyArgs -m pip install --user --quiet --upgrade --timeout 20 pipx
if ($LASTEXITCODE -ne 0) {
    Warn "PyPI 连接慢，改用清华镜像"
    & $py[0] @pyArgs -m pip install --user --quiet --upgrade -i $mirror pipx
}
& $py[0] @pyArgs -m pipx ensurepath | Out-Null
$pkg = "https://github.com/limeiwang/credwatch/archive/refs/heads/main.zip"
& $py[0] @pyArgs -m pipx install --force $pkg | Out-Null
if ($LASTEXITCODE -ne 0) {
    & $py[0] @pyArgs -m pipx install --force --pip-args="-i $mirror" $pkg | Out-Null
}
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

# ---------- 6. 设置向导 ----------
if ($env:CI -or -not [Environment]::UserInteractive -or [Console]::IsInputRedirected) {
    Step 6 "当前授权状态"
    & $cw
} else {
    Step 6 "设置向导（每一步都能直接回车跳过）"
    & $cw setup
}
Write-Host "`n全部完成。电脑弹窗提醒已默认开启；以后想改设置，运行：credwatch setup" -ForegroundColor Green
