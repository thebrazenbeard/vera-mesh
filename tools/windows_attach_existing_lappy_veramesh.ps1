param(
    [string]$TunnelId = "",
    [string]$Alias = "veramesh-lappy",
    [string]$ControllerPrivateKey = "C:\\ProgramData\\VeraMesh\\controller\\vera-controller-bootstrap.pem",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$VeraMeshSourceSha = "d2782a0dc4e50e353a807aba93a55bb8ff97ac83"
$TunnelClientVersion = "v0.0.14"
$TunnelClientArchiveSha256 = "784ab8da7b5a88f0109f1fd8aaf0a1c86067430b896dddf307ef7e3cc49fa1a5"
$TunnelClientUrl = "https://github.com/openai/tunnel-client/releases/download/v0.0.14/tunnel-client-v0.0.14-windows-amd64.zip"

$PythonVersion = "3.11.9"
$PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embeddable-amd64.zip"
$PythonSha256 = "33b448f95fecb7c6f802157dbd5e6b40a2ad9bfc8b95ca634a06ba4073ad1ac0"
$GetPipCommit = "f6f644156f23dfe9acc06e7b9ca75eee311f2e37"
$GetPipUrl = "https://raw.githubusercontent.com/pypa/get-pip/$GetPipCommit/public/get-pip.py"
$SourceUrl = "https://github.com/thebrazenbeard/vera-mesh/archive/$VeraMeshSourceSha.zip"

$Root = "C:\\ProgramData\\VeraMesh"
$ServiceConfig = Join-Path $Root "veraport.json"
$RuntimeDir = Join-Path $Root "tunnel-runtime"
$BinDir = Join-Path $Root "tunnel-bin"
$SecretDir = Join-Path $Root "secrets"
$RuntimeKeyPath = Join-Path $SecretDir "tunnel-runtime.key"
$GeneratedControllerPrivateKey = Join-Path $Root "controller\\chatgpt-readonly-controller.pem"
$ReceiptPath = Join-Path $Root "tunnel-attach-receipt.json"
$FailureReceiptPath = Join-Path $Root "tunnel-attach-failure-receipt.json"

$StageRoot = Join-Path $env:TEMP ("VeraMesh-Existing-Attach-" + [guid]::NewGuid().ToString("N"))
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
    if (Test-Path -LiteralPath $Destination) {
        if (Get-ChildItem -LiteralPath $Destination -Force | Select-Object -First 1) {
            throw "Existing tunnel runtime directory is non-empty: $Destination"
        }
    }
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    Expand-Archive -Path $PythonZip -DestinationPath $Destination -Force
    Enable-EmbeddedSite $Destination

    $python = Join-Path $Destination "python.exe"
    if (-not (Test-Path $python -PathType Leaf)) {
        throw "Portable Python executable missing from $Destination"
    }

    & $python $GetPip --disable-pip-version-check | Out-Host
    Assert-ExitCode "get-pip"

    & $python -m pip install --disable-pip-version-check "pip==26.2.1" "setuptools==84.0.0" "wheel==0.48.0" "packaging==26.3" | Out-Host
    Assert-ExitCode "pinned packaging bootstrap"

    $target = $PackagePath + "[windows-service,mcp]"
    & $python -m pip install --disable-pip-version-check --no-build-isolation --constraint $ConstraintsPath $target | Out-Host
    Assert-ExitCode "VeraMesh tunnel runtime package installation"
    return $python
}

function Protect-SecretDirectory([string]$Path) {
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    & icacls.exe $Path /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
    Assert-ExitCode "secret directory ACL"
}

function Read-RuntimeKeyToFile([string]$Path) {
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        $existing = [IO.File]::ReadAllText($Path).Trim()
        if ($existing.Length -lt 32 -or $existing -match '\\s') {
            throw "Existing runtime API key file is invalid: $Path"
        }
        return
    }
    $secure = Read-Host "Paste the restricted tunnel runtime API key (input hidden)" -AsSecureString
    $ptr = [IntPtr]::Zero
    $plain = $null
    try {
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        if ([string]::IsNullOrWhiteSpace($plain) -or $plain.Length -lt 32) {
            throw "Runtime API key is empty or implausibly short."
        }
        if ($plain -match '\\s') {
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
    throw "VeraMesh doctor did not reach authenticated VeraPort + healthy tunnel state."
}

$plan = [ordered]@{
    schema = "VERAMESH_EXISTING_LAPPY_TUNNEL_ATTACH_PLAN_V1"
    veramesh_source_sha = $VeraMeshSourceSha
    tunnel_client = [ordered]@{
        version = $TunnelClientVersion
        archive_sha256 = $TunnelClientArchiveSha256
    }
    existing_veraport = [ordered]@{
        config = $ServiceConfig
        preferred_controller_private_key = $ControllerPrivateKey
        generated_readonly_controller_private_key = $GeneratedControllerPrivateKey
        workstation_identity_preserved = $true
        existing_controller_entries_preserved = $true
    }
    authority = [ordered]@{
        requested_capabilities = @("fs.read")
        process_execution = $false
        existing_allowed_roots_preserved = $true
    }
    effects_if_applied = [ordered]@{
        replaces_veraport_service = $false
        rotates_workstation_identity = $false
        may_append_readonly_controller = $true
        retires_existing_controller = $false
        changes_veraport_roots = $false
        changes_veraport_process_policy = $false
        writes_tunnel_runtime_material = $true
        stores_tunnel_runtime_key_locally = $true
        installs_tunnel_windows_service = $true
        starts_tunnel_windows_service = $true
        changes_firewall = $false
        changes_tailscale = $false
        merges_repository = $false
    }
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 12
    Write-Host ""
    Write-Host "PLAN ONLY. Existing VeraPort will be preserved; -Apply only adds the tunnel/controller sidecar."
    exit 0
}

if (-not (Test-Administrator)) {
    throw "Run this script from PowerShell as Administrator."
}
$veraPortService = Get-Service -Name "VeraPortAgent" -ErrorAction Stop
if ($veraPortService.Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
    throw "Existing VeraPortAgent must already be Running; this attach packet does not start or replace it."
}
if (-not (Test-Path -LiteralPath $ServiceConfig -PathType Leaf)) {
    throw "Existing VeraPort config not found: $ServiceConfig"
}
if (Get-Service -Name "VeraMeshTunnelRuntime" -ErrorAction SilentlyContinue) {
    throw "VeraMeshTunnelRuntime already exists. Refusing to overwrite; reconcile existing tunnel state first."
}
foreach ($path in @(
    (Join-Path $Root "controller.json"),
    (Join-Path $Root "tunnel-runtime.json"),
    $ReceiptPath
)) {
    if (Test-Path -LiteralPath $path) {
        throw "Existing tunnel-attach material found: $path. Refusing to overwrite."
    }
}
if ((Test-Path -LiteralPath $RuntimeDir) -and (Get-ChildItem -LiteralPath $RuntimeDir -Force | Select-Object -First 1)) {
    throw "Existing tunnel runtime directory is non-empty: $RuntimeDir"
}
if ((Test-Path -LiteralPath $BinDir) -and (Get-ChildItem -LiteralPath $BinDir -Force | Select-Object -First 1)) {
    throw "Existing tunnel bin directory is non-empty: $BinDir"
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
    $packagePath = Join-Path $repo.FullName "reference\\veraport_agent"
    $constraintsPath = Join-Path $repo.FullName "protocol\\veraport\\v1\\windows-runtime-constraints.txt"
    if (-not (Test-Path (Join-Path $packagePath "veraport_agent\\existing_install_attach.py") -PathType Leaf)) {
        throw "Exact VeraMesh source lacks existing-install attach support."
    }
    if (-not (Test-Path (Join-Path $packagePath "veraport_agent\\controller_recovery.py") -PathType Leaf)) {
        throw "Exact VeraMesh source lacks controller recovery support."
    }
    if (-not (Test-Path $constraintsPath -PathType Leaf)) {
        throw "Windows runtime constraints missing from exact source."
    }

    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
    Protect-SecretDirectory $SecretDir

    Write-Host "Installing isolated tunnel-side VeraMesh runtime..."
    $runtimePython = Initialize-PortableRuntime $RuntimeDir $packagePath $constraintsPath

    $tunnelCandidate = Get-ChildItem $StageTunnel -Recurse -Filter "tunnel-client.exe" | Select-Object -First 1
    if (-not $tunnelCandidate) {
        throw "tunnel-client.exe missing from official OpenAI archive."
    }
    $tunnelExe = Join-Path $BinDir "tunnel-client.exe"
    Copy-Item -LiteralPath $tunnelCandidate.FullName -Destination $tunnelExe

    $mcpExe = Join-Path $RuntimeDir "Scripts\\veraport-mcp-stdio.exe"
    $tunnelService = Join-Path $RuntimeDir "Scripts\\veramesh-tunnel-service.exe"
    $doctor = Join-Path $RuntimeDir "Scripts\\veraport-doctor.exe"
    foreach ($required in @($mcpExe, $tunnelService, $doctor)) {
        if (-not (Test-Path $required -PathType Leaf)) {
            throw "Required VeraMesh tunnel entrypoint missing: $required"
        }
    }

    Write-Host "Recovering the enrolled VeraPort controller or appending a new read-only controller..."
    $controllerRecoveryOutput = @(
        & $runtimePython -m veraport_agent.controller_recovery `
            --service-config $ServiceConfig `
            --preferred-controller-private-key $ControllerPrivateKey `
            --generated-controller-private-key $GeneratedControllerPrivateKey
    )
    Assert-ExitCode "VeraPort controller recovery"
    $controllerRecovery = (($controllerRecoveryOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    $activeControllerPrivateKey = [string]$controllerRecovery.controller_private_key
    if ([string]::IsNullOrWhiteSpace($activeControllerPrivateKey) -or -not (Test-Path -LiteralPath $activeControllerPrivateKey -PathType Leaf)) {
        throw "Controller recovery did not return a usable private-key path."
    }

    if ($controllerRecovery.service_restart_required -eq $true) {
        Write-Host "New fs.read-only controller was appended. Restarting VeraPortAgent to load the expanded trust set..."
        Restart-Service -Name "VeraPortAgent" -Force
        Wait-ServiceRunning "VeraPortAgent"
    }

    Write-Host "Writing/reusing the protected tunnel runtime key..."
    Read-RuntimeKeyToFile $RuntimeKeyPath

    Write-Host "Binding tunnel/controller material to the existing VeraPort identity..."
    & $runtimePython -m veraport_agent.existing_install_attach `
        --service-config $ServiceConfig `
        --controller-private-key $activeControllerPrivateKey `
        --tunnel-client $tunnelExe `
        --tunnel-id $TunnelId `
        --runtime-api-key-file $RuntimeKeyPath `
        --mcp-executable $mcpExe `
        --alias $Alias | Out-Host
    Assert-ExitCode "existing VeraPort tunnel attach"

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
        schema = "VERAMESH_EXISTING_LAPPY_TUNNEL_ATTACH_RECEIPT_V1"
        pass = $true
        observed_at = (Get-Date).ToString("o")
        veramesh_source_sha = $VeraMeshSourceSha
        preserved_veraport_service = [ordered]@{
            name = "VeraPortAgent"
            status = (Get-Service -Name "VeraPortAgent").Status.ToString()
            config = $ServiceConfig
            workstation_identity_rotated = $false
            existing_controller_entries_preserved = [bool]$controllerRecovery.old_controller_entries_preserved
            controller_trust_changed = [bool]$controllerRecovery.service_restart_required
            roots_changed = $false
            process_policy_changed = $false
        }
        controller_recovery = $controllerRecovery
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
        service = [ordered]@{
            VeraMeshTunnelRuntime = (Get-Service -Name "VeraMeshTunnelRuntime").Status.ToString()
            startup = "Automatic"
        }
        doctor = $doctorResult
        effects = [ordered]@{
            firewall_changed = $false
            tailscale_changed = $false
            repository_merged = $false
            chatgpt_connector_registered = $false
        }
        next_gate = "SELECT_TUNNEL_IN_CHATGPT_AND_RUN_READ_ONLY_LAPPY_CALL"
    }
    [IO.File]::WriteAllText(
        $ReceiptPath,
        ($receipt | ConvertTo-Json -Depth 20) + [Environment]::NewLine,
        [Text.UTF8Encoding]::new($false)
    )

    Write-Host ""
    Write-Host "EXISTING VERAPORT PRESERVED; SECURE TUNNEL RUNTIME HEALTHY."
    Write-Host "Tunnel ID: $TunnelId"
    Write-Host "Receipt: $ReceiptPath"
    Write-Host ""
    Write-Host "Opening ChatGPT connector settings."
    Start-Process "https://chatgpt.com/#settings/Connectors"
}
catch {
    try {
        $vera = Get-Service -Name "VeraPortAgent" -ErrorAction SilentlyContinue
        $failure = [ordered]@{
            schema = "VERAMESH_EXISTING_LAPPY_TUNNEL_ATTACH_FAILURE_V1"
            pass = $false
            observed_at = (Get-Date).ToString("o")
            veramesh_source_sha = $VeraMeshSourceSha
            tunnel_id = $TunnelId
            alias = $Alias
            error = $_.Exception.Message
            VeraPortAgent_status = $(if ($vera) { $vera.Status.ToString() } else { "ABSENT" })
            VeraMeshTunnelRuntime_registered = [bool]$tunnelInstalled
            preserved_existing_state_for_reconciliation = $true
            runtime_key_value_recorded = $false
        }
        [IO.File]::WriteAllText(
            $FailureReceiptPath,
            ($failure | ConvertTo-Json -Depth 12) + [Environment]::NewLine,
            [Text.UTF8Encoding]::new($false)
        )
    }
    catch {
    }
    throw
}
finally {
    Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
