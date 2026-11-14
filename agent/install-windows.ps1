# Installs the assistant's PC agent on Windows. The dashboard gives you the exact command:
#   $env:HUB='https://assistant.your-tailnet.ts.net'; $env:PAIR='123456'; irm https://raw.githubusercontent.com/Sh4dwDev/claude-assistant/main/agent/install-windows.ps1 | iex
# No admin rights needed. Run the same command again to update.

function Install-ClaudeAgent {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    function Say($t) { Write-Host "`n==> $t" -ForegroundColor Magenta }
    function Info($t) { Write-Host "    $t" }

    $Repo = 'https://github.com/Sh4dwDev/claude-assistant/archive/refs/heads/main.zip'
    $Dir = Join-Path $env:LOCALAPPDATA 'ClaudeAgent'
    $App = Join-Path $Dir 'app'
    $Venv = Join-Path $Dir 'venv'
    $Role = if ($env:ROLE) { $env:ROLE } else { 'pc' }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null

    # --- 1. Python ---------------------------------------------------------------------
    function Find-Python {
        $q = "import sys;print(sys.executable if sys.version_info>=(3,10) else '')"
        $cands = @()
        try { $p = & py -3 -c $q 2>$null; if ($p) { $cands += "$p".Trim() } } catch {}
        try { $p = & python -c $q 2>$null; if ($p) { $cands += "$p".Trim() } } catch {}
        $cands += Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | ForEach-Object FullName
        foreach ($p in $cands) { if ($p -and (Test-Path $p) -and ($p -notlike '*WindowsApps*')) { return $p } }
        return $null
    }
    $Py = Find-Python
    if (-not $Py) {
        Say 'Installing Python (one time)'
        winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
        $Py = Find-Python
        if (-not $Py) { throw 'Python install failed. Install Python 3.12 from python.org, then run this command again.' }
    }
    Info "Python: $Py"

    # --- 2. Code -------------------------------------------------------------------------------
    Say 'Downloading the agent'
    $zip = Join-Path $env:TEMP 'claude-assistant.zip'
    $tmp = Join-Path $env:TEMP 'claude-assistant-src'
    Invoke-WebRequest -UseBasicParsing $Repo -OutFile $zip
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    Expand-Archive $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Select-Object -First 1

    # stop a running agent before replacing its files
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -like '*ClaudeAgent*agent.py*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Remove-Item -Recurse -Force $App -ErrorAction SilentlyContinue
    Copy-Item -Recurse (Join-Path $src.FullName 'agent') $App
    Remove-Item -Recurse -Force $tmp, $zip -ErrorAction SilentlyContinue

    # --- 3. Libraries ---------------------------------------------------------------------------------
    Say 'Installing libraries'
    if (-not (Test-Path "$Venv\Scripts\python.exe")) { & $Py -m venv $Venv }
    & "$Venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r "$App\requirements.txt"
    if ($LASTEXITCODE -ne 0) { throw 'Installing libraries failed. See the messages above.' }

    # --- 4. Pair with the hub ------------------------------------------------------------------------------
    $cfg = Join-Path $Dir 'config.json'
    if ($env:PAIR -or -not (Test-Path $cfg)) {
        $Hub = $env:HUB; $Code = $env:PAIR
        if (-not $Hub) { $Hub = Read-Host 'Hub address (shown in the dashboard)' }
        if (-not $Code) { $Code = Read-Host 'Pairing code (shown in the dashboard)' }
        Say 'Pairing with your hub'
        & "$Venv\Scripts\python.exe" "$App\agent.py" pair --hub $Hub --code $Code --role $Role --name $env:COMPUTERNAME
        if ($LASTEXITCODE -ne 0) { throw 'Pairing failed. Make a new code in the dashboard and run the command again.' }
    }

    # --- 5. Start at login, and start now ---------------------------------------------------------------------
    Say 'Starting the agent (and at every login)'
    $startup = [Environment]::GetFolderPath('Startup')
    $lnk = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $startup 'Claude Agent.lnk'))
    $lnk.TargetPath = "$Venv\Scripts\pythonw.exe"
    $lnk.Arguments = "`"$App\agent.py`" run"
    $lnk.WorkingDirectory = $App
    $lnk.Description = 'Assistant PC agent'
    $lnk.Save()
    Start-Process -FilePath "$Venv\Scripts\pythonw.exe" -ArgumentList "`"$App\agent.py`" run" -WorkingDirectory $App -WindowStyle Hidden

    # --- 6. Claude Code and Git (for coding jobs) -------------------------------------------------------------
    $claude = (Get-Command claude -ErrorAction SilentlyContinue) -or (Test-Path "$env:USERPROFILE\.local\bin\claude.exe")
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        $a = Read-Host "`nCoding jobs need Git. Install it now? (Y/n)"
        if ($a -notmatch '^[nN]') { winget install -e --id Git.Git --silent --accept-package-agreements --accept-source-agreements | Out-Null }
    }
    if (-not $claude) {
        $a = Read-Host "`nCoding jobs need Claude Code (uses your Claude Max plan). Install it now? (Y/n)"
        if ($a -notmatch '^[nN]') {
            Invoke-RestMethod https://claude.ai/install.ps1 | Invoke-Expression
            Info 'Now open a new terminal, type  claude  and sign in with your Claude account once.'
        }
    }

    Say 'Done.'
    Info 'The PC should show as online in the dashboard within a few seconds.'
    Info "Logs: $Dir\agent.log"
    Info 'For waking this PC remotely, turn on Wake-on-LAN (see the README, "Wake-on-LAN").'
}

Install-ClaudeAgent
