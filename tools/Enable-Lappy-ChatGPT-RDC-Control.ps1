[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$SourceCommit = "720a1f446b433825e59f1119405bdfc57bccec8e"
$Root = "C:\ProgramData\VeraMesh"
$ServiceConfig = Join-Path $Root "veraport.json"
$ControllerConfig = Join-Path $Root "controller.json"
$TunnelPython = Join-Path $Root "tunnel-runtime\python.exe"
$Doctor = Join-Path $Root "tunnel-runtime\Scripts\veraport-doctor.exe"
$Receipt = Join-Path $Root "chatgpt-rdc-control-activation.json"
$Stage = Join-Path $env:TEMP ("VeraMesh-RDC-Control-" + [guid]::NewGuid().ToString("N"))

$upgrade = $null
$tunnelCodeReplaced = $false
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
  $svc = Get-CimInstance Win32_Service -Filter "Name='VeraPortAgent'"
  if (-not $svc) { throw "VeraPortAgent Win32 service metadata missing." }
  $serviceHost = ([string]$svc.PathName).Trim().Trim('"')
  if (-not (Test-Path -LiteralPath $serviceHost -PathType Leaf)) {
    throw "VeraPort service host missing: $serviceHost"
  }
  $pythonHome = Split-Path -Parent $serviceHost
  $pkg = Join-Path $pythonHome "Lib\site-packages\veraport_agent"
  if (-not (Test-Path -LiteralPath $pkg -PathType Container)) {
    throw "Installed VeraPort package missing from service Python site-packages: $pkg"
  }
  $pkg
}

function Assert-ServiceSurface([string]$PackagePath) {
  $rdc = Join-Path $PackagePath "rdc_surface.py"
  $protocol = Join-Path $PackagePath "protocol.py"
  foreach ($p in @($rdc,$protocol)) {
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "Installed VeraPort surface file missing: $p" }
  }
  $rdcText = Get-Content -LiteralPath $rdc -Raw -Encoding UTF8
  $protocolText = Get-Content -LiteralPath $protocol -Raw -Encoding UTF8
  foreach ($needle in @("async def process_start","async def process_output","async def process_input","async def process_terminate")) {
    if ($rdcText -notmatch [regex]::Escape($needle)) { throw "Installed VeraPort RDC surface missing: $needle" }
  }
  foreach ($needle in @('"process.start"','"process.output"','"process.input"','"process.terminate"')) {
    if ($protocolText -notmatch [regex]::Escape($needle)) { throw "Installed VeraPort protocol missing: $needle" }
  }
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
  if ($null -eq $upgrade -and -not $tunnelCodeReplaced) {
    Write-Warning "Activation failed before any VeraPort mutation; rollback is a no-op."
    return
  }
  Write-Warning "Restoring pre-upgrade VeraPort authority/tunnel runtime after failed qualification."
  try { Stop-Service VeraMeshTunnelRuntime -Force -ErrorAction SilentlyContinue } catch {}
  try { Stop-Service VeraPortAgent -Force -ErrorAction SilentlyContinue } catch {}

  if ($null -ne $upgrade) {
    if ($controllerTrustBefore) {
      Copy-Item -LiteralPath ([string]$upgrade.trust_backup) -Destination $controllerTrustBefore -Force
    }
    Copy-Item -LiteralPath ([string]$upgrade.controller_config_backup) -Destination $ControllerConfig -Force
    Copy-Item -LiteralPath ([string]$upgrade.service_config_backup) -Destination $ServiceConfig -Force
  }

  if ($tunnelCodeReplaced) {
    Restore-PackageTree $installedTunnelPackage $tunnelPackageBackup
    $script:tunnelCodeReplaced = $false
  }

  Ensure-Running "VeraPortAgent"
  Ensure-Running "VeraMeshTunnelRuntime"
}

Assert-Admin
if (-not (Test-Path -LiteralPath $ServiceConfig -PathType Leaf)) { throw "Existing VeraPort config missing: $ServiceConfig" }
if (-not (Get-Service VeraPortAgent -ErrorAction SilentlyContinue)) { throw "VeraPortAgent service is missing." }
if (-not (Get-Service VeraMeshTunnelRuntime -ErrorAction SilentlyContinue)) { throw "VeraMeshTunnelRuntime service is missing." }
foreach ($required in @($ControllerConfig,$TunnelPython,$Doctor)) {
  if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required tunnel material missing: $required" }
}

try {
  Write-Host "=== Preflight installed VeraPort RDC surface ==="
  $installedServicePackage = Resolve-ServicePackage
  Assert-ServiceSurface $installedServicePackage
  Write-Host "Service package: $installedServicePackage"

  Write-Host "=== Download exact controller/tunnel source ==="
  $source = Download-ExactSource
  $sourcePackage = Join-Path $source "reference\veraport_agent\veraport_agent"
  foreach ($required in @(
    (Join-Path $sourcePackage "controller_full_control_upgrade.py"),
    (Join-Path $sourcePackage "full_control_qualification.py")
  )) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Pinned source missing: $required" }
  }

  $serviceBefore = Read-Json $ServiceConfig
  $rootsBefore = @($serviceBefore.allowed_roots | ForEach-Object { [string]$_ })
  $controllerTrustBefore = [string]$serviceBefore.controller_trust
  $installedTunnelPackage = Resolve-TunnelPackage
  $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ")
  $tunnelPackageBackup = $installedTunnelPackage + ".pre-rdc-" + $stamp
  Write-Host "Tunnel package:  $installedTunnelPackage"

  Write-Host "=== Stop only VeraPort/tunnel services for bounded authority reload ==="
  if ((Get-Service VeraMeshTunnelRuntime).Status.ToString() -eq "Running") {
    Stop-Service VeraMeshTunnelRuntime -Force
    Wait-ServiceState VeraMeshTunnelRuntime "Stopped"
  }
  if ((Get-Service VeraPortAgent).Status.ToString() -eq "Running") {
    Stop-Service VeraPortAgent -Force
    Wait-ServiceState VeraPortAgent "Stopped"
  }

  Write-Host "=== Update only tunnel-side VeraPort control code ==="
  Replace-PackageTree $installedTunnelPackage $sourcePackage $tunnelPackageBackup
  $tunnelCodeReplaced = $true

  Write-Host "=== Enable exact filesystem + managed-process authority ==="
  $raw = @(& $TunnelPython -m veraport_agent.controller_full_control_upgrade --service-config $ServiceConfig --controller-config $ControllerConfig)
  if ($LASTEXITCODE -ne 0) { throw "Full-control authority upgrade failed." }
  $upgrade = (($raw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json

  $serviceAfter = Read-Json $ServiceConfig
  $rootsAfter = @($serviceAfter.allowed_roots | ForEach-Object { [string]$_ })
  if (($rootsBefore -join "`n") -cne ($rootsAfter -join "`n")) { throw "VeraPort allowed roots changed unexpectedly." }
  if ($serviceAfter.allow_process_exec -ne $true) { throw "VeraPort process execution did not become enabled." }

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
      try { $doctorResult = (($d | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json } catch {}
      if ($doctorResult -and $doctorResult.ok -eq $true) { break }
    }
    Start-Sleep -Seconds 5
  }
  if (-not $doctorResult -or $doctorResult.ok -ne $true) { throw "Secure MCP tunnel did not return to authenticated healthy state." }

  Write-Host "=== Prove file control + one-shot + managed process control ==="
  $qraw = @(& $TunnelPython -m veraport_agent.full_control_qualification --controller-config $ControllerConfig --root "C:\Temp")
  if ($LASTEXITCODE -ne 0) { throw "Full-control VeraPort qualification failed." }
  $qualification = (($qraw | ForEach-Object { [string]$_ }) -join [Environment]::NewLine) | ConvertFrom-Json
  if ($qualification.status -ne "PASS") { throw "Full-control qualification did not return PASS." }

  $receiptDoc = [ordered]@{
    schema = "VERAMESH_CHATGPT_RDC_CONTROL_ACTIVATION_V3"
    status = "PASS"
    observed_at = (Get-Date).ToString("o")
    source_commit = $SourceCommit
    authority = [ordered]@{
      requested_capabilities = @($upgrade.capabilities_after)
      process_execution_enabled = $true
      allowed_roots_before = $rootsBefore
      allowed_roots_after = $rootsAfter
    }
    service = [ordered]@{
      installed_package = $installedServicePackage
      package_code_replaced = $false
    }
    tunnel = [ordered]@{
      installed_package = $installedTunnelPackage
      backup = $tunnelPackageBackup
      package_code_replaced = $true
    }
    services = [ordered]@{
      VeraPortAgent = (Get-Service VeraPortAgent).Status.ToString()
      VeraMeshTunnelRuntime = (Get-Service VeraMeshTunnelRuntime).Status.ToString()
    }
    doctor = $doctorResult
    qualification = $qualification
    next_gate = "SELECT_SECURE_MCP_TUNNEL_IN_CHATGPT_AND_RUN_TOOL_CALL"
  }
  [IO.File]::WriteAllText($Receipt, ($receiptDoc | ConvertTo-Json -Depth 30) + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
  Write-Host ""
  Write-Host "VERA -> LAPPY RDC-CLASS CONTROL LOCALLY QUALIFIED."
  Write-Host "Receipt: $Receipt"
  $receiptDoc | ConvertTo-Json -Depth 30
}
catch {
  try { Restore-PreviousState } catch { Write-Warning ("Rollback failed: " + $_.Exception.Message) }
  throw
}
finally {
  try { Ensure-Running "VeraPortAgent" } catch {}
  try { Ensure-Running "VeraMeshTunnelRuntime" } catch {}
  Remove-Item -LiteralPath $Stage -Recurse -Force -ErrorAction SilentlyContinue
}
