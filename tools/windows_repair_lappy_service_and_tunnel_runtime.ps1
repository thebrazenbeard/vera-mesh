[CmdletBinding()]
param([switch]$Apply)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceCommit = "12f80d79474ba0a3f4d78eff4d6a05f8c97f7454"
$Root = "C:\ProgramData\VeraMesh"
$RuntimeDir = Join-Path $Root "tunnel-runtime"
$RuntimePython = Join-Path $RuntimeDir "python.exe"
$ServiceCli = Join-Path $RuntimeDir "Scripts\veraport-service.exe"
$TunnelServiceCli = Join-Path $RuntimeDir "Scripts\veramesh-tunnel-service.exe"
$Doctor = Join-Path $RuntimeDir "Scripts\veraport-doctor.exe"
$ServiceConfig = Join-Path $Root "veraport.json"
$ControllerConfig = Join-Path $Root "controller.json"
$TunnelConfig = Join-Path $Root "tunnel-runtime.json"
$ReceiptPath = Join-Path $Root "service-tunnel-runtime-repair-receipt.json"
$StageRoot = Join-Path $env:TEMP ("VeraMesh-Service-Tunnel-Repair-" + [Guid]::NewGuid().ToString("N"))

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this repair from an elevated Administrator PowerShell window."
    }
}

function Assert-ExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed with exit code $LASTEXITCODE" }
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Required file missing: $Path" }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Wait-ServiceState([string]$Name, [System.ServiceProcess.ServiceControllerStatus]$State, [int]$TimeoutSeconds = 45) {
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction Stop
        if ($service.Status -eq $State) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Service $Name did not reach $State within $TimeoutSeconds seconds."
}

function Get-ServiceObservation([string]$Name) {
    $service = Get-CimInstance Win32_Service -Filter "Name='$Name'" -ErrorAction Stop
    $pythonClass = $null
    try {
        $pythonClass = (Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\$Name\PythonClass" -ErrorAction Stop)."(default)"
    } catch {}
    return [ordered]@{
        name = [string]$service.Name
        state = [string]$service.State
        start_mode = [string]$service.StartMode
        start_name = [string]$service.StartName
        path_name = [string]$service.PathName
        process_id = [int]$service.ProcessId
        python_class = $pythonClass
    }
}

function Invoke-LiveDoctor {
    $output = @(& $Doctor --live --tunnel-status)
    $exit = $LASTEXITCODE
    try {
        $result = (($output | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    } catch {
        throw "VeraMesh doctor did not return valid JSON."
    }
    if ($exit -ne 0 -or $result.ok -ne $true) {
        Write-Host (($result | ConvertTo-Json -Depth 20))
        throw "VeraMesh doctor did not reach ok=true."
    }
    return $result
}

foreach ($required in @($RuntimePython,$ServiceCli,$TunnelServiceCli,$Doctor,$ServiceConfig,$ControllerConfig,$TunnelConfig)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required existing VeraMesh material missing: $required" }
}

$beforeVera = Get-ServiceObservation "VeraPortAgent"
$beforeTunnel = Get-ServiceObservation "VeraMeshTunnelRuntime"

$plan = [ordered]@{
    schema = "VERAMESH_LAPPY_SERVICE_TUNNEL_REPAIR_PLAN_V1"
    apply = [bool]$Apply
    source_commit = $SourceCommit
    current_veraport_image = $beforeVera.path_name
    target_runtime = $RuntimeDir
    effects_if_applied = [ordered]@{
        refreshes_veraport_package_in_existing_isolated_runtime = $true
        rebinds_veraport_windows_service = $true
        rebinds_tunnel_windows_service = $true
        rotates_identity = $false
        changes_allowed_roots = $false
        changes_process_policy = $false
        changes_tunnel_id = $false
        changes_runtime_api_key = $false
        reseals_mcp_executable_hash = $true
        reseals_runtime_acls = $true
        starts_services = $true
    }
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 10
    Write-Host "PLAN ONLY. Re-run with -Apply to perform the bounded repair."
    exit 0
}

Assert-Administrator
$serviceHashBefore = Get-FileSha256 $ServiceConfig
$controllerHashBefore = Get-FileSha256 $ControllerConfig
$tunnelBefore = Get-Content -LiteralPath $TunnelConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$tunnelIdBefore = [string]$tunnelBefore.tunnel_id
$runtimeKeyPath = [string]$tunnelBefore.runtime_api_key_file
$tunnelClientPath = [string]$tunnelBefore.tunnel_client
$runtimeKeyHashBefore = Get-FileSha256 $runtimeKeyPath
$tunnelClientHashBefore = Get-FileSha256 $tunnelClientPath

New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
try {
    $sourceZip = Join-Path $StageRoot "source.zip"
    $sourceStage = Join-Path $StageRoot "source"
    $sourceUri = "https://github.com/thebrazenbeard/vera-mesh/archive/$SourceCommit.zip"
    Invoke-WebRequest -UseBasicParsing -Uri $sourceUri -OutFile $sourceZip
    New-Item -ItemType Directory -Force -Path $sourceStage | Out-Null
    Expand-Archive -LiteralPath $sourceZip -DestinationPath $sourceStage -Force
    $dirs = @(Get-ChildItem -LiteralPath $sourceStage -Directory)
    if ($dirs.Count -ne 1) { throw "Unexpected VeraMesh source archive layout." }

    $packageRoot = Join-Path $dirs[0].FullName "reference\veraport_agent"
    $resealModule = Join-Path $packageRoot "veraport_agent\tunnel_runtime_reseal.py"
    if (-not (Test-Path -LiteralPath $resealModule -PathType Leaf)) { throw "Pinned source is missing tunnel_runtime_reseal.py" }

    $tunnelSvc = Get-Service -Name "VeraMeshTunnelRuntime" -ErrorAction Stop
    if ($tunnelSvc.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Stopped) {
        Stop-Service -Name "VeraMeshTunnelRuntime" -Force
        Wait-ServiceState "VeraMeshTunnelRuntime" ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
    }
    $veraSvc = Get-Service -Name "VeraPortAgent" -ErrorAction Stop
    if ($veraSvc.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Stopped) {
        Stop-Service -Name "VeraPortAgent" -Force
        Wait-ServiceState "VeraPortAgent" ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
    }

    Write-Host "Building exact VeraPort wheel before changing the installed runtime..."
    $wheelDir = Join-Path $StageRoot "wheel"
    New-Item -ItemType Directory -Force -Path $wheelDir | Out-Null
    $wheelArgs = @("-m","pip","wheel","--disable-pip-version-check","--no-index","--no-deps","--no-build-isolation","--wheel-dir",$wheelDir,$packageRoot)
    & $RuntimePython @wheelArgs | Out-Host
    Assert-ExitCode "exact VeraPort wheel build"
    $wheels = @(Get-ChildItem -LiteralPath $wheelDir -Filter "veraport_agent_reference-*.whl" -File)
    if ($wheels.Count -ne 1) { throw "Expected exactly one VeraPort wheel, found $($wheels.Count)." }

    Write-Host "Refreshing VeraPort package in existing isolated runtime..."
    $pipArgs = @("-m","pip","install","--disable-pip-version-check","--no-index","--no-deps","--force-reinstall",$wheels[0].FullName)
    & $RuntimePython @pipArgs | Out-Host
    Assert-ExitCode "isolated VeraPort package refresh"

    foreach ($required in @($ServiceCli,$TunnelServiceCli,$Doctor)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Runtime refresh removed required entrypoint: $required" }
    }

    Write-Host "Rebinding VeraPortAgent to isolated runtime..."
    & $ServiceCli --startup auto update | Out-Host
    Assert-ExitCode "VeraPortAgent service update"

    Write-Host "Rebinding VeraMeshTunnelRuntime to isolated runtime..."
    & $TunnelServiceCli --startup auto update | Out-Host
    Assert-ExitCode "VeraMeshTunnelRuntime service update"

    $reboundVera = Get-ServiceObservation "VeraPortAgent"
    $reboundTunnel = Get-ServiceObservation "VeraMeshTunnelRuntime"
    $runtimePrefix = (Resolve-Path -LiteralPath $RuntimeDir -ErrorAction Stop).ProviderPath
    if (-not $runtimePrefix.EndsWith([IO.Path]::DirectorySeparatorChar.ToString(), [StringComparison]::Ordinal)) {
        $runtimePrefix += [IO.Path]::DirectorySeparatorChar
    }
    $veraImage = ([string]$reboundVera.path_name).Trim('"')
    $tunnelImage = ([string]$reboundTunnel.path_name).Trim('"')
    if (-not $veraImage.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "VeraPortAgent still points outside isolated runtime: $veraImage"
    }
    if (-not $tunnelImage.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "VeraMeshTunnelRuntime points outside isolated runtime: $tunnelImage"
    }

    Start-Service -Name "VeraPortAgent"
    Wait-ServiceState "VeraPortAgent" ([System.ServiceProcess.ServiceControllerStatus]::Running)
    $loopback = @(Get-NetTCPConnection -State Listen -LocalPort 17444 -ErrorAction SilentlyContinue | Where-Object { [string]$_.LocalAddress -eq "127.0.0.1" })
    if ($loopback.Count -lt 1) { throw "VeraPortAgent is Running but 127.0.0.1:17444 is not listening." }

    Write-Host "Resealing MCP launcher digest and ACL..."
    $resealOutput = @(& $RuntimePython -m veraport_agent.tunnel_runtime_reseal --config $TunnelConfig --runtime-root $RuntimeDir)
    Assert-ExitCode "tunnel runtime reseal"
    $reseal = (($resealOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    if ([string]$reseal.schema -ne "VERAMESH_TUNNEL_RUNTIME_RESEAL_V1") { throw "Unexpected tunnel runtime reseal schema." }

    if ((Get-FileSha256 $ServiceConfig) -ne $serviceHashBefore) { throw "VeraPort service config changed during repair." }
    if ((Get-FileSha256 $ControllerConfig) -ne $controllerHashBefore) { throw "VeraPort controller config changed during repair." }

    $tunnelAfter = Get-Content -LiteralPath $TunnelConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$tunnelAfter.tunnel_id -ne $tunnelIdBefore) { throw "Tunnel ID changed during repair." }
    if ((Get-FileSha256 $runtimeKeyPath) -ne $runtimeKeyHashBefore) { throw "Tunnel runtime API key file changed during repair." }
    if ((Get-FileSha256 $tunnelClientPath) -ne $tunnelClientHashBefore) { throw "Tunnel-client executable changed during repair." }

    Start-Service -Name "VeraMeshTunnelRuntime"
    Wait-ServiceState "VeraMeshTunnelRuntime" ([System.ServiceProcess.ServiceControllerStatus]::Running)
    $doctor = Invoke-LiveDoctor

    $processPolicy = @($doctor.checks | Where-Object { [string]$_.name -eq "process_policy" })
    if ($processPolicy.Count -ne 1 -or [string]$processPolicy[0].detail -ne "process disabled") {
        throw "Live doctor does not confirm process execution is disabled."
    }
    $requested = @($doctor.machine_info.requested_capabilities | ForEach-Object { [string]$_ } | Sort-Object)
    if (($requested -join ",") -ne "fs.read,fs.write") { throw "Live doctor does not request exact fs.read+fs.write." }
    $selectedId = [string]$doctor.machine_info.selected_path_id
    $selected = @($doctor.machine_info.paths | Where-Object { [string]$_.path_id -eq $selectedId })
    if ($selected.Count -ne 1) { throw "Live doctor did not expose exactly one selected VeraPort path." }
    $granted = @($selected[0].granted_capabilities | ForEach-Object { [string]$_ } | Sort-Object)
    if (($granted -join ",") -ne "fs.read,fs.write") { throw "Live selected VeraPort path did not grant exact fs.read+fs.write." }

    $receipt = [ordered]@{
        schema = "VERAMESH_LAPPY_SERVICE_TUNNEL_REPAIR_RECEIPT_V1"
        pass = $true
        observed_at = (Get-Date).ToString("o")
        source_commit = $SourceCommit
        service_before = $beforeVera
        tunnel_service_before = $beforeTunnel
        service_after = Get-ServiceObservation "VeraPortAgent"
        tunnel_service_after = Get-ServiceObservation "VeraMeshTunnelRuntime"
        veraport_config_unchanged = $true
        controller_config_unchanged = $true
        tunnel_id_unchanged = $true
        runtime_api_key_unchanged = $true
        tunnel_client_unchanged = $true
        reseal = $reseal
        doctor = $doctor
        next_gate = "CREATE_OR_RESCAN_CHATGPT_TUNNEL_CONNECTOR_AND_RUN_REAL_READ_WRITE_CALL"
    }
    [IO.File]::WriteAllText($ReceiptPath, ($receipt | ConvertTo-Json -Depth 20) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    $receipt | ConvertTo-Json -Depth 20
}
finally {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
