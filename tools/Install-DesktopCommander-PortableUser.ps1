[CmdletBinding()]
param(
    [string]$TunnelId = "",
    [string]$Alias = "",
    [switch]$PlanOnly
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$DcCommit = "550a0b3e31da18b7cf25e87ed840e3d953b6da42"
$WorkBridgeCommit = "cda1e513b5fe31fc0f5114ff2e3f15ac6733f12e"
$NodeVersion = "24.19.0"
$NodeExeSha256 = "3602f2bb1a10f2cbab4c36886218a33c1ab3db87290e73b033c46c77147d0237"
$TunnelVersion = "v0.0.14"
$TunnelZipSha256 = "784ab8da7b5a88f0109f1fd8aaf0a1c86067430b896dddf307ef7e3cc49fa1a5"

$Bt2 = Join-Path $env:LOCALAPPDATA "BT2"
$DcRoot = Join-Path $Bt2 "DesktopCommanderMCP"
$RtRoot = Join-Path $Bt2 "DesktopCommanderTunnel"
$Bin = Join-Path $RtRoot "bin"
$Profiles = Join-Path $RtRoot "profiles"
$State = Join-Path $RtRoot "state"
$Secrets = Join-Path $RtRoot "secrets"
$KeyFile = Join-Path $Secrets "runtime.key"
$TunnelExe = Join-Path $Bin "tunnel-client.exe"
$ConfigFile = Join-Path $RtRoot "runtime.json"
$ReceiptFile = Join-Path $RtRoot "install-receipt.json"
$StartupFile = Join-Path ([Environment]::GetFolderPath("Startup")) "BT2-DesktopCommander-Tunnel.cmd"
$Tmp = Join-Path $env:TEMP ("bt2-dc-portable-" + [guid]::NewGuid().ToString("N"))
$CurrentPowerShell = [Diagnostics.Process]::GetCurrentProcess().MainModule.FileName

function Sha([string]$p) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash.ToLowerInvariant()
}
function Json([string]$p, [object]$v) {
    [IO.File]::WriteAllText(
        $p,
        (($v | ConvertTo-Json -Depth 20) + [Environment]::NewLine),
        [Text.UTF8Encoding]::new($false)
    )
}
function Wait-Ready([object]$c) {
    $env:TUNNEL_CLIENT_PROFILE_DIR = [string]$c.profile_dir
    $env:TUNNEL_CLIENT_STATE_DIR = [string]$c.state_dir
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $raw = @(& $TunnelExe runtimes status $c.alias --json)
        if ($LASTEXITCODE -eq 0) {
            try {
                $s = (($raw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
                if (
                    $s.process_running -eq $true -and
                    $s.healthy -eq $true -and
                    $s.ready -eq $true -and
                    [string]::IsNullOrWhiteSpace([string]$s.remote_error) -and
                    $null -ne $s.remote -and
                    [string]$s.remote.id -eq [string]$c.tunnel_id
                ) { return $s }
            } catch {}
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Portable tunnel did not become running + healthy + ready with clean remote auth."
}

if ($env:OS -ne "Windows_NT" -or -not [Environment]::Is64BitOperatingSystem) {
    throw "This installer requires 64-bit Windows."
}
if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
    throw "LOCALAPPDATA is unavailable."
}
if ([string]::IsNullOrWhiteSpace($Alias)) {
    $n = (([string]$env:COMPUTERNAME).ToLowerInvariant() -replace '[^a-z0-9._-]','-').Trim('-')
    $Alias = "desktop-$n"
}
if ($Alias -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$') {
    throw "Invalid alias."
}
if ($PlanOnly) {
    [ordered]@{
        schema = "BT2_PORTABLE_DESKTOP_COMMANDER_PLAN_V1"
        admin_required = $false
        git_required = $false
        windows_services_installed = $false
        authority = "CURRENT_WINDOWS_USER"
        root = $RtRoot
        desktop_commander_root = $DcRoot
        alias = $Alias
        desktop_commander_commit = $DcCommit
        workbridge_commit = $WorkBridgeCommit
        tunnel_client_version = $TunnelVersion
    } | ConvertTo-Json -Depth 8
    exit 0
}

if ((Test-Path $DcRoot) -or (Test-Path $RtRoot)) {
    throw "BT2 portable Desktop Commander already has state under $Bt2. Refusing to overwrite it."
}

New-Item -ItemType Directory -Force -Path $Tmp | Out-Null
try {
    Write-Host "=== 1/5 Portable Node $NodeVersion ==="
    $nodeName = "node-v$NodeVersion-win-x64.zip"
    $sums = Join-Path $Tmp "SHASUMS256.txt"
    $nodeZip = Join-Path $Tmp $nodeName
    Invoke-WebRequest -UseBasicParsing "https://nodejs.org/dist/v$NodeVersion/SHASUMS256.txt" -OutFile $sums
    $sumLine = Get-Content $sums | Where-Object { $_ -match ("^[0-9a-fA-F]{64}\s+" + [regex]::Escape($nodeName) + "$") } | Select-Object -First 1
    if (-not $sumLine) { throw "Node archive missing from official SHASUMS256.txt." }
    $nodeZipHash = ($sumLine -split '\s+')[0].ToLowerInvariant()
    Invoke-WebRequest -UseBasicParsing "https://nodejs.org/dist/v$NodeVersion/$nodeName" -OutFile $nodeZip
    if ((Sha $nodeZip) -ne $nodeZipHash) { throw "Node archive hash mismatch." }
    $nodeDir = Join-Path $Tmp "node"
    Expand-Archive $nodeZip $nodeDir -Force
    $nodeHome = (Get-ChildItem $nodeDir -Directory | Select-Object -First 1).FullName
    $node = Join-Path $nodeHome "node.exe"
    $npm = Join-Path $nodeHome "npm.cmd"
    if ((Sha $node) -ne $NodeExeSha256) { throw "Node executable hash mismatch." }

    Write-Host "=== 2/5 Exact Desktop Commander ==="
    $wbZip = Join-Path $Tmp "workbridge.zip"
    $wbDir = Join-Path $Tmp "workbridge"
    Invoke-WebRequest -UseBasicParsing "https://github.com/thebrazenbeard/WorkBridgeMCP/archive/$WorkBridgeCommit.zip" -OutFile $wbZip
    Expand-Archive $wbZip $wbDir -Force
    $wb = (Get-ChildItem $wbDir -Directory | Select-Object -First 1).FullName
    $installer = Join-Path $wb "scripts\Install-DesktopCommanderDuplicate.ps1"
    $probe = Join-Path $wb "scripts\Test-DesktopCommanderDuplicate.mjs"
    & $CurrentPowerShell -NoProfile -ExecutionPolicy Bypass -File $installer -InstallRoot $DcRoot -NodeExe $node -NpmExe $npm
    if ($LASTEXITCODE -ne 0) { throw "Desktop Commander install failed." }

    $manifest = Get-Content -Raw (Join-Path $DcRoot "workbridge-desktop-commander.manifest.json") | ConvertFrom-Json
    if ($manifest.upstream_commit -ne $DcCommit -or $manifest.unrestricted_command_string_shell -ne $true) {
        throw "Desktop Commander manifest qualification failed."
    }
    $dcNode = Join-Path $DcRoot $manifest.node_executable_relative
    $dcEntry = Join-Path $DcRoot $manifest.entrypoint_relative
    $old = $env:DESKTOP_COMMANDER_ROOT
    try {
        $env:DESKTOP_COMMANDER_ROOT = $DcRoot
        & $dcNode $probe
        if ($LASTEXITCODE -ne 0) { throw "Desktop Commander local MCP probe failed." }
    } finally { $env:DESKTOP_COMMANDER_ROOT = $old }

    Write-Host "=== 3/5 OpenAI tunnel-client ==="
    $tz = Join-Path $Tmp "tunnel.zip"
    $te = Join-Path $Tmp "tunnel"
    $url = "https://github.com/openai/tunnel-client/releases/download/$TunnelVersion/tunnel-client-$TunnelVersion-windows-amd64.zip"
    Invoke-WebRequest -UseBasicParsing $url -OutFile $tz
    if ((Sha $tz) -ne $TunnelZipSha256) { throw "tunnel-client archive hash mismatch." }
    Expand-Archive $tz $te -Force
    $tc = Get-ChildItem $te -Recurse -Filter tunnel-client.exe | Select-Object -First 1
    if (-not $tc) { throw "tunnel-client.exe missing." }

    New-Item -ItemType Directory -Force -Path $Bin,$Profiles,$State,$Secrets | Out-Null
    Copy-Item $tc.FullName $TunnelExe
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Secrets /inheritance:r /grant:r "*$sid`:(OI)(CI)F" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not protect runtime key directory." }

    if ([string]::IsNullOrWhiteSpace($TunnelId)) {
        Start-Process "https://platform.openai.com/settings/organization/tunnels"
        Start-Process "https://platform.openai.com/settings/organization/api-keys"
        $TunnelId = Read-Host "Paste the NEW tunnel ID for this laptop"
    }
    if ($TunnelId -notmatch '^tunnel_[0-9a-f]{32}$') { throw "Invalid tunnel ID." }

    Write-Host "=== 4/5 Tunnel credential ==="
    $sec = Read-Host "Paste the NEW restricted runtime key (Tunnels Read + Use)" -AsSecureString
    $ptr = [IntPtr]::Zero
    try {
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt 32 -or $plain -match '\s') { throw "Invalid runtime key." }
        [IO.File]::WriteAllText($KeyFile,$plain.Trim(),[Text.UTF8Encoding]::new($false))
    } finally {
        $plain = $null
        if ($ptr -ne [IntPtr]::Zero) { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }
    }

    $oldControl = $env:CONTROL_PLANE_API_KEY
    $oldAdmin = $env:OPENAI_ADMIN_KEY
    try {
        Remove-Item Env:OPENAI_ADMIN_KEY -ErrorAction SilentlyContinue
        $env:CONTROL_PLANE_API_KEY = [IO.File]::ReadAllText($KeyFile).Trim()
        $remoteRaw = @(& $TunnelExe admin tunnels get $TunnelId --json)
        if ($LASTEXITCODE -ne 0) { throw "Runtime key is not authorized for this tunnel." }
        $remote = (($remoteRaw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    } finally {
        if ($null -eq $oldControl) { Remove-Item Env:CONTROL_PLANE_API_KEY -ErrorAction SilentlyContinue } else { $env:CONTROL_PLANE_API_KEY=$oldControl }
        if ($null -eq $oldAdmin) { Remove-Item Env:OPENAI_ADMIN_KEY -ErrorAction SilentlyContinue } else { $env:OPENAI_ADMIN_KEY=$oldAdmin }
    }

    if ([string]$remote.id -ne $TunnelId) { throw "Tunnel metadata mismatch." }
    if (($dcNode + $dcEntry) -match "['`r`n`0]") { throw "Runtime path contains unsupported quoting characters." }
    $mcp = "'$dcNode' '$dcEntry' '--no-onboarding'"
    $cfg = [ordered]@{
        schema="BT2_PORTABLE_DESKTOP_COMMANDER_RUNTIME_V1"
        alias=$Alias
        tunnel_id=$TunnelId
        tunnel_client=$TunnelExe
        runtime_api_key_file=$KeyFile
        profile_dir=$Profiles
        state_dir=$State
        mcp_command=$mcp
    }
    Json $ConfigFile $cfg

    Write-Host "=== 5/5 Connect and qualify ==="
    $env:TUNNEL_CLIENT_PROFILE_DIR=$Profiles
    $env:TUNNEL_CLIENT_STATE_DIR=$State
    & $TunnelExe runtimes connect --alias $Alias --tunnel-id $TunnelId --runtime-api-key ("file:"+$KeyFile) --profile $Alias --profile-dir $Profiles --mcp-command $mcp | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "tunnel-client connect failed." }
    $status = Wait-Ready ([pscustomobject]$cfg)

    $startPs = Join-Path $RtRoot "start.ps1"
    $start = '$c=Get-Content -Raw "' + $ConfigFile + '"|ConvertFrom-Json;' +
        '$env:TUNNEL_CLIENT_PROFILE_DIR=$c.profile_dir;$env:TUNNEL_CLIENT_STATE_DIR=$c.state_dir;' +
        '& $c.tunnel_client runtimes connect --alias $c.alias --tunnel-id $c.tunnel_id --runtime-api-key ("file:"+$c.runtime_api_key_file) --profile $c.alias --profile-dir $c.profile_dir --mcp-command $c.mcp_command'
    [IO.File]::WriteAllText($startPs,$start,[Text.UTF8Encoding]::new($false))
    $startup = '@echo off' + [Environment]::NewLine + 'start "" /min "' + $CurrentPowerShell + '" -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $startPs + '"'
    [IO.File]::WriteAllText($StartupFile,$startup,[Text.UTF8Encoding]::new($false))

    $receipt=[ordered]@{
        schema="BT2_PORTABLE_DESKTOP_COMMANDER_INSTALL_V1"
        status="PASS"
        observed_at=(Get-Date).ToString("o")
        admin_required=$false
        authority="CURRENT_WINDOWS_USER"
        machine=[string]$env:COMPUTERNAME
        alias=$Alias
        tunnel_id=$TunnelId
        remote_name=[string]$status.remote.name
        remote_error=[string]$status.remote_error
        process_running=[bool]$status.process_running
        healthy=[bool]$status.healthy
        ready=[bool]$status.ready
        desktop_commander_commit=$DcCommit
        unrestricted_command_string_shell=$true
        startup=$StartupFile
        next_gate="CREATE_CHATGPT_TUNNEL_APP_AND_RUN_WHOAMI_HOSTNAME"
    }
    Json $ReceiptFile $receipt
    $receipt | ConvertTo-Json -Depth 10
}
finally {
    Remove-Item $Tmp -Recurse -Force -ErrorAction SilentlyContinue
}
