[CmdletBinding()]
param(
    [string]$WorkBridgeInstallRoot = "C:\ProgramData\WorkBridgeMCP\DesktopCommanderMCP",
    [string]$TunnelConfigPath = "C:\ProgramData\VeraMesh\tunnel-runtime.json",
    [string]$TunnelServiceName = "VeraMeshTunnelRuntime",
    [bool]$StartRuntime = $true
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ExpectedManifestSchema = "WORKBRIDGE_DESKTOP_COMMANDER_DUPLICATE_V1"
$ExpectedUpstreamCommit = "550a0b3e31da18b7cf25e87ed840e3d953b6da42"
$VeraMeshRuntimeSourceCommit = "6f2d813b59fe2ff0f0aafa6a60feaacc7eea0de7"
$Root = "C:\ProgramData\VeraMesh"
$TunnelPython = Join-Path $Root "tunnel-runtime\python.exe"
$StageRoot = Join-Path $env:TEMP ("VeraMesh-DesktopCommander-" + [Guid]::NewGuid().ToString("N"))
$ReceiptPath = Join-Path $Root "desktop-commander-duplicate-activation.json"

$tunnelPackage = $null
$tunnelPackageBackup = $null
$tunnelCodeReplaced = $false
$configBackup = $null
$wasRunning = $false
$runtimeStatus = $null

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this activation from an elevated Administrator PowerShell window."
    }
}

function Add-OrSetProperty {
    param(
        [Parameter(Mandatory=$true)]$Object,
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)]$Value
    )
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    }
    else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Get-Sha256([string]$Path) {
    (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Write-Utf8Json([string]$Path, [object]$Value) {
    $payload = ($Value | ConvertTo-Json -Depth 30) + [Environment]::NewLine
    [IO.File]::WriteAllText(
        $Path,
        $payload,
        [Text.UTF8Encoding]::new($false)
    )
}

function Wait-ServiceState([string]$Name, [string]$State, [int]$TimeoutSeconds = 45) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $svc = Get-Service -Name $Name -ErrorAction Stop
        if ($svc.Status.ToString() -eq $State) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "$Name did not reach $State."
}

function Wait-TunnelRuntimeReady([object]$Config, [int]$TimeoutSeconds = 45) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $lastStatus = $null
    $lastError = $null

    do {
        $statusRaw = @(& ([string]$Config.tunnel_client) runtimes status ([string]$Config.alias) --json)
        if ($LASTEXITCODE -eq 0) {
            try {
                $candidate = (($statusRaw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
                $lastStatus = $candidate
                if (
                    $candidate.process_running -eq $true -and
                    $candidate.healthy -eq $true -and
                    $candidate.ready -eq $true
                ) {
                    return $candidate
                }
            }
            catch {
                $lastError = $_.Exception.Message
            }
        }
        else {
            $lastError = "tunnel-client status rc=$LASTEXITCODE"
        }

        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)

    if ($null -ne $lastStatus) {
        $summary = "process_running=$($lastStatus.process_running) healthy=$($lastStatus.healthy) ready=$($lastStatus.ready)"
    }
    elseif ($lastError) {
        $summary = "no parseable status; last_error=$lastError"
    }
    else {
        $summary = "no status observed"
    }
    throw "Desktop Commander tunnel runtime did not become running, healthy, and ready within $TimeoutSeconds seconds ($summary)."
}

function Download-ExactSource([string]$Commit) {
    if ($Commit -notmatch '^[0-9a-f]{40}$') {
        throw "VeraMesh source commit must be full lowercase 40-hex."
    }
    New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
    $zip = Join-Path $StageRoot "veramesh-$Commit.zip"
    Invoke-WebRequest -UseBasicParsing -Uri ("https://github.com/thebrazenbeard/vera-mesh/archive/" + $Commit + ".zip") -OutFile $zip
    $expand = Join-Path $StageRoot "source"
    Expand-Archive -LiteralPath $zip -DestinationPath $expand -Force
    $dirs = @(Get-ChildItem -LiteralPath $expand -Directory)
    if ($dirs.Count -ne 1) {
        throw "Unexpected VeraMesh source archive layout."
    }
    $dirs[0].FullName
}

function Resolve-TunnelPackage {
    $raw = @(& $TunnelPython -c "import pathlib,veraport_agent; print(pathlib.Path(veraport_agent.__file__).resolve().parent)")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve installed tunnel veraport_agent package."
    }
    $resolved = (($raw | ForEach-Object { [string]$_ }) -join "").Trim()
    if (-not $resolved -or -not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "Installed tunnel package path is invalid: $resolved"
    }
    $resolved
}

function Replace-PackageTree([string]$Current, [string]$Source, [string]$Backup) {
    if (-not (Test-Path -LiteralPath $Current -PathType Container)) {
        throw "Installed tunnel package missing: $Current"
    }
    if (-not (Test-Path -LiteralPath $Source -PathType Container)) {
        throw "Pinned tunnel package missing: $Source"
    }
    if (Test-Path -LiteralPath $Backup) {
        throw "Tunnel package backup already exists: $Backup"
    }
    Move-Item -LiteralPath $Current -Destination $Backup
    try {
        Copy-Item -LiteralPath $Source -Destination $Current -Recurse
    }
    catch {
        if (Test-Path -LiteralPath $Current) {
            Remove-Item -LiteralPath $Current -Recurse -Force -ErrorAction SilentlyContinue
        }
        Move-Item -LiteralPath $Backup -Destination $Current
        throw
    }
}

function Restore-PackageTree([string]$Current, [string]$Backup) {
    if (-not $Backup -or -not (Test-Path -LiteralPath $Backup -PathType Container)) {
        return
    }
    if (Test-Path -LiteralPath $Current) {
        Remove-Item -LiteralPath $Current -Recurse -Force
    }
    Move-Item -LiteralPath $Backup -Destination $Current
}

function Restore-PreviousState {
    try {
        Stop-Service -Name $TunnelServiceName -Force -ErrorAction SilentlyContinue
    } catch {}

    if ($configBackup -and (Test-Path -LiteralPath $configBackup -PathType Leaf)) {
        Copy-Item -LiteralPath $configBackup -Destination $TunnelConfigPath -Force
    }

    if ($tunnelCodeReplaced) {
        Restore-PackageTree $tunnelPackage $tunnelPackageBackup
        $script:tunnelCodeReplaced = $false
    }

    if ($wasRunning) {
        Start-Service -Name $TunnelServiceName
        Wait-ServiceState $TunnelServiceName "Running"
    }
}

Assert-Administrator

$manifestPath = Join-Path $WorkBridgeInstallRoot "workbridge-desktop-commander.manifest.json"
foreach ($required in @($manifestPath, $TunnelConfigPath, $TunnelPython)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required runtime material missing: $required"
    }
}
if (-not (Get-Service -Name $TunnelServiceName -ErrorAction SilentlyContinue)) {
    throw "Required tunnel service is missing: $TunnelServiceName"
}

$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
if ($manifest.schema -ne $ExpectedManifestSchema) {
    throw "Unexpected WorkBridge duplicate manifest schema: $($manifest.schema)"
}
if ($manifest.upstream_commit -ne $ExpectedUpstreamCommit) {
    throw "Desktop Commander source pin mismatch: $($manifest.upstream_commit)"
}
if ($manifest.unrestricted_command_string_shell -ne $true) {
    throw "Duplicate manifest does not declare unrestricted command-string shell."
}

$nodePath = Join-Path $WorkBridgeInstallRoot $manifest.node_executable_relative
$entrypointPath = Join-Path $WorkBridgeInstallRoot $manifest.entrypoint_relative
foreach ($required in @($nodePath, $entrypointPath)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required Desktop Commander runtime file missing: $required"
    }
}

$nodeHash = Get-Sha256 $nodePath
$entryHash = Get-Sha256 $entrypointPath
if ($nodeHash -ne $manifest.node_sha256) {
    throw "Packaged Node runtime hash mismatch."
}
if ($entryHash -ne $manifest.entrypoint_sha256) {
    throw "Desktop Commander entrypoint hash mismatch."
}

$service = Get-Service -Name $TunnelServiceName -ErrorAction Stop
$wasRunning = $service.Status.ToString() -eq "Running"
$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$configBackup = "$TunnelConfigPath.pre-desktop-commander-$stamp.bak"

New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
try {
    Write-Host "=== Stage exact VeraMesh tunnel runtime source ==="
    $sourceRoot = Download-ExactSource $VeraMeshRuntimeSourceCommit
    $sourcePackage = Join-Path $sourceRoot "reference\veraport_agent\veraport_agent"
    $sourceTunnelRuntime = Join-Path $sourcePackage "tunnel_runtime_service.py"
    if (-not (Test-Path -LiteralPath $sourceTunnelRuntime -PathType Leaf)) {
        throw "Pinned VeraMesh source lacks tunnel_runtime_service.py."
    }
    $sourceText = Get-Content -LiteralPath $sourceTunnelRuntime -Raw -Encoding UTF8
    foreach ($needle in @("mcp_entrypoint", "mcp_entrypoint_sha256", "mcp_arguments", "def mcp_command")) {
        if ($sourceText -notmatch [regex]::Escape($needle)) {
            throw "Pinned tunnel runtime source lacks Desktop Commander support: $needle"
        }
    }

    Write-Host "=== Stop tunnel service and replace only its package code ==="
    if ($wasRunning) {
        Stop-Service -Name $TunnelServiceName -Force
        Wait-ServiceState $TunnelServiceName "Stopped"
    }

    $tunnelPackage = Resolve-TunnelPackage
    $tunnelPackageBackup = "$tunnelPackage.pre-desktop-commander-$stamp"
    Replace-PackageTree $tunnelPackage $sourcePackage $tunnelPackageBackup
    $tunnelCodeReplaced = $true

    $installedTunnelRuntime = Join-Path $tunnelPackage "tunnel_runtime_service.py"
    if ((Get-Sha256 $installedTunnelRuntime) -ne (Get-Sha256 $sourceTunnelRuntime)) {
        throw "Installed tunnel runtime does not match pinned VeraMesh source."
    }

    Write-Host "=== Bind exact Desktop Commander runtime into existing tunnel ==="
    Copy-Item -LiteralPath $TunnelConfigPath -Destination $configBackup
    $tunnelConfig = Get-Content -Raw -Encoding UTF8 $TunnelConfigPath | ConvertFrom-Json
    if ($tunnelConfig.schema -ne "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1") {
        throw "Unexpected tunnel runtime schema: $($tunnelConfig.schema)"
    }

    Add-OrSetProperty $tunnelConfig "mcp_executable" $nodePath
    Add-OrSetProperty $tunnelConfig "mcp_executable_sha256" $nodeHash
    Add-OrSetProperty $tunnelConfig "mcp_entrypoint" $entrypointPath
    Add-OrSetProperty $tunnelConfig "mcp_entrypoint_sha256" $entryHash
    Add-OrSetProperty $tunnelConfig "mcp_arguments" @("--no-onboarding")
    Write-Utf8Json $TunnelConfigPath $tunnelConfig

    Write-Host "=== Harden and validate updated tunnel materials ==="
    $oldConfigEnv = $env:VERAMESH_DESKTOP_COMMANDER_TUNNEL_CONFIG
    try {
        $env:VERAMESH_DESKTOP_COMMANDER_TUNNEL_CONFIG = $TunnelConfigPath
        & $TunnelPython -c "import os; from pathlib import Path; from veraport_agent.tunnel_runtime_service import TunnelRuntimeServiceConfig,harden_service_materials,validate_service_materials; p=Path(os.environ['VERAMESH_DESKTOP_COMMANDER_TUNNEL_CONFIG']); c=TunnelRuntimeServiceConfig.load(p); c.validate_runtime_files(); harden_service_materials(p,c); validate_service_materials(p,c)"
        if ($LASTEXITCODE -ne 0) {
            throw "Tunnel runtime material hardening/validation failed."
        }
    }
    finally {
        $env:VERAMESH_DESKTOP_COMMANDER_TUNNEL_CONFIG = $oldConfigEnv
    }

    if ($StartRuntime) {
        Write-Host "=== Start exact Desktop Commander through existing Secure MCP Tunnel ==="
        Start-Service -Name $TunnelServiceName
        Wait-ServiceState $TunnelServiceName "Running"

        $activeConfig = Get-Content -Raw -Encoding UTF8 $TunnelConfigPath | ConvertFrom-Json
        $env:TUNNEL_CLIENT_PROFILE_DIR = [string]$activeConfig.profile_dir
        $env:TUNNEL_CLIENT_STATE_DIR = [string]$activeConfig.state_dir
        $runtimeStatus = Wait-TunnelRuntimeReady $activeConfig 45
        if (
            $runtimeStatus.PSObject.Properties.Name -contains "remote_error" -and
            -not [string]::IsNullOrWhiteSpace([string]$runtimeStatus.remote_error)
        ) {
            Write-Warning (
                "Desktop Commander runtime is locally running/healthy/ready, " +
                "but remote tunnel metadata lookup reported: " +
                [string]$runtimeStatus.remote_error
            )
        }
    }

    $receipt = [ordered]@{
        schema = "VERAMESH_DESKTOP_COMMANDER_DUPLICATE_ACTIVATION_V2"
        status = if (
            $null -ne $runtimeStatus -and
            $runtimeStatus.PSObject.Properties.Name -contains "remote_error" -and
            -not [string]::IsNullOrWhiteSpace([string]$runtimeStatus.remote_error)
        ) {
            "LOCAL_PASS_REMOTE_AUTH_PENDING"
        }
        else {
            "PASS"
        }
        observed_at = (Get-Date).ToString("o")
        sources = [ordered]@{
            desktop_commander = $ExpectedUpstreamCommit
            veramesh_runtime = $VeraMeshRuntimeSourceCommit
        }
        tunnel_alias = [string]$tunnelConfig.alias
        node_executable = $nodePath
        node_sha256 = $nodeHash
        entrypoint = $entrypointPath
        entrypoint_sha256 = $entryHash
        unrestricted_command_string_shell = $true
        tunnel_package = $tunnelPackage
        tunnel_package_backup = $tunnelPackageBackup
        runtime_started = $StartRuntime
        runtime_status = if ($null -ne $runtimeStatus) {
            [ordered]@{
                process_running = [bool]$runtimeStatus.process_running
                healthy = [bool]$runtimeStatus.healthy
                ready = [bool]$runtimeStatus.ready
                runtime_state = [string]$runtimeStatus.runtime_state
                remote_error = [string]$runtimeStatus.remote_error
            }
        }
        else {
            $null
        }
        config_backup = $configBackup
        remote_transport_qualified = (
            $StartRuntime -and
            $null -ne $runtimeStatus -and
            (
                -not ($runtimeStatus.PSObject.Properties.Name -contains "remote_error") -or
                [string]::IsNullOrWhiteSpace([string]$runtimeStatus.remote_error)
            )
        )
        next_gate = if (
            $StartRuntime -and
            $null -ne $runtimeStatus -and
            $runtimeStatus.PSObject.Properties.Name -contains "remote_error" -and
            -not [string]::IsNullOrWhiteSpace([string]$runtimeStatus.remote_error)
        ) {
            "REPAIR_CONTROL_PLANE_AUTH_THEN_CALL_DESKTOP_COMMANDER_FROM_CHATGPT"
        }
        else {
            "CALL_DESKTOP_COMMANDER_TOOL_FROM_CHATGPT_THROUGH_SELECTED_SECURE_MCP_TUNNEL"
        }
    }
    Write-Utf8Json $ReceiptPath $receipt

    Write-Host ""
    Write-Host "DESKTOP COMMANDER DUPLICATE TUNNEL ACTIVATION QUALIFIED."
    Write-Host "Receipt: $ReceiptPath"
    $receipt | ConvertTo-Json -Depth 20
}
catch {
    Write-Warning ("Desktop Commander activation failed: " + $_.Exception.Message)
    try { Restore-PreviousState } catch { Write-Warning ("Rollback failed: " + $_.Exception.Message) }
    throw
}
finally {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
