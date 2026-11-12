<#
CS2 Map MCP: one-command setup (Windows PowerShell 5.1+). Safe to run again.

  .\setup.ps1                      # check everything and show the plan; changes nothing outside the project
  .\setup.ps1 -Apply               # also install the Python environment and register the MCP server in clients
  .\setup.ps1 -Apply -Clients codex,claude-desktop
  (blocked by the execution policy? powershell -ExecutionPolicy Bypass -File .\setup.ps1 ... - this run only)

Steps: Python environment from config/requirements.lock.txt (offline wheels in tools/wheels when present);
CS2 + Workshop Tools (config/settings.json); Blender (settings -> Steam libraries -> tools/ -> Program Files;
downloaded only when missing and only after you agree); Source2Viewer CLI (same rule); Node.js (optional);
MCP registration for Claude Code (.mcp.json), Claude Desktop and Codex CLI/desktop (~/.codex/config.toml),
each user config backed up before it is written; the local layer my/. Finally the server's own `status`.
#>
param(
    [switch]$Apply,
    [string[]]$Clients = @('claude-code', 'claude-desktop', 'codex')
)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Py = Join-Path $Root 'tools\python-env\Scripts\python.exe'
$Sources = Get-Content (Join-Path $Root 'config\tool-sources.json') -Raw | ConvertFrom-Json
function Say($text, $color = 'Gray') { Write-Host $text -ForegroundColor $color }
function Ask($question) {
    $answer = Read-Host "$question [y/N]"
    return ($answer -eq 'y' -or $answer -eq 'Y')
}
function Get-Archive($id) {
    $item = $Sources.archives | Where-Object { $_.id -eq $id }
    if (-not $item) { throw "no source for $id in config/tool-sources.json" }
    $zip = Join-Path $Root $item.archive
    if (-not (Test-Path $zip)) {
        New-Item -ItemType Directory -Force (Split-Path $zip) | Out-Null
        Say "  downloading $($item.origin)"
        Invoke-WebRequest -Uri $item.origin -OutFile $zip -UseBasicParsing
    }
    $hash = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    if ($hash -ne $item.sha256) { Remove-Item $zip; throw "sha256 mismatch for $zip (removed; run setup again)" }
    Expand-Archive -Path $zip -DestinationPath (Join-Path $Root $item.extract_to) -Force
    Say "  installed $id $($item.version)" 'Green'
}

Say "== CS2 Map MCP setup ($Root)" 'Cyan'
if (-not $Apply) { Say 'Plan mode: nothing outside the project is changed. Run with -Apply to install and register.' 'Yellow' }

# 1. Python environment ----------------------------------------------------------------------------------------
Say '1. Python environment'
$pyOk = $false
if (Test-Path $Py) {
    & $Py -c "import mcp, PIL, numpy, psutil" 2>$null
    if ($LASTEXITCODE -eq 0) { $pyOk = $true }
}
if ($pyOk) { Say '  ok: tools\python-env' 'Green' }
else {
    $base = Join-Path $Root 'tools\python-3.14.4\python.exe'
    if (-not (Test-Path $base)) { $base = (Get-Command py -ErrorAction SilentlyContinue).Source }
    if (-not $base) { Say '  missing: install Python 3.14 (python.org) and run setup again' 'Red'; exit 1 }
    if (-not $Apply) { Say "  would create tools\python-env from $base and install config\requirements.lock.txt" 'Yellow' }
    else {
        if ($base -like '*py.exe') { & $base -3.14 -m venv (Join-Path $Root 'tools\python-env') } else { & $base -m venv (Join-Path $Root 'tools\python-env') }
        $wheels = Join-Path $Root 'tools\wheels'
        $lock = Join-Path $Root 'config\requirements.lock.txt'
        if (Test-Path $wheels) { & $Py -m pip install --no-index --find-links $wheels --require-hashes -r $lock }
        else { & $Py -m pip install --require-hashes -r $lock }
        if ($LASTEXITCODE -ne 0) { Say '  pip install failed' 'Red'; exit 1 }
        Say '  installed tools\python-env' 'Green'
    }
}
if (-not (Test-Path $Py)) { Say 'Run again with -Apply to create the Python environment, then the remaining checks run.' 'Yellow'; exit 0 }
$env:PYTHONPATH = Join-Path $Root 'src'
$env:PYTHONUTF8 = '1'

# 2. CS2 and Workshop Tools --------------------------------------------------------------------------------------
Say '2. CS2 + Workshop Tools'
$settings = Join-Path $Root 'config\settings.json'
$cs2 = & $Py -c "from cs2map.config import load_settings; s=load_settings(); print(s.cs2_path if s.resourcecompiler.exists() else '')" 2>$null
if ($cs2) {
    Say "  ok: $cs2" 'Green'
    if (-not (Test-Path $settings)) {
        if ($Apply) {
            $json = @{ cs2_path = "$cs2"; addon = 'mcp_lab'; compile_threads = 0 } | ConvertTo-Json
            Set-Content -Path $settings -Value $json -Encoding UTF8
            Say '  wrote config\settings.json' 'Green'
        } else { Say '  would write config\settings.json' 'Yellow' }
    }
} else { Say '  CS2 with Workshop Tools not found: install CS2 and "CS2 Workshop Tools" (Steam -> Library -> Tools), or set cs2_path in config\settings.json' 'Red' }
# Asked once per install (the answer is kept in settings.json; change it there any time).
$restart = & $Py -c "import json,pathlib; p=pathlib.Path(r'$settings'); d=json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}; print('' if 'restart_hung_cs2' not in d else ('off' if not d['restart_hung_cs2'] else 'on, also user-started' if d.get('restart_hung_cs2_user') else 'on'))" 2>$null
if ($restart) { Say "  restart of a hung CS2 the MCP launched: $restart (restart_hung_cs2 in config\settings.json)" 'Green' }
elseif ($Apply -and (Test-Path $settings)) {
    Say '  When CS2 Workshop Tools started by the MCP (game_launch) stays "Not responding" for 3 min, the MCP can'
    Say '  kill it, start it again with your addon, reload the map and retry the call. A CS2 you started is never touched.'
    $on = Ask '  Allow this (recommended for long unattended sessions)?'
    $flag = if ($on) { 'True' } else { 'False' }
    $userFlag = 'False'
    if ($on) {
        Say '  Also a Workshop Tools session YOU started (Steam -> CS2 Workshop Tools, addon from settings)? Useful when'
        Say '  you start CS2 yourself and leave; refused while Hammer has unsaved edits; a normal game is never touched.'
        if (Ask '  Allow restarting a hung Workshop Tools you started?') { $userFlag = 'True' }
    }
    & $Py -c "from cs2map.config import update_settings, RESTART_HUNG_DEFAULT; update_settings({'restart_hung_cs2': $flag, 'restart_hung_cs2_seconds': RESTART_HUNG_DEFAULT, 'restart_hung_cs2_user': $userFlag})"
    Say "  restart_hung_cs2 = $($flag.ToLower()), restart_hung_cs2_user = $($userFlag.ToLower()) in config\settings.json" 'Green'
} else { Say '  would ask whether the MCP may restart a hung CS2 it launched (restart_hung_cs2)' 'Yellow' }
# Asked once per install as well.
$focus = & $Py -c "import json,pathlib; p=pathlib.Path(r'$settings'); d=json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}; print(d.get('game_focus', ''))" 2>$null
if ($focus) { Say "  CS2 window focus: $focus (game_focus in config\settings.json)" 'Green' }
elseif ($Apply -and (Test-Path $settings)) {
    Say '  CS2 works behind your windows: frames, map loads and tests do not take the focus. Only a test step with the'
    Say '  real mouse (rare) brings the game window to the front for under a second and gives the focus back.'
    Say '  The game sound stays as it is: mute Counter-Strike 2 in the Windows volume mixer if it disturbs you.'
    $never = Ask '  Never let the CS2 window come to the front (mouse steps are emulated, HUD clicks refused)?'
    $value = if ($never) { 'never' } else { 'input_only' }
    & $Py -c "from cs2map.config import update_settings; update_settings({'game_focus': '$value'})"
    Say "  game_focus = $value in config\settings.json" 'Green'
} else { Say '  would ask when the CS2 window may take the focus (game_focus)' 'Yellow' }
# Asked once per install as well: the status page.
$panel = & $Py -c "import json,pathlib; p=pathlib.Path(r'$settings'); d=json.loads(p.read_text(encoding='utf-8-sig')) if p.is_file() else {}; print('' if 'panel' not in d else ('on, http://127.0.0.1:%d/' % d.get('panel_port', 8717) if d['panel'] else 'off'))" 2>$null
if ($panel) { Say "  status page (panel): $panel (panel in config\settings.json)" 'Green' }
elseif ($Apply -and (Test-Path $settings)) {
    Say '  The panel is a page in your browser (this PC only, http://127.0.0.1:8717/): what the tools are doing now,'
    Say '  the newest frames, the load of the PC, and switches for what the AI may do without asking (take the focus'
    Say '  for mouse steps, start long light bakes). The MCP server starts it; nothing is sent anywhere.'
    $on = Ask '  Turn the panel on?'
    $flag = if ($on) { 'True' } else { 'False' }
    & $Py -c "from cs2map.config import update_settings; update_settings({'panel': $flag})"
    Say "  panel = $($flag.ToLower()) in config\settings.json$(if ($on) { '; open http://127.0.0.1:8717/ once an AI client runs' })" 'Green'
} else { Say '  would ask whether to turn on the status page (panel)' 'Yellow' }

# 3. Blender ----------------------------------------------------------------------------------------------------
Say '3. Blender'
$blender = & $Py -c "from cs2map.config import load_settings, blender_version, BLENDER_SUPPORTED as S; b=load_settings().blender_exe; v=blender_version(b) if b.exists() else None; print(f'{b}|{v[0]}.{v[1]}|{int(S[0] <= v <= S[1])}' if v else (f'{b}||0' if b.exists() else ''))" 2>$null
$bParts = if ($blender) { $blender.Split('|') } else { @() }
if ($bParts.Count -eq 3 -and $bParts[2] -eq '1') { Say "  ok: $($bParts[0])  version $($bParts[1])" 'Green' }
else {
    if ($bParts.Count -eq 3) { Say "  $($bParts[0]) (version $($bParts[1])) is not a tested version (4.5 - 5.x)" 'Yellow' }
    else { Say '  not found (settings, Steam libraries, tools\, Program Files)' 'Yellow' }
    Say '  recommended: Blender from Steam (free, app 365670); setup finds it in any Steam library' 'Yellow'
    if ($Apply -and (Ask '  Open the Steam install dialog for Blender now?')) {
        Start-Process 'steam://install/365670'; Say '  finish the install in Steam, then run setup again' 'Yellow'
    } elseif ($Apply -and (Ask '  Or download portable Blender 4.5 LTS (~400 MB) from download.blender.org into tools\?')) { Get-Archive 'blender' }
    else { Say '  or set "blender" in config\settings.json to a Blender 4.5 - 5.x' 'Yellow' }
}

# 4. Source2Viewer CLI (stock prop previews) ---------------------------------------------------------------------
Say '4. Source2Viewer CLI'
if (Test-Path (Join-Path $Root 'tools\vrf-cli-20.1\Source2Viewer-CLI.exe')) { Say '  ok: tools\vrf-cli-20.1' 'Green' }
else {
    Say '  not found (asset_search kind=prop previews need it)' 'Yellow'
    if ($Apply -and (Ask '  Download Source2Viewer CLI 20.1 (~53 MB) from GitHub?')) { Get-Archive 'vrf-cli' }
}

# 5. Node.js (optional: script syntax checks, mechanics tests) ---------------------------------------------------
Say '5. Node.js'
$node = Get-Command node -ErrorAction SilentlyContinue
if ($node) { Say "  ok: $($node.Source)" 'Green' } else { Say '  optional, not found: script_write skips the syntax check (nodejs.org)' 'Yellow' }

# 6. MCP registration -------------------------------------------------------------------------------------------
Say '6. MCP clients'
$clientArgs = @((Join-Path $Root 'tools\setup_clients.py'), '--clients', ($Clients -join ','))
if ($Apply) { $clientArgs += "--apply" }
& $Py @clientArgs

# 7. Local layer my/ (notes, styles, recipes and inbox of this computer; never in git) ----------------------------
Say '7. Local layer my/'
if ($Apply) {
    $myDir = & $Py -c "from cs2map import mylayer; print(mylayer.ensure())"
    Say "  ok: $myDir (put your photos, models and sounds into its inbox folder)" 'Green'
} else { Say '  would create my/notes, my/styles, my/recipes, my/inbox, my/maps' 'Yellow' }

# 8. Self-check -------------------------------------------------------------------------------------------------
Say '8. Server status'
& $Py (Join-Path $Root 'tools\mcp_call.py') status '{}' | Select-String -Pattern '"found"|"version"|"workshop_tools"|"responding"|"hint"|"node"|"source2viewer"'
Say 'Done. Restart the AI client(s) to load cs2map; in a chat start with: status, then read_guide.' 'Cyan'
