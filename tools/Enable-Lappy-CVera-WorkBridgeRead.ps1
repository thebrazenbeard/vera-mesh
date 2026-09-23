[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$VeraMeshSourceCommit = "6c38e45d59f4cbdcc827effe905d04574b92b6db"
$Root = "C:\ProgramData\VeraMesh"
$RuntimePython = Join-Path $Root "runtime\python.exe"
$ServiceConfig = Join-Path $Root "veraport.json"
$ControllerConfig = Join-Path $Root "controller.json"
$WorkBridgeConfig = "C:\ProgramData\WorkBridgeMCP\config.json"
$WorkBridgeToken = "C:\ProgramData\WorkBridgeMCP\http-token.txt"
$WorkBridgeLocalConfig = Join-Path $Root "workbridge-local.json"
$ReceiptPath = Join-Path $Root "c-vera-workbridge-read-qualification.json"
$CVera = "C:\Vera"
$StageRoot = Join-Path $env:TEMP ("VeraMesh-CVera-Read-" + [Guid]::NewGuid().ToString("N"))

function Assert-Administrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Run this script from an elevated Administrator PowerShell window."
    }
}

function Read-StrictJson {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required JSON file missing: $Path"
    }
    try {
        return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    catch {
        throw "Cannot parse strict JSON file $Path : $($_.Exception.Message)"
    }
}

function Write-Utf8Json {
    param([string]$Path, [object]$Value)
    $payload = ($Value | ConvertTo-Json -Depth 30) + [Environment]::NewLine
    [IO.File]::WriteAllText($Path, $payload, [Text.UTF8Encoding]::new($false))
}

function Backup-File {
    param([string]$Path, [string]$Tag)
    $backup = $Path + "." + $Tag + ".bak"
    if (Test-Path -LiteralPath $backup) {
        throw "Backup already exists; refusing to overwrite: $backup"
    }
    Copy-Item -LiteralPath $Path -Destination $backup
    return $backup
}

function Expand-GitHubCommit {
    param([string]$Repository, [string]$Commit, [string]$Destination)
    if ($Commit -notmatch '^[0-9a-f]{40}$') {
        throw "Source commit must be full lowercase 40-hex."
    }
    $zip = Join-Path $StageRoot (($Repository -replace '/', '-') + "-" + $Commit + ".zip")
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/$Repository/archive/$Commit.zip" -OutFile $zip
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -LiteralPath $zip -DestinationPath $Destination -Force
    $dirs = @(Get-ChildItem -LiteralPath $Destination -Directory)
    if ($dirs.Count -ne 1) {
        throw "Unexpected source archive layout for $Repository@$Commit."
    }
    return $dirs[0].FullName
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
    throw "Service $Name did not reach RUNNING."
}

function Wait-TaskRunning {
    param([string]$Name, [int]$TimeoutSeconds = 30)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $task = Get-ScheduledTask -TaskName $Name -ErrorAction Stop
        if ($task.State -eq "Running") {
            return
        }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Scheduled task $Name did not reach Running."
}

function Harden-PrivateFile {
    param([string]$Path)
    & icacls.exe $Path /inheritance:r /grant:r '*S-1-5-18:(F)' '*S-1-5-32-544:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to harden ACL for $Path"
    }
}

function Same-StringArray {
    param([object[]]$Left, [object[]]$Right)
    if ($Left.Count -ne $Right.Count) { return $false }
    for ($i = 0; $i -lt $Left.Count; $i++) {
        if ([string]$Left[$i] -cne [string]$Right[$i]) { return $false }
    }
    return $true
}

Assert-Administrator

foreach ($required in @($RuntimePython, $ServiceConfig, $ControllerConfig, $WorkBridgeConfig, $WorkBridgeToken)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Required existing runtime material missing: $required"
    }
}
if (-not (Test-Path -LiteralPath $CVera -PathType Container)) {
    throw "Acceptance root does not exist: $CVera"
}

$veraService = Get-Service -Name "VeraPortAgent" -ErrorAction Stop
$workBridgeTask = Get-ScheduledTask -TaskName "WorkBridgeMCP" -ErrorAction Stop
$serviceWasRunning = $veraService.Status -eq [System.ServiceProcess.ServiceControllerStatus]::Running
$taskWasRunning = $workBridgeTask.State -eq "Running"
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")

New-Item -ItemType Directory -Force -Path $StageRoot | Out-Null
$serviceBackup = $null
$workBridgeBackup = $null
$localConfigBackup = $null
$modifiedService = $false
$modifiedWorkBridge = $false

try {
    Write-Host "=== Pin and stage VeraMesh source ==="
    $source = Expand-GitHubCommit -Repository "thebrazenbeard/vera-mesh" -Commit $VeraMeshSourceCommit -Destination (Join-Path $StageRoot "source")
    $packageRoot = Join-Path $source "reference\veraport_agent"
    $constraints = Join-Path $source "protocol\veraport\v1\windows-runtime-constraints.txt"
    foreach ($required in @(
        (Join-Path $packageRoot "veraport_agent\workbridge_local.py"),
        (Join-Path $packageRoot "veraport_agent\workbridge_local_qualification.py"),
        $constraints
    )) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Pinned source is missing required WorkBridge integration material: $required"
        }
    }

    Write-Host "=== Verify existing authority ceilings ==="
    $service = Read-StrictJson $ServiceConfig
    if ($service.allow_process_exec -eq $true) {
        throw "Refusing activation because VeraPort process execution is enabled."
    }
    $serviceRootsBefore = @($service.allowed_roots | ForEach-Object { [string]$_ })

    $wb = Read-StrictJson $WorkBridgeConfig
    if ($wb.process.enabled -eq $true) {
        throw "Refusing activation because WorkBridge process execution is enabled."
    }
    $wbReadBefore = @($wb.read_roots | ForEach-Object { [string]$_ })
    $wbWriteBefore = @($wb.write_roots | ForEach-Object { [string]$_ })

    Write-Host "=== Stop only the local services being upgraded ==="
    if ($serviceWasRunning) {
        Stop-Service -Name "VeraPortAgent" -Force
    }
    if ($taskWasRunning) {
        Stop-ScheduledTask -TaskName "WorkBridgeMCP"
    }

    Write-Host "=== Upgrade VeraPort service code with pinned MCP client ==="
    & $RuntimePython -m pip install --disable-pip-version-check --constraint $constraints "mcp==1.27.2" | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Pinned MCP runtime installation failed." }

    & $RuntimePython -m pip install --disable-pip-version-check --no-build-isolation --no-deps --force-reinstall $packageRoot | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Pinned VeraPort package reinstall failed." }

    $installedModule = @(& $RuntimePython -c "import veraport_agent.workbridge_local as m; print(m.__file__)")
    if ($LASTEXITCODE -ne 0 -or $installedModule.Count -lt 1) {
        throw "Could not locate installed WorkBridge local adapter."
    }
    $installedPath = ([string]$installedModule[-1]).Trim()
    $sourceAdapter = Join-Path $packageRoot "veraport_agent\workbridge_local.py"
    $sourceSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceAdapter).Hash.ToLowerInvariant()
    $installedSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $installedPath).Hash.ToLowerInvariant()
    if ($sourceSha -ne $installedSha) {
        throw "Installed WorkBridge local adapter does not match pinned source."
    }

    Write-Host "=== Add C:\Vera to WorkBridge READ roots only ==="
    $workBridgeBackup = Backup-File $WorkBridgeConfig ("pre-cvera-" + $stamp)
    $readRoots = @($wbReadBefore)
    $alreadyRead = @($readRoots | Where-Object { $_.TrimEnd('\') -ieq $CVera.TrimEnd('\') }).Count -gt 0
    if (-not $alreadyRead) {
        $readRoots += $CVera
    }
    $wb.read_roots = $readRoots
    Write-Utf8Json $WorkBridgeConfig $wb
    $modifiedWorkBridge = $true

    $wbAfter = Read-StrictJson $WorkBridgeConfig
    $wbWriteAfter = @($wbAfter.write_roots | ForEach-Object { [string]$_ })
    if (-not (Same-StringArray $wbWriteBefore $wbWriteAfter)) {
        throw "WorkBridge write roots changed; refusing broadened write authority."
    }
    if ($wbAfter.process.enabled -eq $true) {
        throw "WorkBridge process execution became enabled."
    }

    Write-Host "=== Configure VeraPort's loopback-only WorkBridge read adapter ==="
    if (Test-Path -LiteralPath $WorkBridgeLocalConfig -PathType Leaf) {
        $localConfigBackup = Backup-File $WorkBridgeLocalConfig ("pre-cvera-" + $stamp)
    }
    $localConfig = [ordered]@{
        schema = "VERAPORT_WORKBRIDGE_LOCAL_V1"
        endpoint = "http://127.0.0.1:8765/mcp"
        bearer_token_file = $WorkBridgeToken
        read_roots = @($CVera)
        timeout_seconds = 10.0
    }
    Write-Utf8Json $WorkBridgeLocalConfig $localConfig
    Harden-PrivateFile $WorkBridgeLocalConfig

    $serviceBackup = Backup-File $ServiceConfig ("pre-cvera-" + $stamp)
    $service = Read-StrictJson $ServiceConfig
    if ($null -eq $service.PSObject.Properties["workbridge_config"]) {
        $service | Add-Member -NotePropertyName "workbridge_config" -NotePropertyValue $WorkBridgeLocalConfig
    }
    else {
        $service.workbridge_config = $WorkBridgeLocalConfig
    }
    Write-Utf8Json $ServiceConfig $service
    Harden-PrivateFile $ServiceConfig
    $modifiedService = $true

    $serviceAfter = Read-StrictJson $ServiceConfig
    $serviceRootsAfter = @($serviceAfter.allowed_roots | ForEach-Object { [string]$_ })
    if (-not (Same-StringArray $serviceRootsBefore $serviceRootsAfter)) {
        throw "VeraPort allowed_roots changed; this activation must not broaden VeraPort roots."
    }
    if ($serviceAfter.allow_process_exec -eq $true) {
        throw "VeraPort process execution became enabled."
    }

    Write-Host "=== Restart WorkBridge and VeraPort ==="
    Start-ScheduledTask -TaskName "WorkBridgeMCP"
    Wait-TaskRunning "WorkBridgeMCP"

    $token = (Get-Content -LiteralPath $WorkBridgeToken -Raw -Encoding UTF8).Trim()
    if ($token.Length -lt 32) { throw "WorkBridge bearer token is invalid." }
    $health = Invoke-RestMethod -Method Get -Uri "http://127.0.0.1:8765/mcp/healthz" -Headers @{ Authorization = "Bearer $token" } -TimeoutSec 10
    if ($health.status -ne "ok") {
        throw "WorkBridge authenticated health did not return status=ok."
    }

    Start-Service -Name "VeraPortAgent"
    Wait-ServiceRunning "VeraPortAgent"

    Write-Host "=== Qualify authenticated VeraPort -> WorkBridge -> C:\Vera ==="
    $qualOutput = @(& $RuntimePython -m veraport_agent.workbridge_local_qualification --controller-config $ControllerConfig --path $CVera)
    if ($LASTEXITCODE -ne 0) {
        throw "Authenticated WorkBridge local qualification failed."
    }
    try {
        $qualification = (($qualOutput | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
    }
    catch {
        throw "WorkBridge local qualification did not return valid JSON."
    }
    if ($qualification.status -ne "PASS" -or $qualification.backend -ne "workbridge") {
        throw "WorkBridge local qualification did not prove the required route."
    }
    if ($qualification.process_execution_requested -eq $true) {
        throw "Qualification unexpectedly used process capability."
    }

    $receipt = [ordered]@{
        schema = "LAPPY_CVERA_WORKBRIDGE_READ_ACTIVATION_V1"
        status = "PASS"
        observed_at = (Get-Date).ToString("o")
        source = [ordered]@{
            repository = "thebrazenbeard/vera-mesh"
            commit = $VeraMeshSourceCommit
            installed_workbridge_adapter_sha256 = $installedSha
        }
        authority = [ordered]@{
            veraport_allowed_roots_before = $serviceRootsBefore
            veraport_allowed_roots_after = $serviceRootsAfter
            veraport_process_enabled = $false
            workbridge_read_roots_before = $wbReadBefore
            workbridge_read_roots_after = @($wbAfter.read_roots | ForEach-Object { [string]$_ })
            workbridge_write_roots_before = $wbWriteBefore
            workbridge_write_roots_after = $wbWriteAfter
            workbridge_process_enabled = $false
            delegated_c_vera_access = "READ_ONLY"
        }
        runtime = [ordered]@{
            WorkBridgeMCP = (Get-ScheduledTask -TaskName "WorkBridgeMCP").State.ToString()
            VeraPortAgent = (Get-Service -Name "VeraPortAgent").Status.ToString()
            workbridge_endpoint = "http://127.0.0.1:8765/mcp"
            public_listener_added = $false
            firewall_changed = $false
            tailscale_changed = $false
        }
        qualification = $qualification
        chatgpt_originated_read_proven = $false
        next_gate = "CONNECT_EXISTING_SECURE_MCP_TUNNEL_AND_CALL_FS_LIST_DIR_C_VERA_FROM_CHATGPT"
    }
    Write-Utf8Json $ReceiptPath $receipt
    Harden-PrivateFile $ReceiptPath

    Write-Host ""
    Write-Host "C:\Vera READ ROUTE QUALIFIED LOCALLY."
    Write-Host "VeraPort roots unchanged; process execution remains OFF."
    Write-Host "WorkBridge write roots unchanged; C:\Vera added to READ roots only."
    Write-Host "Receipt: $ReceiptPath"
    $receipt | ConvertTo-Json -Depth 30
}
catch {
    Write-Warning ("Activation failed: " + $_.Exception.Message)
    if ($modifiedService -and $serviceBackup -and (Test-Path -LiteralPath $serviceBackup)) {
        Copy-Item -LiteralPath $serviceBackup -Destination $ServiceConfig -Force
    }
    if ($modifiedWorkBridge -and $workBridgeBackup -and (Test-Path -LiteralPath $workBridgeBackup)) {
        Copy-Item -LiteralPath $workBridgeBackup -Destination $WorkBridgeConfig -Force
    }
    if ($localConfigBackup -and (Test-Path -LiteralPath $localConfigBackup)) {
        Copy-Item -LiteralPath $localConfigBackup -Destination $WorkBridgeLocalConfig -Force
    }
    elseif (Test-Path -LiteralPath $WorkBridgeLocalConfig -PathType Leaf) {
        Remove-Item -LiteralPath $WorkBridgeLocalConfig -Force -ErrorAction SilentlyContinue
    }
    try {
        if ((Get-ScheduledTask -TaskName "WorkBridgeMCP" -ErrorAction SilentlyContinue).State -ne "Running") {
            Start-ScheduledTask -TaskName "WorkBridgeMCP"
        }
    } catch {}
    try {
        if ((Get-Service -Name "VeraPortAgent" -ErrorAction SilentlyContinue).Status -ne [System.ServiceProcess.ServiceControllerStatus]::Running) {
            Start-Service -Name "VeraPortAgent"
        }
    } catch {}
    throw
}
finally {
    Remove-Item -LiteralPath $StageRoot -Recurse -Force -ErrorAction SilentlyContinue
}
