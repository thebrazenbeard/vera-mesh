[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceCommit = "720a1f446b433825e59f1119405bdfc57bccec8e"
$Root = "C:\ProgramData\VeraMesh"
$ProgramRoot = "C:\Program Files\VeraMesh\VeraPortAgent"
$ServiceConfig = Join-Path $Root "veraport.json"
$ControllerConfig = Join-Path $Root "controller.json"
$TunnelPython = Join-Path $Root "tunnel-runtime\python.exe"
$Doctor = Join-Path $Root "tunnel-runtime\Scripts\veraport-doctor.exe"
$Receipt = Join-Path $Root "chatgpt-rdc-control-activation.json"
$Stage = Join-Path $env:TEMP ("VeraMesh-RDC-Control-" + [guid]::NewGuid().ToString("N"))
$upgrade = $null
$serviceCodeReplaced = $false
$tunnelCodeReplaced = $false
$installedServicePackage = $null
$servicePackageBackup = $null
$installedTunnelPackage = $null
$tunnelPackageBackup = $null
$controllerTrustBefore = $null

function Assert-Admin {
  $id = [Security.Principal.WindowsIdentity]::GetCurrent()
  $p = [Security.Principal.WindowsPrincipal]::new($id)
  if (-not $p.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run from an elevated Administrator PowerShell window."
  }
}

function Wait-ServiceState([string]$Name,[string]$State,[int]$TimeoutSeconds=45) {
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  do {
    $svc = Get-Service -Name $Name -ErrorAction Stop
    if ($svc.Status.ToString() -eq $State) { return }
    Start-Sleep -Milliseconds 500
  } while ([DateTime]::UtcNow -lt $deadline)
  throw "$Name did not reach $State."
}

function Read-Json([string]$Path) {
  Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Download-ExactSource {
  New-Item -ItemType Directory -Force -Path $Stage | Out-Null
  $zip = Join-Path $Stage "source.zip"
  Invoke-WebRequest -UseBasicParsing -Uri ("https://github.com/thebrazenbeard/vera-mesh/archive/" + $SourceCommit + ".zip") -OutFile $zip
  Expand-Archive -LiteralPath $zip -DestinationPath $Stage -Force
  $dirs = @(Get-ChildItem -LiteralPath $Stage -Directory | Where-Object { $_.Name -like "vera-mesh-*" })
  if ($dirs.Count -ne 1) { throw "Unexpected source archive layout." }
  $dirs[0].FullName
}

function Resolve-ServicePackage {
  if (-not (Test-Path -LiteralPath $ProgramRoot -PathType Container)) {
    throw "Existing VeraPort program root missing: $ProgramRoot"
  }
  $matches = @(
    Get-ChildItem -LiteralPath $ProgramRoot -Filter "windows_service.py" -File -Recurse -ErrorAction Stop |
      Where-Object {
        $_.Directory.Name -eq "veraport_agent" -and
        $_.Directory.FullName -notmatch "\\.pre-rdc-"
      }
  )
  if ($matches.Count -ne 1) {
    throw "Expected exactly one active installed veraport_agent package under $ProgramRoot; found $($matches.Count)."
  }
  $matches[0].Directory.FullName
}

function Resolve-TunnelPackage {
  $raw = @(& $TunnelPython -c "import pathlib,veraport_agent; print(pathlib.Path(veraport_agent.__file__).resolve().parent)")
  if ($LASTEXITCODE -ne 0) { throw "Could not resolve tunnel veraport_agent package." }
  $path = (($raw | ForEach-Object { [string]$_ }) -join "").Trim()
  if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Container)) {
    throw "Tunnel veraport_agent package path is invalid: $path"
  }
  $path
}

function Replace-PackageTree([string]$Current,[string]$Source,[string]$Backup) {
  if (-not (Test-Path -LiteralPath $Current -PathType Container)) { throw "Installed package missing: $Current" }
  if (-not (Test-Path -LiteralPath $Source -PathType Container)) { throw "Source package missing: $Source" }
  if (Test-Path -LiteralPath $Backup) { throw "Backup path already exists: $Backup" }
  Move-Item -LiteralPath $Current -Destination $Backup
  try {
    Copy-Item -LiteralPath $Source -Destination $Current -Recurse
  }
  catch {
    if (Test-Path -LiteralPath $Current) { Remove-Item -LiteralPath $Current -Recurse -Force -ErrorAction SilentlyContinue }
    Move-Item -LiteralPath $Backup -Destination $Current
    throw
  }
}

function Restore-PackageTree([string]$Current,[string]$Backup) {
  if (-not $Backup -or -not (Test-Path -LiteralPath $Backup -PathType Container)) { return }
  if (Test-Path -LiteralPath $Current) { Remove-Item -LiteralPath $Current -Recurse -Force }
  Move-Item -LiteralPath $Backup -Destination $Current
}

function Ensure-Running([string]$Name) {
  $svc = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($null -eq $svc) { return }
  if ($svc.Status.ToString() -ne "Running") {
    Start-Service -Name $Name
    Wait-ServiceState $Name "Running"
  }
}

function Restore-PreviousState {
  if ($null -eq $upgrade -and -not $serviceCodeReplaced -and -not $tunnelCodeReplaced) {
    Write-Warning "Activation failed before any VeraPort mutation; rollback is a no-op."
    return
  }
  Write-Warning "Restoring pre-upgrade VeraPort code and authority after failed qualification."
  try { Stop-Service VeraMeshTunnelRuntime -Force -ErrorAction SilentlyContinue } catch {}
  try { Stop-Service VeraPortAgent -Force -ErrorAction SilentlyContinue } catch {}

  if ($null -ne $upgrade) {
    if ($controllerTrustBefore) {
      Copy-Item -LiteralPath ([string]$upgrade.trust_backup) -Destination $controllerTrustBefore -Force
    }
    Copy-Item -LiteralPath ([string]$upgrade.controller_config_backup) -Destination $ControllerConfig -Force
    Copy-Item -LiteralPath ([string]$upgrade.service_config_backup) -Destination $ServiceConfig -Force
  }

  if ($serviceCodeReplaced) {
    Restore-PackageTree $installedServicePackage $servicePackageBackup
    $script:serviceCodeReplaced = $false
  }
  if ($tunnelCodeReplaced) {
    Restore-PackageTree $installedTunnelPackage $tunnelPackageBackup
    $script:tunnelCodeReplaced = $false
  }

  Ensure-Running "VeraPortAgent"
  Ensure-Running "VeraMeshTunnelRuntime"
}

Assert-Admin
if (-not (Test-Path -LiteralPath $ServiceConfig -PathType Leaf)) {
  throw "Existing VeraPort config missing: $ServiceConfig"
}
if (-not (Get-Service VeraPortAgent -ErrorAction SilentlyContinue)) {
  throw "VeraPortAgent service is missing."
}

try {
  Write-Host "=== Download exact full-control source ==="
  $source = Download-ExactSource
  $packageRoot = Join-Path $source "reference\veraport_agent"
  $sourcePackage = Join-Path $packageRoot "veraport_agent"
  $attachScript = Join-Path $source "tools\windows_attach_existing_lappy_veramesh.ps1"
  foreach ($required in @(
    (Join-Path $sourcePackage "controller_full_control_upgrade.py"),
    (Join-Path $sourcePackage "full_control_qualification.py"),
    (Join-Path $sourcePackage "rdc_surface.py"),
    $attachScript
  )) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "Pinned source missing: $required"
    }
  }

  if (-not (Test-Path -LiteralPath $ControllerConfig -PathType Leaf) -or
      -not (Test-Path -LiteralPath $TunnelPython -PathType Leaf) -or
      -not (Get-Service VeraMeshTunnelRuntime -ErrorAction SilentlyContinue)) {
    Write-Host "=== Existing Secure MCP tunnel is incomplete; attach/resume it first ==="
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $attachScript -Apply
    if ($LASTEXITCODE -ne 0) { throw "Secure MCP tunnel attach/resume failed." }
  }

  foreach ($required in @($ControllerConfig,$TunnelPython,$Doctor)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
      throw "Required tunnel material missing after attach: $required"
    }
  }

  $serviceBefore = Read-Json $ServiceConfig
  $rootsBefore = @($serviceBefore.allowed_roots | ForEach-Object { [string]$_ })
  $controllerTrustBefore = [string]$serviceBefore.controller_trust
  $installedServicePackage = Resolve-ServicePackage
  $installedTunnelPackage = Resolve-TunnelPackage

  $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
  $servicePackageBackup = $installedServicePackage + ".pre-rdc-" + $stamp
  $tunnelPackageBackup = $installedTunnelPackage + ".pre-rdc-" + $stamp

  Write-Host "Service package: $installedServicePackage"
  Write-Host "Tunnel package:  $installedTunnelPackage"

  Write-Host "=== Stop only VeraPort/tunnel services for atomic code + authority reload ==="
  if ((Get-Service VeraMeshTunnelRuntime).Status.ToString() -eq "Running") {
    Stop-Service VeraMeshTunnelRuntime -Force
    Wait-ServiceState VeraMeshTunnelRuntime "Stopped"
  }
  if ((Get-Service VeraPortAgent).Status.ToString() -eq "Running") {
    Stop-Service VeraPortAgent -Force
    Wait-ServiceState VeraPortAgent "Stopped"
  }

  Write-Host "=== Replace exact VeraPort code trees with rollback backups ==="
  Replace-PackageTree $installedServicePackage $sourcePackage $servicePackageBackup
  $serviceCodeReplaced = $true
  Replace-PackageTree $installedTunnelPackage $sourcePackage $tunnelPackageBackup
  $tunnelCodeReplaced = $true

  Write-Host "=== Enable exact filesystem + managed-process authority ==="
  $raw = @(& $TunnelPython -m veraport_agent.controller_full_control_upgrade --service-config $ServiceConfig --controller-config $ControllerConfig)
  if ($LASTEXITCODE -ne 0) { throw "Full-control authority upgrade failed." }
  $upgrade = (($raw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json

  $serviceAfter = Read-Json $ServiceConfig
  $rootsAfter = @($serviceAfter.allowed_roots | ForEach-Object { [string]$_ })
  if (($rootsBefore -join "`n") -cne ($rootsAfter -join "`n")) {
    throw "VeraPort allowed roots changed unexpectedly."
  }
  if ($serviceAfter.allow_process_exec -ne $true) {
    throw "VeraPort process execution did not become enabled."
  }

  Write-Host "=== Restart VeraPort and Secure MCP tunnel ==="
  Start-Service VeraPortAgent
  Wait-ServiceState VeraPortAgent "Running"
  Start-Service VeraMeshTunnelRuntime
  Wait-ServiceState VeraMeshTunnelRuntime "Running"

  Write-Host "=== Require authenticated tunnel health ==="
  $doctorResult = $null
  for ($i=1; $i -le 18; $i++) {
    $d = @(& $Doctor --live --tunnel-status)
    if ($LASTEXITCODE -eq 0) {
      try {
        $doctorResult = (($d | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
      } catch {}
      if ($doctorResult -and $doctorResult.ok -eq $true) { break }
    }
    Start-Sleep -Seconds 5
  }
  if (-not $doctorResult -or $doctorResult.ok -ne $true) {
    throw "Secure MCP tunnel did not return to authenticated healthy state."
  }

  Write-Host "=== Prove file control + one-shot + managed process control ==="
  $qraw = @(& $TunnelPython -m veraport_agent.full_control_qualification --controller-config $ControllerConfig --root "C:\Temp")
  if ($LASTEXITCODE -ne 0) { throw "Full-control VeraPort qualification failed." }
  $qualification = (($qraw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
  if ($qualification.status -ne "PASS") {
    throw "Full-control qualification did not return PASS."
  }

  $receiptDoc = [ordered]@{
    schema = "VERAMESH_CHATGPT_RDC_CONTROL_ACTIVATION_V2"
    status = "PASS"
    observed_at = (Get-Date).ToString("o")
    source_commit = $SourceCommit
    authority = [ordered]@{
      requested_capabilities = @($upgrade.capabilities_after)
      process_execution_enabled = $true
      allowed_roots_before = $rootsBefore
      allowed_roots_after = $rootsAfter
    }
    code = [ordered]@{
      service_package = $installedServicePackage
      service_backup = $servicePackageBackup
      tunnel_package = $installedTunnelPackage
      tunnel_backup = $tunnelPackageBackup
    }
    services = [ordered]@{
      VeraPortAgent = (Get-Service VeraPortAgent).Status.ToString()
      VeraMeshTunnelRuntime = (Get-Service VeraMeshTunnelRuntime).Status.ToString()
    }
    doctor = $doctorResult
    qualification = $qualification
    next_gate = "SELECT_SECURE_MCP_TUNNEL_IN_CHATGPT_AND_RUN_TOOL_CALL"
  }
  [IO.File]::WriteAllText(
    $Receipt,
    ($receiptDoc | ConvertTo-Json -Depth 30) + [Environment]::NewLine,
    [Text.UTF8Encoding]::new($false)
  )
  Write-Host ""
  Write-Host "VERA -> LAPPY RDC-CLASS CONTROL LOCALLY QUALIFIED."
  Write-Host "Receipt: $Receipt"
  $receiptDoc | ConvertTo-Json -Depth 30
}
catch {
  try {
    Restore-PreviousState
  }
  catch {
    Write-Warning ("Rollback failed: " + $_.Exception.Message)
  }
  throw
}
finally {
  try { Ensure-Running "VeraPortAgent" } catch {}
  try { Ensure-Running "VeraMeshTunnelRuntime" } catch {}
  Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
}
