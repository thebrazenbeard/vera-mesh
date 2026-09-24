param(
    [string]$WorkBridgeInstallRoot = "C:\ProgramData\WorkBridgeMCP\DesktopCommanderMCP",
    [string]$TunnelConfigPath = "C:\ProgramData\VeraMesh\tunnel-runtime.json",
    [string]$TunnelServiceName = "VeraMeshTunnelRuntime",
    [bool]$StartRuntime = $true
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ExpectedSchema = "WORKBRIDGE_DESKTOP_COMMANDER_DUPLICATE_V1"
$ExpectedUpstreamCommit = "550a0b3e31da18b7cf25e87ed840e3d953b6da42"

function Add-OrSetProperty {
    param(
        [Parameter(Mandatory=$true)]$Object,
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)]$Value
    )
    if ($Object.PSObject.Properties.Name -contains $Name) {
        $Object.$Name = $Value
    } else {
        $Object | Add-Member -NotePropertyName $Name -NotePropertyValue $Value
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

$manifestPath = Join-Path $WorkBridgeInstallRoot "workbridge-desktop-commander.manifest.json"
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Desktop Commander duplicate manifest missing: $manifestPath"
}
if (-not (Test-Path -LiteralPath $TunnelConfigPath -PathType Leaf)) {
    throw "VeraMesh tunnel runtime config missing: $TunnelConfigPath"
}

$manifest = Get-Content -Raw -Encoding UTF8 $manifestPath | ConvertFrom-Json
if ($manifest.schema -ne $ExpectedSchema) {
    throw "unexpected WorkBridge duplicate manifest schema: $($manifest.schema)"
}
if ($manifest.upstream_commit -ne $ExpectedUpstreamCommit) {
    throw "Desktop Commander source pin mismatch: $($manifest.upstream_commit)"
}
if ($manifest.unrestricted_command_string_shell -ne $true) {
    throw "duplicate manifest does not declare unrestricted command-string shell"
}

$nodePath = Join-Path $WorkBridgeInstallRoot $manifest.node_executable_relative
$entrypointPath = Join-Path $WorkBridgeInstallRoot $manifest.entrypoint_relative
foreach ($path in @($nodePath, $entrypointPath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "required duplicate runtime file missing: $path"
    }
}

$nodeHash = Get-Sha256 $nodePath
$entryHash = Get-Sha256 $entrypointPath
if ($nodeHash -ne $manifest.node_sha256) {
    throw "packaged Node runtime hash mismatch"
}
if ($entryHash -ne $manifest.entrypoint_sha256) {
    throw "Desktop Commander entrypoint hash mismatch"
}

$tunnelConfig = Get-Content -Raw -Encoding UTF8 $TunnelConfigPath | ConvertFrom-Json
if ($tunnelConfig.schema -ne "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1") {
    throw "unexpected tunnel runtime schema: $($tunnelConfig.schema)"
}

$backupPath = "$TunnelConfigPath.desktop-commander-backup.$([DateTime]::UtcNow.ToString('yyyyMMddHHmmss')).json"
Copy-Item -LiteralPath $TunnelConfigPath -Destination $backupPath -Force

$service = Get-Service -Name $TunnelServiceName -ErrorAction Stop
$wasRunning = $service.Status -eq "Running"

try {
    if ($wasRunning) {
        Stop-Service -Name $TunnelServiceName -Force
        (Get-Service -Name $TunnelServiceName).WaitForStatus("Stopped", [TimeSpan]::FromSeconds(30))
    }

    Add-OrSetProperty $tunnelConfig "mcp_executable" $nodePath
    Add-OrSetProperty $tunnelConfig "mcp_executable_sha256" $nodeHash
    Add-OrSetProperty $tunnelConfig "mcp_entrypoint" $entrypointPath
    Add-OrSetProperty $tunnelConfig "mcp_entrypoint_sha256" $entryHash
    Add-OrSetProperty $tunnelConfig "mcp_arguments" @("--no-onboarding")

    $tempPath = "$TunnelConfigPath.tmp.$([Guid]::NewGuid().ToString('N'))"
    $tunnelConfig | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $tempPath -Encoding UTF8
    Move-Item -LiteralPath $tempPath -Destination $TunnelConfigPath -Force

    if ($StartRuntime) {
        Start-Service -Name $TunnelServiceName
        (Get-Service -Name $TunnelServiceName).WaitForStatus("Running", [TimeSpan]::FromSeconds(30))

        $env:TUNNEL_CLIENT_PROFILE_DIR = [string]$tunnelConfig.profile_dir
        $env:TUNNEL_CLIENT_STATE_DIR = [string]$tunnelConfig.state_dir
        $statusRaw = & ([string]$tunnelConfig.tunnel_client) runtimes status ([string]$tunnelConfig.alias) --json
        if ($LASTEXITCODE -ne 0) {
            throw "tunnel-client status failed after Desktop Commander activation"
        }
        $status = ($statusRaw -join [Environment]::NewLine) | ConvertFrom-Json
        if ($status.process_running -ne $true -or $status.healthy -ne $true) {
            throw "Desktop Commander tunnel runtime did not become running and healthy"
        }
    }

    [pscustomobject]@{
        schema = "VERAMESH_DESKTOP_COMMANDER_DUPLICATE_ACTIVATION_V1"
        status = "activated"
        upstream_commit = $ExpectedUpstreamCommit
        tunnel_alias = [string]$tunnelConfig.alias
        node_executable = $nodePath
        node_sha256 = $nodeHash
        entrypoint = $entrypointPath
        entrypoint_sha256 = $entryHash
        unrestricted_command_string_shell = $true
        runtime_started = $StartRuntime
        backup_config = $backupPath
    } | ConvertTo-Json -Depth 6
}
catch {
    try {
        Stop-Service -Name $TunnelServiceName -Force -ErrorAction SilentlyContinue
    } catch {}
    Copy-Item -LiteralPath $backupPath -Destination $TunnelConfigPath -Force
    if ($wasRunning) {
        try {
            Start-Service -Name $TunnelServiceName
            (Get-Service -Name $TunnelServiceName).WaitForStatus("Running", [TimeSpan]::FromSeconds(30))
        } catch {}
    }
    throw
}
