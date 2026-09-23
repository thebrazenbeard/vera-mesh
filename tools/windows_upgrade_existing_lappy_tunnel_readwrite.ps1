[CmdletBinding()]
param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$VeraMeshSourceCommit = "aa56672e0d0d79927a895add673bbb8b2bb78f8d"
$Root = "C:\ProgramData\VeraMesh"
$ServiceConfig = Join-Path $Root "veraport.json"
$ControllerConfig = Join-Path $Root "controller.json"
$TunnelConfig = Join-Path $Root "tunnel-runtime.json"
$RuntimePython = Join-Path $Root "tunnel-runtime\python.exe"
$Doctor = Join-Path $Root "tunnel-runtime\Scripts\veraport-doctor.exe"
$ReceiptPath = Join-Path $Root "tunnel-readwrite-upgrade-receipt.json"
$StageRoot = Join-Path $env:TEMP ("VeraMesh-ReadWrite-Upgrade-" + [Guid]::NewGuid().ToString("N"))

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this upgrade from an elevated Administrator PowerShell window."
    }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file missing: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Read-StrictJson([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required JSON file missing: $Path"
    }
    return (Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json)
}

function Wait-ServiceRunning([string]$Name, [int]$TimeoutSeconds = 45) {
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

function Invoke-LiveDoctor {
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

foreach ($required in @($ServiceConfig, $ControllerConfig, $TunnelConfig, $RuntimePython, $Doctor)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required existing VeraMesh material missing: $required"
    }
}

$service = Read-StrictJson $ServiceConfig
$controller = Read-StrictJson $ControllerConfig

# Capability/trust/process-operation validation is deliberately delegated to
# controller_capability_upgrade.py, which uses the same VeraPort Python config
# and trust parsers as the live runtime. Do not duplicate those semantics in
# Windows PowerShell, where JSON array/scalar coercion can diverge.

$plan = [ordered]@{
    schema = "VERAMESH_EXISTING_LAPPY_READWRITE_UPGRADE_PLAN_V1"
    apply = [bool]$Apply
    source_commit = $VeraMeshSourceCommit
    capabilities_before = "RUNTIME_AUTHORITATIVE_PREFLIGHT_ON_APPLY"
    capabilities_after = @("fs.read", "fs.write")
    process_execution = $false
    allowed_roots_change = $false
    reinstall = $false
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 8
    Write-Host "PLAN ONLY. Re-run with -Apply to perform the bounded controller upgrade."
    exit 0
}

Assert-Administrator
$serviceHashBefore = Get-FileSha256 $ServiceConfig
$tunnelHashBefore = Get-FileSha256 $TunnelConfig
$allowedRootsBefore = @($service.allowed_roots | ForEach-Object { [string]$_ })

New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
try {
    $zip = Join-Path $StageRoot "vera-mesh-$VeraMeshSourceCommit.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/thebrazenbeard/vera-mesh/archive/$VeraMeshSourceCommit.zip" -OutFile $zip

    $sourceStage = Join-Path $StageRoot "source"
    New-Item -ItemType Directory -Force -Path $sourceStage | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $sourceStage -Force
    $dirs = @(Get-ChildItem -LiteralPath $sourceStage -Directory)
    if ($dirs.Count -ne 1) {
        throw "Unexpected VeraMesh source archive layout."
    }

    $packageRoot = Join-Path $dirs[0].FullName "reference\veraport_agent"
    $upgradeModule = Join-Path $packageRoot "veraport_agent\controller_capability_upgrade.py"
    if (-not (Test-Path -LiteralPath $upgradeModule -PathType Leaf)) {
        throw "Pinned VeraMesh source is missing controller_capability_upgrade.py"
    }

    $upgradeDriver = Join-Path $StageRoot "invoke-controller-capability-upgrade.py"
    $upgradeDriverSource = @'
from __future__ import annotations

import sys
from pathlib import Path

if len(sys.argv) < 2:
    raise SystemExit("source root argument is required")

source_root = Path(sys.argv.pop(1)).resolve()
if not source_root.is_dir():
    raise SystemExit(f"source root is not a directory: {source_root}")

sys.path.insert(0, str(source_root))

from veraport_agent.controller_capability_upgrade import main

main()
'@
    [IO.File]::WriteAllText(
        $upgradeDriver,
        $upgradeDriverSource,
        [Text.UTF8Encoding]::new($false)
    )

    $upgradeOutput = @(
        & $RuntimePython $upgradeDriver $packageRoot `
            --service-config $ServiceConfig `
            --controller-config $ControllerConfig
    )
    if ($LASTEXITCODE -ne 0) {
        throw "VeraMesh tunnel controller read/write upgrade failed."
    }

    try {
        $upgrade = (($upgradeOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    }
    catch {
        throw "VeraMesh read/write upgrade did not return valid JSON."
    }
    if (($upgrade.capabilities_after -join ",") -ne "fs.read,fs.write") {
        throw "Controller did not reach exact fs.read+fs.write."
    }
    if ($upgrade.process_execution_enabled -eq $true) {
        throw "Process execution unexpectedly became enabled."
    }

    if ($upgrade.service_restart_required -eq $true) {
        Restart-Service -Name "VeraPortAgent" -Force
        Wait-ServiceRunning "VeraPortAgent"
    }
    if ($upgrade.tunnel_restart_required -eq $true -or $upgrade.service_restart_required -eq $true) {
        Restart-Service -Name "VeraMeshTunnelRuntime" -Force
        Wait-ServiceRunning "VeraMeshTunnelRuntime"
    }

    $doctor = Invoke-LiveDoctor
    $serviceAfter = Read-StrictJson $ServiceConfig
    $controllerAfter = Read-StrictJson $ControllerConfig
    $requestedAfter = @($controllerAfter.requested_capabilities | ForEach-Object { [string]$_ } | Sort-Object)
    if (($requestedAfter -join ",") -ne "fs.read,fs.write") {
        throw "Controller config requested capabilities are not exact fs.read+fs.write."
    }

    $writeOps = @("fs.write_text", "fs.append_text", "fs.mkdir", "fs.move", "fs.replace_text")
    $operationsAfter = @($controllerAfter.gateway_operations | ForEach-Object { [string]$_ })
    foreach ($operation in $writeOps) {
        if ($operation -notin $operationsAfter) {
            throw "Controller config is missing write operation: $operation"
        }
    }
    if (@($operationsAfter | Where-Object { $_ -like "process.*" }).Count -ne 0) {
        throw "Controller config unexpectedly exposes process operations."
    }

    if ((Get-FileSha256 $ServiceConfig) -ne $serviceHashBefore) {
        throw "VeraPort service config changed during controller-only upgrade."
    }
    if ((Get-FileSha256 $TunnelConfig) -ne $tunnelHashBefore) {
        throw "Tunnel runtime config changed during controller-only upgrade."
    }
    $allowedRootsAfter = @($serviceAfter.allowed_roots | ForEach-Object { [string]$_ })
    if (($allowedRootsBefore -join [Environment]::NewLine) -ne ($allowedRootsAfter -join [Environment]::NewLine)) {
        throw "VeraPort allowed roots changed during controller-only upgrade."
    }
    $processPolicy = @($doctor.checks | Where-Object { [string]$_.name -eq "process_policy" })
    if ($processPolicy.Count -ne 1 -or [string]$processPolicy[0].state -ne "PASS" -or [string]$processPolicy[0].detail -ne "process disabled") {
        throw "Live doctor does not confirm process execution remains disabled."
    }

    $machine = $doctor.machine_info
    $doctorRequested = @($machine.requested_capabilities | ForEach-Object { [string]$_ } | Sort-Object)
    if (($doctorRequested -join ",") -ne "fs.read,fs.write") {
        throw "Live doctor does not request exact fs.read+fs.write."
    }
    $selectedId = [string]$machine.selected_path_id
    $selected = @($machine.paths | Where-Object { [string]$_.path_id -eq $selectedId })
    if ($selected.Count -ne 1) {
        throw "Live doctor did not expose exactly one selected VeraPort path."
    }
    $granted = @($selected[0].granted_capabilities | ForEach-Object { [string]$_ } | Sort-Object)
    if (($granted -join ",") -ne "fs.read,fs.write") {
        throw "Live selected VeraPort path did not grant exact fs.read+fs.write."
    }

    $receipt = [ordered]@{
        schema = "VERAMESH_EXISTING_LAPPY_READWRITE_UPGRADE_RECEIPT_V1"
        pass = $true
        observed_at = (Get-Date).ToString("o")
        source_commit = $VeraMeshSourceCommit
        capabilities_before = $upgrade.capabilities_before
        capabilities_after = $upgrade.capabilities_after
        write_operations = $writeOps
        process_execution = $false
        allowed_roots_unchanged = $true
        service_config_unchanged = $true
        tunnel_config_unchanged = $true
        services = [ordered]@{
            VeraPortAgent = (Get-Service -Name "VeraPortAgent").Status.ToString()
            VeraMeshTunnelRuntime = (Get-Service -Name "VeraMeshTunnelRuntime").Status.ToString()
        }
        doctor = $doctor
        upgrade = $upgrade
        next_gate = "CREATE_OR_RESCAN_CHATGPT_TUNNEL_CONNECTOR_AND_RUN_READ_WRITE_CALL"
    }
    [IO.File]::WriteAllText(
        $ReceiptPath,
        ($receipt | ConvertTo-Json -Depth 20) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    $receipt | ConvertTo-Json -Depth 20
}
finally {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
