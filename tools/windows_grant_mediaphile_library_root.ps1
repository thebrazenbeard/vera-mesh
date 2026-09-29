[CmdletBinding()]
param(
    [string]$TargetMappedPath = "Z:\Library",
    [string]$ConfigPath = "C:\ProgramData\VeraMesh\veraport.json",
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Fail([string]$Message) {
    throw $Message
}

function Get-DriveMapping([string]$DriveLetter) {
    $drive = $DriveLetter.TrimEnd(":").ToUpperInvariant()
    if ($drive -notmatch '^[A-Z]$') {
        Fail "Drive letter must be A-Z."
    }

    $found = @()
    Get-ChildItem -LiteralPath Registry::HKEY_USERS -ErrorAction Stop | ForEach-Object {
        $sid = $_.PSChildName
        if ($sid -notmatch '^S-1-5-21-') { return }
        $key = "Registry::HKEY_USERS\$sid\Network\$drive"
        if (-not (Test-Path -LiteralPath $key)) { return }
        try {
            $remote = [string](Get-ItemProperty -LiteralPath $key -Name RemotePath -ErrorAction Stop).RemotePath
            if (-not [string]::IsNullOrWhiteSpace($remote)) {
                $found += [pscustomobject]@{ sid = $sid; remote_path = $remote.TrimEnd("\") }
            }
        } catch {
        }
    }

    $unique = @($found | Group-Object { $_.remote_path.ToLowerInvariant() } | ForEach-Object { $_.Group[0] })
    if ($unique.Count -eq 0) {
        Fail "No loaded user hive contains a mapping for drive $($drive):."
    }
    if ($unique.Count -ne 1) {
        $paths = ($unique | ForEach-Object { $_.remote_path }) -join ", "
        Fail "Drive $($drive): has multiple distinct loaded-user mappings: $paths"
    }
    return $unique[0]
}

function Resolve-TargetUnc([string]$MappedPath) {
    if ($MappedPath -notmatch '^(?<drive>[A-Za-z]):\\(?<relative>.*)$') {
        Fail "TargetMappedPath must be an absolute mapped-drive path like Z:\Library."
    }

    $driveLetter = [string]$Matches.drive
    $relative = [string]$Matches.relative
    $mapping = Get-DriveMapping -DriveLetter $driveLetter
    $target = $mapping.remote_path
    if (-not [string]::IsNullOrWhiteSpace($relative)) {
        $target = $target + "\" + $relative.TrimStart("\")
    }

    if ($target -notmatch '^\\\\[^\\]+\\[^\\]+(?:\\.*)?$') {
        Fail "Resolved target is not a UNC path."
    }

    return [pscustomobject]@{
        drive = $driveLetter.ToUpperInvariant()
        mapped_path = $MappedPath
        mapping_sid = $mapping.sid
        remote_root = $mapping.remote_path
        unc_path = $target.TrimEnd("\")
    }
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not [string]::Equals($identity.Name, "NT AUTHORITY\SYSTEM", [StringComparison]::OrdinalIgnoreCase)) {
    Fail "This repair must run as NT AUTHORITY\SYSTEM so UNC accessibility is tested under the same identity as VeraPortAgent."
}

if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
    Fail "VeraPort config not found: $ConfigPath"
}

$resolved = Resolve-TargetUnc -MappedPath $TargetMappedPath

if (-not (Test-Path -LiteralPath $resolved.unc_path -PathType Container)) {
    Fail "Resolved UNC path is not accessible to LocalSystem: $($resolved.unc_path)"
}

$configRaw = [IO.File]::ReadAllText($ConfigPath)
$config = $configRaw | ConvertFrom-Json

$currentRoots = @($config.allowed_roots)
if ($currentRoots.Count -lt 1) {
    Fail "VeraPort config has no allowed_roots."
}

$alreadyPresent = $false
foreach ($root in $currentRoots) {
    if ([string]::Equals([string]$root, $resolved.unc_path, [StringComparison]::OrdinalIgnoreCase)) {
        $alreadyPresent = $true
        break
    }
}

$plan = [ordered]@{
    schema = "MEDIAPHILE_VERAPORT_LIBRARY_ROOT_REPAIR_V1"
    principal = $identity.Name
    config_path = $ConfigPath
    target_mapped_path = $resolved.mapped_path
    mapping_sid = $resolved.mapping_sid
    remote_root = $resolved.remote_root
    resolved_unc_path = $resolved.unc_path
    unc_access_as_local_system = $true
    current_allowed_roots = @($currentRoots)
    already_present = $alreadyPresent
    effect_if_applied = if ($alreadyPresent) { "NO_CHANGE" } else { "ADD_EXACT_UNC_ROOT_AND_RESTART_REQUIRED" }
}

if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 8
    exit 0
}

if (-not $alreadyPresent) {
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = "$ConfigPath.pre-mediaphile-library-$timestamp.bak"
    Copy-Item -LiteralPath $ConfigPath -Destination $backup -Force

    $newRoots = @($currentRoots) + @($resolved.unc_path)
    $config.allowed_roots = @($newRoots)

    $tmp = "$ConfigPath.mediaphile.tmp"
    $utf8NoBom = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [IO.File]::WriteAllText($tmp, (($config | ConvertTo-Json -Depth 32) + [Environment]::NewLine), $utf8NoBom)
    Move-Item -LiteralPath $tmp -Destination $ConfigPath -Force

    $readback = (Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json)
    $matched = @($readback.allowed_roots | Where-Object {
        [string]::Equals([string]$_, $resolved.unc_path, [StringComparison]::OrdinalIgnoreCase)
    })
    if ($matched.Count -ne 1) {
        Copy-Item -LiteralPath $backup -Destination $ConfigPath -Force
        Fail "Config readback did not contain exactly one expected UNC root; backup restored."
    }

    $plan.backup_path = $backup
}

$plan.applied = $true
$plan.restart_required = (-not $alreadyPresent)
$plan | ConvertTo-Json -Depth 8
