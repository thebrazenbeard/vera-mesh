[CmdletBinding()]
param(
    [string]$TunnelId = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$VeraMeshSourceCommit = "432e870eff5d230ce33089f669ef9a7e8ef755af"
$WorkBridgeSourceCommit = "db56871f74f641a0819a601fe166e3241352edaf"
$Root = "C:\ProgramData\VeraMesh"
$ServiceConfig = Join-Path $Root "veraport.json"
$ControllerConfig = Join-Path $Root "controller.json"
$RuntimePython = Join-Path $Root "tunnel-runtime\python.exe"
$Doctor = Join-Path $Root "tunnel-runtime\Scripts\veraport-doctor.exe"
$ReceiptPath = Join-Path $Root "lappy-full-readwrite-install-receipt.json"
$StageRoot = Join-Path $env:TEMP ("Lappy-FullRW-" + [Guid]::NewGuid().ToString("N"))

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this installer from an elevated Administrator PowerShell window."
    }
}

function Wait-ServiceRunning {
    param([string]$Name, [int]$TimeoutSeconds = 45)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction Stop
        if ($service.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Running) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Service $Name did not reach RUNNING within $TimeoutSeconds seconds."
}

function Expand-GitHubCommit {
    param(
        [string]$Repository,
        [string]$Commit,
        [string]$Destination
    )
    if ($Commit -notmatch '^[0-9a-f]{40}$') {
        throw "Commit must be full lowercase 40-hex: $Commit"
    }
    $zip = Join-Path $StageRoot (($Repository -replace '/', '-') + "-" + $Commit + ".zip")
    $url = "https://github.com/$Repository/archive/$Commit.zip"
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $Destination -Force
    $dirs = @(Get-ChildItem -LiteralPath $Destination -Directory)
    if ($dirs.Count -ne 1) {
        throw "Unexpected GitHub source archive layout for $Repository@$Commit."
    }
    return $dirs[0].FullName
}

function Invoke-ElevatedScript {
    param(
        [string]$Path,
        [string[]]$Arguments = @()
    )
    $argumentList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", ('"' + $Path + '"')
    ) + $Arguments
    $process = Start-Process -FilePath "powershell.exe" -ArgumentList $argumentList -NoNewWindow -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        throw "Script failed with exit code $($process.ExitCode): $Path"
    }
}

function Invoke-Doctor {
    if (-not (Test-Path -LiteralPath $Doctor -PathType Leaf)) {
        throw "VeraMesh doctor is missing: $Doctor"
    }
    $output = @(& $Doctor --live --tunnel-status)
    if ($LASTEXITCODE -ne 0) {
        throw "veraport-doctor --live --tunnel-status failed."
    }
    try {
        $result = (($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    }
    catch {
        throw "VeraMesh doctor did not return valid JSON."
    }
    if ($result.ok -ne $true) {
        throw "VeraMesh doctor returned ok=false."
    }
    return $result
}

function Read-StrictJson {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required JSON file missing: $Path"
    }
    return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}

Assert-Administrator

if (-not (Test-Path -LiteralPath $ServiceConfig -PathType Leaf)) {
    throw "Existing VeraPort config not found: $ServiceConfig"
}

New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
try {
    Write-Host ""
    Write-Host "=== 1/4 WorkBridge full read/write install ==="
    $workBridgeRepo = Expand-GitHubCommit -Repository "thebrazenbeard/WorkBridgeMCP" -Commit $WorkBridgeSourceCommit -Destination (Join-Path $StageRoot "workbridge")
    $workBridgeInstaller = Join-Path $workBridgeRepo "scripts\Install-WorkBridgeLappy.ps1"
    if (-not (Test-Path -LiteralPath $workBridgeInstaller -PathType Leaf)) {
        throw "Pinned WorkBridge installer missing: $workBridgeInstaller"
    }
    Invoke-ElevatedScript -Path $workBridgeInstaller

    $workBridgeConfig = Read-StrictJson "C:\ProgramData\WorkBridgeMCP\config.json"
    $readRoots = @($workBridgeConfig.read_roots | ForEach-Object { [string]$_ })
    $writeRoots = @($workBridgeConfig.write_roots | ForEach-Object { [string]$_ })
    if ($readRoots.Count -lt 1) {
        throw "WorkBridge installed with no read roots."
    }
    if (($readRoots -join [Environment]::NewLine) -ne ($writeRoots -join [Environment]::NewLine)) {
        throw "WorkBridge write roots do not exactly match read roots."
    }
    if ($workBridgeConfig.process.enabled -eq $true) {
        throw "WorkBridge process execution unexpectedly became enabled."
    }

    Write-Host ""
    Write-Host "=== 2/4 VeraMesh tunnel repair/attach ==="
    $veraMeshRepo = Expand-GitHubCommit -Repository "thebrazenbeard/vera-mesh" -Commit $VeraMeshSourceCommit -Destination (Join-Path $StageRoot "veramesh")
    $attach = Join-Path $veraMeshRepo "tools\windows_attach_existing_lappy_veramesh.ps1"
    $resume = Join-Path $veraMeshRepo "tools\windows_resume_existing_lappy_tunnel.ps1"

    if (Get-Service -Name "VeraMeshTunnelRuntime" -ErrorAction SilentlyContinue) {
        if (-not (Test-Path -LiteralPath $resume -PathType Leaf)) {
            throw "Pinned VeraMesh resume packet missing: $resume"
        }
        Invoke-ElevatedScript -Path $resume -Arguments @("-Apply")
    }
    else {
        if (-not (Test-Path -LiteralPath $attach -PathType Leaf)) {
            throw "Pinned VeraMesh attach packet missing: $attach"
        }
        $attachArgs = @("-Apply")
        if (-not [string]::IsNullOrWhiteSpace($TunnelId)) {
            $attachArgs += @("-TunnelId", $TunnelId)
        }
        Invoke-ElevatedScript -Path $attach -Arguments $attachArgs
    }

    Wait-ServiceRunning "VeraPortAgent"
    Wait-ServiceRunning "VeraMeshTunnelRuntime"

    Write-Host ""
    Write-Host "=== 3/4 Upgrade Secure MCP Tunnel to full filesystem read/write ==="
    if (-not (Test-Path -LiteralPath $RuntimePython -PathType Leaf)) {
        throw "VeraMesh runtime Python missing: $RuntimePython"
    }
    if (-not (Test-Path -LiteralPath $ControllerConfig -PathType Leaf)) {
        throw "VeraMesh controller config missing: $ControllerConfig"
    }

    $packageRoot = Join-Path $veraMeshRepo "reference\veraport_agent"
    $oldPythonPath = $env:PYTHONPATH
    try {
        $env:PYTHONPATH = $packageRoot
        $upgradeOutput = @(& $RuntimePython -m veraport_agent.controller_capability_upgrade --service-config $ServiceConfig --controller-config $ControllerConfig)
        if ($LASTEXITCODE -ne 0) {
            throw "VeraMesh tunnel controller read/write upgrade failed."
        }
    }
    finally {
        $env:PYTHONPATH = $oldPythonPath
    }

    try {
        $upgrade = (($upgradeOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    }
    catch {
        throw "VeraMesh read/write upgrade did not return valid JSON."
    }
    if (($upgrade.capabilities_after -join ",") -ne "fs.read,fs.write") {
        throw "VeraMesh tunnel controller did not reach exact fs.read+fs.write."
    }
    if ($upgrade.process_execution_enabled -eq $true) {
        throw "VeraMesh process execution unexpectedly became enabled."
    }

    if ($upgrade.service_restart_required -eq $true) {
        Restart-Service -Name "VeraPortAgent" -Force
        Wait-ServiceRunning "VeraPortAgent"
    }
    if ($upgrade.tunnel_restart_required -eq $true -or $upgrade.service_restart_required -eq $true) {
        Restart-Service -Name "VeraMeshTunnelRuntime" -Force
        Wait-ServiceRunning "VeraMeshTunnelRuntime"
    }

    Write-Host ""
    Write-Host "=== 4/4 Live connection qualification ==="
    $doctorResult = Invoke-Doctor

    $controller = Read-StrictJson $ControllerConfig
    $requested = @($controller.requested_capabilities | ForEach-Object { [string]$_ } | Sort-Object)
    if (($requested -join ",") -ne "fs.read,fs.write") {
        throw "Controller config requested capabilities are not exact fs.read+fs.write."
    }
    $operations = @($controller.gateway_operations | ForEach-Object { [string]$_ })
    foreach ($required in @(
        "fs.read_text",
        "fs.read_bytes",
        "fs.stat",
        "fs.list_dir",
        "fs.search",
        "fs.search_content",
        "fs.write_text",
        "fs.append_text",
        "fs.mkdir",
        "fs.move",
        "fs.replace_text"
    )) {
        if ($required -notin $operations) {
            throw "Controller config is missing required operation: $required"
        }
    }
    if (@($operations | Where-Object { $_ -like "process.*" }).Count -ne 0) {
        throw "Controller config unexpectedly exposes process operations."
    }

    $receipt = [ordered]@{
        schema = "LAPPY_WORKBRIDGE_VERAMESH_FULL_READWRITE_INSTALL_V1"
        pass = $true
        observed_at = (Get-Date).ToString("o")
        sources = [ordered]@{
            workbridge = $WorkBridgeSourceCommit
            veramesh = $VeraMeshSourceCommit
        }
        WorkBridge = [ordered]@{
            task = "WorkBridgeMCP"
            config = "C:\ProgramData\WorkBridgeMCP\config.json"
            read_roots = $readRoots
            write_roots = $writeRoots
            process_enabled = $false
            loopback_only = $true
        }
        VeraMesh = [ordered]@{
            controller_config = $ControllerConfig
            capabilities = @("fs.read","fs.write")
            filesystem_write_operations = @(
                "fs.write_text","fs.append_text","fs.mkdir","fs.move","fs.replace_text"
            )
            process_enabled = $false
            VeraPortAgent = (Get-Service -Name "VeraPortAgent").Status.ToString()
            VeraMeshTunnelRuntime = (Get-Service -Name "VeraMeshTunnelRuntime").Status.ToString()
            doctor = $doctorResult
            capability_upgrade = $upgrade
        }
        effects = [ordered]@{
            firewall_changed = $false
            tailscale_changed = $false
            process_execution_enabled = $false
            roots_broadened = $false
            chatgpt_connector_registration_proven = $false
        }
        next_gate = "SELECT_EXISTING_SECURE_MCP_TUNNEL_IN_CHATGPT_AND_TEST_READ_WRITE"
    }

    [IO.File]::WriteAllText(
        $ReceiptPath,
        ($receipt | ConvertTo-Json -Depth 20) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )

    Write-Host ""
    Write-Host "FULL READ/WRITE INSTALL QUALIFIED."
    Write-Host "WorkBridge: read/write, process OFF."
    Write-Host "VeraMesh tunnel: fs.read + fs.write, process OFF."
    Write-Host "Receipt: $ReceiptPath"
    $receipt | ConvertTo-Json -Depth 20
}
finally {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
