param(
    [Parameter(Mandatory = $true)]
    [string[]]$AllowedRoot,
    [string]$ControllerExportDir = "$env:USERPROFILE\VeraMesh-Controller-Export",
    [string]$BindHost = "127.0.0.1",
    [int]$BindPort = 17444,
    [switch]$AllowNonLoopbackListener,
    [switch]$EnableProcess,
    [switch]$Apply,
    [switch]$RemoveAfterQualification
)

$ErrorActionPreference = "Stop"

$SourceSha = "cecc418824b5ee0141d44a0b42637a9e54c1d4e3"
$PythonVersion = "3.11.9"
$PythonUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embeddable-amd64.zip"
$PythonSha256 = "33b448f95fecb7c6f802157dbd5e6b40a2ad9bfc8b95ca634a06ba4073ad1ac0"
$GetPipCommit = "f6f644156f23dfe9acc06e7b9ca75eee311f2e37"
$GetPipUrl = "https://raw.githubusercontent.com/pypa/get-pip/$GetPipCommit/public/get-pip.py"
$SourceUrl = "https://github.com/thebrazenbeard/vera-mesh/archive/$SourceSha.zip"

$ServiceName = "VeraPortAgent"
$ServiceRoot = "C:\ProgramData\VeraMesh"
$RuntimeDir = Join-Path $ServiceRoot "runtime"
$ReceiptPath = Join-Path $ServiceRoot "install-qualification-receipt.json"
$StageRoot = Join-Path $env:TEMP ("VeraMesh-Service-Install-" + [guid]::NewGuid().ToString("N"))
$StagePython = Join-Path $StageRoot "python"
$StageSource = Join-Path $StageRoot "source"
$PythonZip = Join-Path $StageRoot "python.zip"
$SourceZip = Join-Path $StageRoot "source.zip"
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

function Download-File([string]$Uri, [string]$Destination, [string]$ExpectedSha256 = "") {
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

function Initialize-PortableRuntime(
    [string]$Destination,
    [string]$PackagePath,
    [string]$ConstraintsPath,
    [bool]$WithWindowsService
) {
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

    $target = $PackagePath
    if ($WithWindowsService) {
        $target = $PackagePath + "[windows-service]"
    }

    & $python -m pip install --disable-pip-version-check --no-build-isolation --constraint $ConstraintsPath $target | Out-Host
    Assert-ExitCode "VeraPort package installation"

    Write-Output $python
}

function Wait-ServiceRunning([string]$Name, [int]$TimeoutSeconds = 30) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $service = Get-Service -Name $Name -ErrorAction Stop
        if ($service.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Running) {
            return
        }
        Start-Sleep -Milliseconds 250
    } while ((Get-Date) -lt $deadline)
    throw "Service $Name did not reach RUNNING within $TimeoutSeconds seconds"
}

function Invoke-ServiceQualification(
    [string]$Python,
    [string]$ControllerConfig,
    [string]$ProbePath,
    [string]$ProbeToken,
    [string]$ExpectedPrincipal = ""
) {
    $args = @(
        "-m", "veraport_agent.qualify_service",
        "--controller-config", $ControllerConfig,
        "--probe-path", $ProbePath,
        "--expected-token", $ProbeToken,
        "--source-sha", $SourceSha
    )
    if ($ExpectedPrincipal) {
        $args += @("--expected-workstation-principal", $ExpectedPrincipal)
    }
    $output = & $Python @args
    Assert-ExitCode "VeraPort service qualification"
    $json = ($output -join [Environment]::NewLine)
    return ($json | ConvertFrom-Json)
}

$plan = [ordered]@{
    schema = "VERAPORT_WINDOWS_SERVICE_INSTALL_PLAN_V1"
    source_sha = $SourceSha
    python = [ordered]@{
        version = $PythonVersion
        url = $PythonUrl
        sha256 = $PythonSha256
    }
    pip_bootstrap = [ordered]@{
        repository = "pypa/get-pip"
        commit = $GetPipCommit
    }
    service = [ordered]@{
        name = $ServiceName
        root = $ServiceRoot
        bind_host = $BindHost
        bind_port = $BindPort
        allowed_roots = @($AllowedRoot)
        process_enabled = [bool]$EnableProcess
        non_loopback_listener = [bool]$AllowNonLoopbackListener
    }
    controller_export = $ControllerExportDir
    effects_if_applied = [ordered]@{
        installs_windows_service = $true
        sets_service_startup_automatic = $true
        starts_and_restarts_service = $true
        writes_programdata = $true
        creates_persistent_workstation_identity = $true
        creates_controller_export_staging = $true
        changes_firewall = $false
        installs_system_python = $false
        installs_git = $false
    }
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 8
    Write-Host ""
    Write-Host "PLAN ONLY. Re-run with -Apply to perform these effects."
    exit 0
}

if (-not (Test-Administrator)) {
    throw "Run PowerShell as Administrator for -Apply."
}

foreach ($root in $AllowedRoot) {
    if (-not (Test-Path -LiteralPath $root -PathType Container)) {
        throw "Allowed root does not exist: $root"
    }
}

if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    throw "Service $ServiceName already exists. Refusing to overwrite/update."
}
if ((Test-Path $ServiceRoot) -and (Get-ChildItem -Force $ServiceRoot | Select-Object -First 1)) {
    throw "Service root already contains material: $ServiceRoot"
}
if ((Test-Path $ControllerExportDir) -and (Get-ChildItem -Force $ControllerExportDir | Select-Object -First 1)) {
    throw "Controller export directory already contains material: $ControllerExportDir"
}

New-Item -ItemType Directory -Path $StageRoot | Out-Null
New-Item -ItemType Directory -Path $StagePython | Out-Null
New-Item -ItemType Directory -Path $StageSource | Out-Null

$probePath = $null
$serviceInstalled = $false
try {
    Write-Host "Downloading pinned Python runtime..."
    Download-File $PythonUrl $PythonZip $PythonSha256

    Write-Host "Downloading exact VeraMesh source $SourceSha..."
    Download-File $SourceUrl $SourceZip

    Write-Host "Downloading exact PyPA get-pip source $GetPipCommit..."
    Download-File $GetPipUrl $GetPip

    Expand-Archive -Path $SourceZip -DestinationPath $StageSource -Force
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

    Write-Host "Preparing temporary installer runtime..."
    $stagePythonExe = Initialize-PortableRuntime $StagePython $packagePath $constraintsPath $true

    $prepareArgs = @(
        "-m", "veraport_agent.prepare_workstation",
        "--service-root", $ServiceRoot,
        "--controller-export-dir", $ControllerExportDir,
        "--bind-host", $BindHost,
        "--bind-port", "$BindPort",
        "--endpoint-id", "workstation-direct"
    )
    foreach ($root in $AllowedRoot) {
        $prepareArgs += @("--allowed-root", $root)
    }
    if ($AllowNonLoopbackListener) {
        $prepareArgs += "--allow-non-loopback-listener"
    }
    if ($EnableProcess) {
        $prepareArgs += "--enable-process"
    }

    Write-Host "Provisioning persistent workstation identity and controller export..."
    $prepareOutput = & $stagePythonExe @prepareArgs
    Assert-ExitCode "workstation preparation"
    $preparation = (($prepareOutput -join [Environment]::NewLine) | ConvertFrom-Json)

    Write-Host "Installing pinned portable Python runtime under ProgramData..."
    $runtimePython = Initialize-PortableRuntime $RuntimeDir $packagePath $constraintsPath $true

    $serviceCli = Join-Path $RuntimeDir "Scripts\veraport-service.exe"
    if (-not (Test-Path $serviceCli)) {
        throw "VeraPort service entrypoint missing: $serviceCli"
    }

    Write-Host "Registering VeraPortAgent Windows service..."
    & $serviceCli install
    Assert-ExitCode "Windows service registration"
    $serviceInstalled = $true

    Set-Service -Name $ServiceName -StartupType Automatic

    Write-Host "Starting VeraPortAgent..."
    Start-Service -Name $ServiceName
    Wait-ServiceRunning $ServiceName

    $probeToken = "veraport-service-" + [guid]::NewGuid().ToString("N")
    $probePath = Join-Path $AllowedRoot[0] (".veramesh-service-probe-" + [guid]::NewGuid().ToString("N") + ".txt")
    [IO.File]::WriteAllText($probePath, $probeToken, [Text.UTF8Encoding]::new($false))

    $controllerConfig = Join-Path $ControllerExportDir "controller.json"

    Write-Host "Qualifying authenticated service behavior before restart..."
    $beforeRestart = Invoke-ServiceQualification $runtimePython $controllerConfig $probePath $probeToken

    Write-Host "Restarting VeraPortAgent through Windows SCM..."
    Restart-Service -Name $ServiceName -Force
    Wait-ServiceRunning $ServiceName

    Write-Host "Qualifying authenticated service behavior after restart..."
    $afterRestart = Invoke-ServiceQualification $runtimePython $controllerConfig $probePath $probeToken $beforeRestart.workstation_principal

    if ($beforeRestart.workstation_principal -ne $afterRestart.workstation_principal) {
        throw "Persistent workstation identity changed across service restart."
    }

    $receipt = [ordered]@{
        schema = "VERAPORT_WINDOWS_SERVICE_INSTALL_QUALIFICATION_V1"
        pass = $true
        source_sha = $SourceSha
        preparation = $preparation
        service = [ordered]@{
            name = $ServiceName
            startup_type = "Automatic"
            status = (Get-Service -Name $ServiceName).Status.ToString()
            runtime_python = $runtimePython
            runtime_python_sha256 = (Get-FileHash -Algorithm SHA256 $runtimePython).Hash.ToLowerInvariant()
        }
        qualification = [ordered]@{
            before_restart = $beforeRestart
            after_restart = $afterRestart
            principal_stable_across_restart = $true
        }
        controller_export = [ordered]@{
            path = $ControllerExportDir
            transferred = $false
            workstation_staging_copy_removed = $false
        }
        effects = [ordered]@{
            windows_service_installed = $true
            windows_service_started = $true
            service_restart_qualified = $true
            firewall_changed = $false
            system_python_installed = $false
            git_installed = $false
        }
    }

    $receiptJson = $receipt | ConvertTo-Json -Depth 16
    [IO.File]::WriteAllText($ReceiptPath, $receiptJson + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    Write-Host ""
    Write-Output $receiptJson

    if ($RemoveAfterQualification) {
        Write-Host ""
        Write-Host "Removing qualified service because -RemoveAfterQualification was specified..."
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        & $serviceCli remove
        Assert-ExitCode "Windows service removal"
        $serviceInstalled = $false
        Remove-Item $ServiceRoot -Recurse -Force
        Remove-Item $ControllerExportDir -Recurse -Force
    }
}
catch {
    if (Test-Path $ServiceRoot) {
        try {
            $status = $null
            $existing = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
            if ($existing) {
                $status = $existing.Status.ToString()
            }
            $failure = [ordered]@{
                schema = "VERAPORT_WINDOWS_SERVICE_INSTALL_FAILURE_V1"
                pass = $false
                source_sha = $SourceSha
                error = $_.Exception.Message
                service_registered = [bool]$serviceInstalled
                service_status = $status
                preserved_for_reconciliation = $true
            }
            [IO.File]::WriteAllText(
                (Join-Path $ServiceRoot "install-failure-receipt.json"),
                ($failure | ConvertTo-Json -Depth 8) + [Environment]::NewLine,
                [Text.UTF8Encoding]::new($false)
            )
        }
        catch {
        }
    }
    throw
}
finally {
    if ($probePath -and (Test-Path $probePath)) {
        Remove-Item $probePath -Force -ErrorAction SilentlyContinue
    }
    Remove-Item $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
