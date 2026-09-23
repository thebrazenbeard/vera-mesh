[CmdletBinding()]
param(
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$VeraMeshSourceSha = "56a6334565352aee46cd20789b8a40282f7da8a9"
$TunnelRuntimeModuleGitBlobSha1 = "f7210d2703552b280a73a2df4137553f1971d2f6"
$TunnelRuntimeModuleUrl = "https://raw.githubusercontent.com/thebrazenbeard/vera-mesh/$VeraMeshSourceSha/reference/veraport_agent/veraport_agent/tunnel_runtime_service.py"

$Root = "C:\\ProgramData\\VeraMesh"
$ServiceConfig = Join-Path $Root "veraport.json"
$ControllerConfig = Join-Path $Root "controller.json"
$TunnelConfig = Join-Path $Root "tunnel-runtime.json"
$RuntimeDir = Join-Path $Root "tunnel-runtime"
$RuntimePython = Join-Path $RuntimeDir "python.exe"
$Doctor = Join-Path $RuntimeDir "Scripts\\veraport-doctor.exe"
$ReceiptPath = Join-Path $Root "tunnel-resume-receipt.json"
$FailureReceiptPath = Join-Path $Root "tunnel-resume-failure-receipt.json"
$StageRoot = Join-Path $env:TEMP ("VeraMesh-Tunnel-Resume-" + [guid]::NewGuid().ToString("N"))
$SourceModule = Join-Path $StageRoot "tunnel_runtime_service.py"

function Test-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-ExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

function Wait-ServiceState(
    [string]$Name,
    [System.ServiceProcess.ServiceControllerStatus]$State,
    [int]$TimeoutSeconds = 45
) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction Stop
        if ($service.Status -eq $State) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Service $Name did not reach $State within $TimeoutSeconds seconds"
}

function Invoke-DoctorUntilHealthy([string]$DoctorPath, [int]$Attempts = 18, [int]$SleepSeconds = 5) {
    $last = $null
    for ($i = 1; $i -le $Attempts; $i++) {
        $output = & $DoctorPath --live --tunnel-status
        $exit = $LASTEXITCODE
        try {
            $last = (($output -join [Environment]::NewLine) | ConvertFrom-Json)
        }
        catch {
            $last = $null
        }
        if ($exit -eq 0 -and $last -and $last.ok -eq $true) {
            return $last
        }
        if ($i -lt $Attempts) {
            Start-Sleep -Seconds $SleepSeconds
        }
    }
    throw "VeraMesh doctor did not reach authenticated VeraPort + healthy tunnel state."
}

function Get-FileSha256([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file missing: $Path"
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Get-GitBlobSha1([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    $header = [Text.Encoding]::ASCII.GetBytes(("blob " + $bytes.Length + [char]0))
    $payload = New-Object byte[] ($header.Length + $bytes.Length)
    [Array]::Copy($header, 0, $payload, 0, $header.Length)
    [Array]::Copy($bytes, 0, $payload, $header.Length, $bytes.Length)
    $sha = [Security.Cryptography.SHA1]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($payload))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-TreeSnapshot([string[]]$Roots) {
    $snapshot = [ordered]@{}
    foreach ($rootPath in $Roots) {
        if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) {
            throw "Preservation root missing: $rootPath"
        }
        Get-ChildItem -LiteralPath $rootPath -File -Recurse | Sort-Object FullName | ForEach-Object {
            $snapshot[$_.FullName.ToLowerInvariant()] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    return $snapshot
}

function Assert-SnapshotEqual($Before, $After, [string]$Label) {
    $beforeJson = $Before | ConvertTo-Json -Compress -Depth 12
    $afterJson = $After | ConvertTo-Json -Compress -Depth 12
    if ($beforeJson -ne $afterJson) {
        throw "$Label changed during tunnel resume."
    }
}

function Assert-ExactReadOnlyController($Controller) {
    $requested = @($Controller.requested_capabilities)
    if ($requested.Count -ne 1 -or [string]$requested[0] -ne "fs.read") {
        throw "Existing tunnel controller is not exact fs.read-only."
    }
    $expected = @(
        "fs.list_dir",
        "fs.read_bytes",
        "fs.read_text",
        "fs.search",
        "fs.search_content",
        "fs.stat",
        "lane.close",
        "lane.list",
        "lane.open",
        "lane.renew"
    )
    $actual = @($Controller.gateway_operations | ForEach-Object { [string]$_ } | Sort-Object)
    $expectedSorted = @($expected | Sort-Object)
    if (($actual -join "`n") -ne ($expectedSorted -join "`n")) {
        throw "Existing tunnel controller gateway operations are not the exact read-only set."
    }
}

$plan = [ordered]@{
    schema = "VERAMESH_EXISTING_LAPPY_TUNNEL_RESUME_PLAN_V1"
    source_sha = $VeraMeshSourceSha
    module_git_blob_sha1 = $TunnelRuntimeModuleGitBlobSha1
    effects_if_applied = [ordered]@{
        module_only = $true
        restarts_tunnel_service = $true
        veraport_restart = $false
        credentials_changed = $false
        controller_trust_changed = $false
        controller_config_changed = $false
        tunnel_config_changed = $false
        runtime_key_changed = $false
        allowed_roots_changed = $false
        process_policy_changed = $false
        firewall_changed = $false
        tailscale_changed = $false
        repository_merged = $false
    }
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 10
    Write-Host ""
    Write-Host "PLAN ONLY. Resume patches only the pinned tunnel runtime module and restarts only VeraMeshTunnelRuntime."
    exit 0
}

if (-not (Test-Administrator)) {
    throw "Run this resume packet from PowerShell as Administrator."
}

foreach ($path in @($ServiceConfig, $ControllerConfig, $TunnelConfig, $RuntimePython, $Doctor)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required existing tunnel material missing: $path"
    }
}

$veraService = Get-CimInstance Win32_Service -Filter "Name='VeraPortAgent'" -ErrorAction Stop
$tunnelService = Get-CimInstance Win32_Service -Filter "Name='VeraMeshTunnelRuntime'" -ErrorAction Stop
if ($veraService.State -ne "Running") {
    throw "VeraPortAgent must remain Running during tunnel resume."
}

$veraConfig = Get-Content -LiteralPath $ServiceConfig -Raw | ConvertFrom-Json
if ($veraConfig.allow_process_exec -eq $true) {
    throw "Existing VeraPort unexpectedly has process execution enabled."
}
$allowedRootsBefore = @($veraConfig.allowed_roots | ForEach-Object { [string]$_ })

$controller = Get-Content -LiteralPath $ControllerConfig -Raw | ConvertFrom-Json
Assert-ExactReadOnlyController $controller

$tunnel = Get-Content -LiteralPath $TunnelConfig -Raw | ConvertFrom-Json
if ([string]$tunnel.schema -ne "VERAMESH_TUNNEL_RUNTIME_SERVICE_V1") {
    throw "Unexpected tunnel runtime config schema."
}
if ([string]$tunnel.tunnel_id -notmatch '^tunnel_[0-9a-f]{32}$') {
    throw "Existing tunnel ID is invalid."
}

$runtimeKeyPath = [string]$tunnel.runtime_api_key_file
$tunnelClientPath = [string]$tunnel.tunnel_client
$mcpExecutablePath = [string]$tunnel.mcp_executable
foreach ($path in @($runtimeKeyPath, $tunnelClientPath, $mcpExecutablePath)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Existing tunnel runtime dependency missing: $path"
    }
}
$runtimeKey = [IO.File]::ReadAllText($runtimeKeyPath).Trim()
if ($runtimeKey.Length -lt 32 -or $runtimeKey -match '\\s') {
    throw "Existing tunnel runtime key file is invalid."
}
$runtimeKey = $null

if ((Get-FileSha256 $tunnelClientPath) -ne ([string]$tunnel.tunnel_client_sha256).ToLowerInvariant()) {
    throw "Existing tunnel-client hash does not match tunnel runtime config."
}
if ((Get-FileSha256 $mcpExecutablePath) -ne ([string]$tunnel.mcp_executable_sha256).ToLowerInvariant()) {
    throw "Existing MCP executable hash does not match tunnel runtime config."
}

$moduleOutput = @(& $RuntimePython -c "import veraport_agent.tunnel_runtime_service as m; print(m.__file__)")
Assert-ExitCode "locate installed tunnel runtime module"
$InstalledModule = ([string]$moduleOutput[-1]).Trim()
if ([string]::IsNullOrWhiteSpace($InstalledModule) -or -not (Test-Path -LiteralPath $InstalledModule -PathType Leaf)) {
    throw "Installed tunnel runtime module could not be located."
}
$runtimePrefix = [IO.Path]::GetFullPath($RuntimeDir).TrimEnd("\\") + "\\"
$moduleFull = [IO.Path]::GetFullPath($InstalledModule)
if (-not $moduleFull.StartsWith($runtimePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Installed tunnel runtime module escaped the isolated runtime directory."
}

$preservationRoots = @(
    (Join-Path $Root "identity"),
    (Join-Path $Root "controller")
)
$identityBefore = Get-TreeSnapshot $preservationRoots
$serviceConfigBefore = Get-FileSha256 $ServiceConfig
$controllerConfigBefore = Get-FileSha256 $ControllerConfig
$tunnelConfigBefore = Get-FileSha256 $TunnelConfig
$runtimeKeyBefore = Get-FileSha256 $runtimeKeyPath
$tunnelClientBefore = Get-FileSha256 $tunnelClientPath
$mcpExecutableBefore = Get-FileSha256 $mcpExecutablePath
$moduleBefore = Get-FileSha256 $InstalledModule
$veraProcessIdBefore = [int]$veraService.ProcessId
$veraStartModeBefore = [string]$veraService.StartMode
$veraStartNameBefore = [string]$veraService.StartName
$tunnelStartModeBefore = [string]$tunnelService.StartMode
$tunnelStartNameBefore = [string]$tunnelService.StartName

New-Item -ItemType Directory -Path $StageRoot -Force | Out-Null

try {
    Invoke-WebRequest -UseBasicParsing -Uri $TunnelRuntimeModuleUrl -OutFile $SourceModule
    $sourceBlob = Get-GitBlobSha1 $SourceModule
    if ($sourceBlob -ne $TunnelRuntimeModuleGitBlobSha1) {
        throw "Pinned tunnel runtime source Git blob mismatch."
    }
    $sourceSha256 = Get-FileSha256 $SourceModule

    $backup = $InstalledModule + ".pre-resume-" + $moduleBefore.Substring(0, 12) + ".bak"
    if (Test-Path -LiteralPath $backup -PathType Leaf) {
        if ((Get-FileSha256 $backup) -ne $moduleBefore) {
            throw "Existing tunnel runtime backup diverges from the pre-resume module."
        }
    }
    else {
        Copy-Item -LiteralPath $InstalledModule -Destination $backup
        if ((Get-FileSha256 $backup) -ne $moduleBefore) {
            throw "Tunnel runtime backup verification failed."
        }
    }

    $currentTunnelService = Get-Service -Name "VeraMeshTunnelRuntime" -ErrorAction Stop
    if ($currentTunnelService.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Stopped) {
        Stop-Service -Name "VeraMeshTunnelRuntime" -Force
        Wait-ServiceState "VeraMeshTunnelRuntime" ([System.ServiceProcess.ServiceControllerStatus]::Stopped)
    }

    Copy-Item -LiteralPath $SourceModule -Destination $InstalledModule -Force
    if ((Get-FileSha256 $InstalledModule) -ne $sourceSha256) {
        throw "Installed tunnel runtime module does not match pinned source."
    }

    & $RuntimePython -m py_compile $InstalledModule
    Assert-ExitCode "compile patched tunnel runtime module"

    Start-Service -Name "VeraMeshTunnelRuntime"
    Wait-ServiceState "VeraMeshTunnelRuntime" ([System.ServiceProcess.ServiceControllerStatus]::Running)

    $doctorResult = Invoke-DoctorUntilHealthy $Doctor

    $veraAfter = Get-CimInstance Win32_Service -Filter "Name='VeraPortAgent'" -ErrorAction Stop
    $tunnelAfter = Get-CimInstance Win32_Service -Filter "Name='VeraMeshTunnelRuntime'" -ErrorAction Stop
    $veraConfigAfter = Get-Content -LiteralPath $ServiceConfig -Raw | ConvertFrom-Json
    $allowedRootsAfter = @($veraConfigAfter.allowed_roots | ForEach-Object { [string]$_ })

    Assert-SnapshotEqual $identityBefore (Get-TreeSnapshot $preservationRoots) "VeraPort identity/controller material"
    if ((Get-FileSha256 $ServiceConfig) -ne $serviceConfigBefore) { throw "VeraPort config changed during tunnel resume." }
    if ((Get-FileSha256 $ControllerConfig) -ne $controllerConfigBefore) { throw "Controller config changed during tunnel resume." }
    if ((Get-FileSha256 $TunnelConfig) -ne $tunnelConfigBefore) { throw "Tunnel config changed during tunnel resume." }
    if ((Get-FileSha256 $runtimeKeyPath) -ne $runtimeKeyBefore) { throw "Tunnel runtime key changed during tunnel resume." }
    if ((Get-FileSha256 $tunnelClientPath) -ne $tunnelClientBefore) { throw "Tunnel-client changed during tunnel resume." }
    if ((Get-FileSha256 $mcpExecutablePath) -ne $mcpExecutableBefore) { throw "MCP executable changed during tunnel resume." }
    if (($allowedRootsBefore -join "`n") -ne ($allowedRootsAfter -join "`n")) { throw "Allowed roots changed during tunnel resume." }
    if ($veraConfigAfter.allow_process_exec -eq $true) { throw "Process execution became enabled during tunnel resume." }
    if ([int]$veraAfter.ProcessId -ne $veraProcessIdBefore) { throw "VeraPortAgent restarted during tunnel resume." }
    if ([string]$veraAfter.StartMode -ne $veraStartModeBefore -or [string]$veraAfter.StartName -ne $veraStartNameBefore) {
        throw "VeraPortAgent service identity/start mode changed during tunnel resume."
    }
    if ([string]$tunnelAfter.StartMode -ne $tunnelStartModeBefore -or [string]$tunnelAfter.StartName -ne $tunnelStartNameBefore) {
        throw "VeraMeshTunnelRuntime service identity/start mode changed during tunnel resume."
    }

    $receipt = [ordered]@{
        schema = "VERAMESH_EXISTING_LAPPY_TUNNEL_RESUME_RECEIPT_V1"
        pass = $true
        observed_at = (Get-Date).ToString("o")
        source_sha = $VeraMeshSourceSha
        source_module_git_blob_sha1 = $TunnelRuntimeModuleGitBlobSha1
        module = [ordered]@{
            installed_path = $InstalledModule
            before_sha256 = $moduleBefore
            after_sha256 = Get-FileSha256 $InstalledModule
            backup = $backup
        }
        preservation = [ordered]@{
            veraport_identity_controller_unchanged = $true
            veraport_config_unchanged = $true
            controller_config_unchanged = $true
            tunnel_config_unchanged = $true
            runtime_key_unchanged = $true
            tunnel_client_unchanged = $true
            mcp_executable_unchanged = $true
            allowed_roots_unchanged = $true
            process_execution_remained_disabled = $true
            veraport_process_id_unchanged = $true
        }
        services = [ordered]@{
            VeraPortAgent = [string]$veraAfter.State
            VeraMeshTunnelRuntime = [string]$tunnelAfter.State
        }
        doctor = $doctorResult
        effects = [ordered]@{
            firewall_changed = $false
            tailscale_changed = $false
            credentials_changed = $false
            controller_trust_changed = $false
            repository_merged = $false
            chatgpt_connector_registered = $false
        }
        next_gate = "SELECT_EXISTING_TUNNEL_IN_CHATGPT_AND_RUN_READ_ONLY_LAPPY_CALL"
    }
    [IO.File]::WriteAllText(
        $ReceiptPath,
        ($receipt | ConvertTo-Json -Depth 20) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    $receipt | ConvertTo-Json -Depth 20
}
catch {
    $tunnelCurrent = Get-Service -Name "VeraMeshTunnelRuntime" -ErrorAction SilentlyContinue
    if ($tunnelCurrent -and $tunnelCurrent.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
        Start-Service -Name "VeraMeshTunnelRuntime" -ErrorAction SilentlyContinue
    }
    $failure = [ordered]@{
        schema = "VERAMESH_EXISTING_LAPPY_TUNNEL_RESUME_FAILURE_V1"
        pass = $false
        observed_at = (Get-Date).ToString("o")
        source_sha = $VeraMeshSourceSha
        error = $_.Exception.Message
        runtime_key_value_recorded = $false
        preserved_existing_state_for_reconciliation = $true
    }
    [IO.File]::WriteAllText(
        $FailureReceiptPath,
        ($failure | ConvertTo-Json -Depth 10) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )
    throw
}
finally {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
