param(
    [string]$TunnelId = "",
    [string[]]$AllowedRoot = @($env:USERPROFILE),
    [string]$Alias = "veramesh-lappy",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"

$VeraMeshSourceSha = "95e1c8a3d60e8b1ac9165f46ab317b50e58c57f9"
$TunnelClientVersion = "v0.0.11"
$TunnelClientArchiveSha256 = "eb912c86c6ccde90cda805cb17009507176a656725cf86c36fabe1901a12e29b"
$TunnelClientUrl = "https://github.com/openai/tunnel-client/releases/download/v0.0.11/tunnel-client-v0.0.11-windows-amd64.zip"

$PythonVersion = "3.11.9"
$PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embeddable-amd64.zip"
$PythonSha256 = "33b448f95fecb7c6f802157dbd5e6b40a2ad9bfc8b95ca634a06ba4073ad1ac0"
$GetPipCommit = "f6f644156f23dfe9acc06e7b9ca75eee311f2e37"
$GetPipUrl = "https://raw.githubusercontent.com/pypa/get-pip/$GetPipCommit/public/get-pip.py"
$SourceUrl = "https://github.com/thebrazenbeard/vera-mesh/archive/$VeraMeshSourceSha.zip"

$Root = "C:\ProgramData\VeraMesh"
$RuntimeDir = Join-Path $Root "runtime"
$BinDir = Join-Path $Root "bin"
$SecretDir = Join-Path $Root "secrets"
$RuntimeKeyPath = Join-Path $SecretDir "tunnel-runtime.key"
$ReceiptPath = Join-Path $Root "activation-receipt.json"
$FailureReceiptPath = Join-Path $Root "activation-failure-receipt.json"

$StageRoot = Join-Path $env:TEMP ("VeraMesh-Lappy-Activation-" + [guid]::NewGuid().ToString("N"))
$StagePython = Join-Path $StageRoot "python"
$StageSource = Join-Path $StageRoot "source"
$StageTunnel = Join-Path $StageRoot "tunnel"
$PythonZip = Join-Path $StageRoot "python.zip"
$SourceZip = Join-Path $StageRoot "source.zip"
$TunnelZip = Join-Path $StageRoot "tunnel-client.zip"
$GetPip = Join-Path $StageRoot "get-pip.py"

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

function Download-VerifiedFile([string]$Uri, [string]$Destination, [string]$ExpectedSha256 = "") {
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination
    if ($ExpectedSha256) {
        $actual = (Get-FileHash -Algorithm SHA256 $Destination).Hash.ToLowerInvariant()
        if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
            throw "SHA-256 mismatch for $Uri. Expected $ExpectedSha256, got $actual"
        }
    }
}

function Enable-EmbeddedSite([string]$PythonDir) {
    $pth = Get-ChildItem $PythonDir -Filter "python311._pth" | Select-Object -First 1
    if (-not $pth) {
        throw "Portable Python _pth file not found under $PythonDir"
    }
    $text = Get-Content $pth.FullName
    $text = $text -replace '^#import site$', 'import site'
    Set-Content -Path $pth.FullName -Value $text -Encoding ASCII
}

function Initialize-PortableRuntime([string]$Destination, [string]$PackagePath, [string]$ConstraintsPath) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Expand-Archive -Path $PythonZip -DestinationPath $Destination -Force
    Enable-EmbeddedSite $Destination

    $python = Join-Path $Destination "python.exe"
    if (-not (Test-Path $python)) {
        throw "Portable Python executable missing from $Destination"
    }

    & $python $GetPip --disable-pip-version-check | Out-Host
    Assert-ExitCode "get-pip"

    & $python -m pip install --disable-pip-version-check "pip==26.2.1" "setuptools==84.0.0" "wheel==0.48.0" "packaging==26.3" | Out-Host
    Assert-ExitCode "pinned packaging bootstrap"

    $target = $PackagePath + "[windows-service,mcp]"
    & $python -m pip install --disable-pip-version-check --no-build-isolation --constraint $ConstraintsPath $target | Out-Host
    Assert-ExitCode "VeraMesh runtime package installation"

    return $python
}

function Wait-ServiceRunning([string]$Name, [int]$TimeoutSeconds = 45) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction Stop
        if ($service.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Running) {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    throw "Service $Name did not reach RUNNING within $TimeoutSeconds seconds"
}

function Protect-SecretDirectory([string]$Path) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    & icacls.exe $Path /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    Assert-ExitCode "secret directory ACL"
}

function Read-RuntimeKeyToFile([string]$Path) {
    $secure = Read-Host "Paste the restricted tunnel runtime API key (input hidden)" -AsSecureString
    $ptr = [IntPtr]::Zero
    $plain = $null
    try {
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt 32) {
            throw "Runtime API key is empty or implausibly short."
        }
        if ($plain -match '\s') {
            throw "Runtime API key must not contain whitespace."
        }
        [IO.File]::WriteAllText($Path, $plain, [Text.UTF8Encoding]::new($false))
    }
    finally {
        $plain = $null
        if ($ptr -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
        }
    }
}

function Invoke-DoctorUntilHealthy([string]$Doctor, [int]$Attempts = 18, [int]$SleepSeconds = 5) {
    $last = $null
    for ($i = 1; $i -le $Attempts; $i++) {
        $output = & $Doctor --live --tunnel-status
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
    throw "VeraMesh doctor did not reach healthy live+tunnel state."
}

$plan = [ordered]@{
    schema = "VERAMESH_LAPPY_ACTIVATION_PLAN_V1"
    veramesh_source_sha = $VeraMeshSourceSha
    tunnel_client = [ordered]@{
        version = $TunnelClientVersion
        archive_sha256 = $TunnelClientArchiveSha256
    }
    root = $Root
    allowed_roots = @($AllowedRoot)
    alias = $Alias
    authority = [ordered]@{
        filesystem_read_write = $true
        process_execution = $false
        inbound_non_loopback_listener = $false
    }
    intended_services = @("VeraPortAgent", "VeraMeshTunnelRuntime")
    effects_if_applied = [ordered]@{
        writes_programdata = $true
        creates_persistent_workstation_and_controller_identity = $true
        stores_tunnel_runtime_key_locally = $true
        installs_windows_services = $true
        starts_windows_services = $true
        service_startup_automatic = $true
        creates_or_modifies_firewall_rules = $false
        opens_inbound_listener = $false
        enables_process_execution = $false
        merges_repository = $false
    }
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 12
    Write-Host ""
    Write-Host "PLAN ONLY. Re-run with -Apply to perform the authorized Lappy connection effects."
    exit 0
}

if (-not (Test-Administrator)) {
    throw "Run this script from PowerShell as Administrator."
}

if (-not $AllowedRoot -or $AllowedRoot.Count -lt 1) {
    throw "At least one allowed filesystem root is required."
}
foreach ($rootPath in $AllowedRoot) {
    if (-not (Test-Path -LiteralPath $rootPath -PathType Container)) {
        throw "Allowed root does not exist: $rootPath"
    }
}

foreach ($serviceName in @("VeraPortAgent", "VeraMeshTunnelRuntime", "VeraPortMCP")) {
    if (Get-Service -Name $serviceName -ErrorAction SilentlyContinue) {
        throw "Existing service $serviceName found. Refusing to overwrite; reconcile existing state first."
    }
}
if ((Test-Path $Root) -and (Get-ChildItem -Force $Root | Select-Object -First 1)) {
    throw "Existing VeraMesh state found under $Root. Refusing to overwrite; reconcile existing state first."
}

if ([string]::IsNullOrWhiteSpace($TunnelId)) {
    Write-Host ""
    Write-Host "Create or select the OpenAI Secure MCP Tunnel for this ChatGPT workspace."
    Write-Host "Create a restricted runtime API key with Tunnels: Read + Use only."
    Start-Process "https://platform.openai.com/settings/organization/tunnels"
    Start-Process "https://platform.openai.com/settings/organization/api-keys"
    Write-Host ""
    $TunnelId = Read-Host "Paste the tunnel ID"
}
if ($TunnelId -notmatch '^tunnel_[0-9a-f]{32}$') {
    throw "Tunnel ID must match tunnel_<32 lowercase hexadecimal characters>."
}

New-Item -ItemType Directory -Path $StageRoot | Out-Null
New-Item -ItemType Directory -Path $StagePython | Out-Null
New-Item -ItemType Directory -Path $StageSource | Out-Null
New-Item -ItemType Directory -Path $StageTunnel | Out-Null

$veraportInstalled = $false
$tunnelInstalled = $false

try {
    Write-Host "Downloading pinned portable Python..."
    Download-VerifiedFile $PythonUrl $PythonZip $PythonSha256

    Write-Host "Downloading exact VeraMesh source $VeraMeshSourceSha..."
    Download-VerifiedFile $SourceUrl $SourceZip

    Write-Host "Downloading exact PyPA get-pip source $GetPipCommit..."
    Download-VerifiedFile $GetPipUrl $GetPip

    Write-Host "Downloading OpenAI tunnel-client $TunnelClientVersion..."
    Download-VerifiedFile $TunnelClientUrl $TunnelZip $TunnelClientArchiveSha256

    Expand-Archive -Path $SourceZip -DestinationPath $StageSource -Force
    Expand-Archive -Path $TunnelZip -DestinationPath $StageTunnel -Force

    $repo = Get-ChildItem $StageSource -Directory | Select-Object -First 1
    if (-not $repo) {
        throw "Extracted VeraMesh source directory not found."
    }
    $packagePath = Join-Path $repo.FullName "reference\veraport_agent"
    $constraintsPath = Join-Path $repo.FullName "protocol\veraport\v1\windows-runtime-constraints.txt"
    if (-not (Test-Path $packagePath)) {
        throw "VeraPort package missing from exact source."
    }
    if (-not (Test-Path $constraintsPath)) {
        throw "Windows runtime constraints missing from exact source."
    }

    $sourcePyproject = Join-Path $packagePath "pyproject.toml"
    $sourcePyprojectText = Get-Content -Raw $sourcePyproject
    if ($sourcePyprojectText -notmatch 'veramesh-tunnel-service' -or $sourcePyprojectText -notmatch 'veraport-doctor') {
        throw "Exact VeraMesh source does not contain required activation entrypoints."
    }

    New-Item -ItemType Directory -Path $Root -Force | Out-Null
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    Protect-SecretDirectory $SecretDir

    Write-Host "Installing pinned portable VeraMesh runtime..."
    $runtimePython = Initialize-PortableRuntime $RuntimeDir $packagePath $constraintsPath

    $tunnelCandidate = Get-ChildItem $StageTunnel -Recurse -Filter "tunnel-client.exe" | Select-Object -First 1
    if (-not $tunnelCandidate) {
        throw "tunnel-client.exe missing from official OpenAI archive."
    }
    $tunnelExe = Join-Path $BinDir "tunnel-client.exe"
    Copy-Item -LiteralPath $tunnelCandidate.FullName -Destination $tunnelExe -Force

    $mcpExe = Join-Path $RuntimeDir "Scripts\veraport-mcp-stdio.exe"
    $veraportService = Join-Path $RuntimeDir "Scripts\veraport-service.exe"
    $tunnelService = Join-Path $RuntimeDir "Scripts\veramesh-tunnel-service.exe"
    $bootstrap = Join-Path $RuntimeDir "Scripts\veramesh-bootstrap-local.exe"
    $doctor = Join-Path $RuntimeDir "Scripts\veraport-doctor.exe"
    foreach ($required in @($mcpExe, $veraportService, $tunnelService, $bootstrap, $doctor)) {
        if (-not (Test-Path $required -PathType Leaf)) {
            throw "Required VeraMesh runtime entrypoint missing: $required"
        }
    }

    Write-Host "Writing the tunnel runtime key to protected local storage..."
    Read-RuntimeKeyToFile $RuntimeKeyPath

    Write-Host "Creating persistent VeraPort identity, trust and tunnel configuration..."
    $bootstrapArgs = @(
        "--root", $Root,
        "--tunnel-client", $tunnelExe,
        "--tunnel-id", $TunnelId,
        "--runtime-api-key-file", $RuntimeKeyPath,
        "--mcp-executable", $mcpExe,
        "--alias", $Alias
    )
    foreach ($rootPath in $AllowedRoot) {
        $bootstrapArgs += @("--allowed-root", $rootPath)
    }
    & $bootstrap @bootstrapArgs | Out-Host
    Assert-ExitCode "VeraMesh local bootstrap"

    Write-Host "Registering VeraPortAgent Windows service..."
    & $veraportService install | Out-Host
    Assert-ExitCode "VeraPortAgent registration"
    $veraportInstalled = $true
    Set-Service -Name "VeraPortAgent" -StartupType Automatic

    Write-Host "Starting VeraPortAgent..."
    Start-Service -Name "VeraPortAgent"
    Wait-ServiceRunning "VeraPortAgent"

    Write-Host "Registering VeraMeshTunnelRuntime Windows service..."
    & $tunnelService install | Out-Host
    Assert-ExitCode "VeraMeshTunnelRuntime registration"
    $tunnelInstalled = $true
    Set-Service -Name "VeraMeshTunnelRuntime" -StartupType Automatic

    Write-Host "Starting VeraMeshTunnelRuntime..."
    Start-Service -Name "VeraMeshTunnelRuntime"
    Wait-ServiceRunning "VeraMeshTunnelRuntime"

    Write-Host "Waiting for authenticated VeraPort + managed tunnel health..."
    $doctorResult = Invoke-DoctorUntilHealthy $doctor

    $receipt = [ordered]@{
        schema = "VERAMESH_LAPPY_ACTIVATION_RECEIPT_V1"
        pass = $true
        observed_at = (Get-Date).ToString("o")
        veramesh_source_sha = $VeraMeshSourceSha
        tunnel_client = [ordered]@{
            version = $TunnelClientVersion
            archive_sha256 = $TunnelClientArchiveSha256
            executable_sha256 = (Get-FileHash -Algorithm SHA256 $tunnelExe).Hash.ToLowerInvariant()
        }
        tunnel = [ordered]@{
            id = $TunnelId
            alias = $Alias
            runtime_key_path = $RuntimeKeyPath
            runtime_key_value_recorded = $false
        }
        services = [ordered]@{
            VeraPortAgent = (Get-Service -Name "VeraPortAgent").Status.ToString()
            VeraMeshTunnelRuntime = (Get-Service -Name "VeraMeshTunnelRuntime").Status.ToString()
            startup = "Automatic"
        }
        authority = [ordered]@{
            allowed_roots = @($AllowedRoot)
            process_execution = $false
            non_loopback_listener = $false
        }
        doctor = $doctorResult
        effects = [ordered]@{
            firewall_changed = $false
            inbound_port_opened = $false
            repository_merged = $false
            chatgpt_connector_registered = $false
        }
        next_gate = "REGISTER_TUNNEL_IN_CHATGPT_AND_RUN_READ_ONLY_E2E"
    }
    [IO.File]::WriteAllText($ReceiptPath, ($receipt | ConvertTo-Json -Depth 20) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))

    Write-Host ""
    Write-Host "LOCAL VERAMESH RUNTIME HEALTHY."
    Write-Host "Tunnel ID: $TunnelId"
    Write-Host "Activation receipt: $ReceiptPath"
    Write-Host ""
    Write-Host "Opening ChatGPT connector settings."
    Write-Host "Add/select a connector using Connection: Tunnel and choose this tunnel."
    Start-Process "https://chatgpt.com/#settings/Connectors"
}
catch {
    if (Test-Path $Root) {
        try {
            $veraService = Get-Service -Name "VeraPortAgent" -ErrorAction SilentlyContinue
            $tunnelServiceState = Get-Service -Name "VeraMeshTunnelRuntime" -ErrorAction SilentlyContinue
            $failure = [ordered]@{
                schema = "VERAMESH_LAPPY_ACTIVATION_FAILURE_V1"
                pass = $false
                observed_at = (Get-Date).ToString("o")
                veramesh_source_sha = $VeraMeshSourceSha
                tunnel_id = $TunnelId
                alias = $Alias
                error = $_.Exception.Message
                services = [ordered]@{
                    VeraPortAgent_registered = [bool]$veraportInstalled
                    VeraMeshTunnelRuntime_registered = [bool]$tunnelInstalled
                    VeraPortAgent_status = $(if ($veraService) { $veraService.Status.ToString() } else { "ABSENT" })
                    VeraMeshTunnelRuntime_status = $(if ($tunnelServiceState) { $tunnelServiceState.Status.ToString() } else { "ABSENT" })
                }
                preserved_for_reconciliation = $true
                runtime_key_value_recorded = $false
            }
            [IO.File]::WriteAllText($FailureReceiptPath, ($failure | ConvertTo-Json -Depth 12) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
        }
        catch {
        }
    }
    throw
}
finally {
    Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
