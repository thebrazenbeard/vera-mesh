[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$PublicKey,
    [string]$UserName = $env:USERNAME,
    [string[]]$RemoteAddress = @("LocalSubnet", "100.64.0.0/10"),
    [switch]$DisablePasswordAuthentication,
    [switch]$Apply
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
function Fail([string]$Message) { throw $Message }
function Test-PublicKey([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value) -or $Value.Contains([char]13) -or $Value.Contains([char]10)) { return $false }
    return $Value -match '^(ssh-ed25519|ecdsa-sha2-nistp(256|384|521)|sk-ssh-ed25519@openssh\.com|sk-ecdsa-sha2-nistp256@openssh\.com|ssh-rsa) [A-Za-z0-9+/=]+(?: .*)?$'
}
if (-not (Test-PublicKey $PublicKey)) { Fail "PublicKey is not a supported single-line OpenSSH public key." }
$user = Get-LocalUser -Name $UserName -ErrorAction Stop
$adminGroup = Get-LocalGroup -SID "S-1-5-32-544" -ErrorAction Stop
$adminMembers = @(Get-LocalGroupMember -Group $adminGroup -ErrorAction Stop)
$userNames = @($UserName.ToLowerInvariant(), "$env:COMPUTERNAME\$UserName".ToLowerInvariant())
$isAdmin = $false
foreach ($member in $adminMembers) { if ($userNames -contains $member.Name.ToLowerInvariant()) { $isAdmin = $true; break } }
if (-not $isAdmin) { Fail "Operator user must already be a member of the local Administrators group." }
$cap = Get-WindowsCapability -Online -Name "OpenSSH.Server*" | Select-Object -First 1
if ($null -eq $cap) { Fail "Windows OpenSSH.Server capability was not found." }
$programDataSsh = Join-Path $env:ProgramData "ssh"
$configPath = Join-Path $programDataSsh "sshd_config"
$keyPath = Join-Path $programDataSsh "administrators_authorized_keys"
$sshd = Join-Path $env:WINDIR "System32\OpenSSH\sshd.exe"
$firewallRule = "OpenSSH-Server-In-TCP"
$plan = [ordered]@{ schema="VERAMESH_WINDOWS_SSH_OPERATOR_PLAN_V1"; user=$UserName; user_enabled=$user.Enabled; user_is_administrator=$isAdmin; openssh_server_state=$cap.State; public_key_target=$keyPath; allow_users=$UserName.ToLowerInvariant(); remote_address=@($RemoteAddress); password_authentication_will_be_disabled=[bool]$DisablePasswordAuthentication; apply=[bool]$Apply }
if (-not $Apply) { $plan | ConvertTo-Json -Depth 8; exit 0 }
if (-not $user.Enabled) { Fail "Operator user is disabled." }
$backupRoot = Join-Path $env:ProgramData ("VeraMesh\ssh-backups\" + (Get-Date -Format "yyyyMMdd-HHmmss"))
New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
$configBackup=$null; $keyBackup=$null; $oldFirewallRemote=$null; $firewallExisted=$false
try {
    if ($cap.State -ne "Installed") {
        $result = Add-WindowsCapability -Online -Name $cap.Name
        if ($result.RestartNeeded) { Fail "OpenSSH.Server installation requires a Windows restart before SSH can be configured safely." }
    }
    if (-not (Test-Path -LiteralPath $sshd -PathType Leaf)) { Fail "sshd.exe is missing after OpenSSH.Server installation." }
    New-Item -ItemType Directory -Path $programDataSsh -Force | Out-Null
    if (Test-Path -LiteralPath $configPath -PathType Leaf) {
        $configBackup=Join-Path $backupRoot "sshd_config"; Copy-Item -LiteralPath $configPath -Destination $configBackup -Force; $config=Get-Content -LiteralPath $configPath -Raw
    } else {
        $defaultConfig=Join-Path $env:WINDIR "System32\OpenSSH\sshd_config_default"
        if (-not (Test-Path -LiteralPath $defaultConfig -PathType Leaf)) { Fail "Neither sshd_config nor sshd_config_default exists." }
        $config=Get-Content -LiteralPath $defaultConfig -Raw
    }
    if (Test-Path -LiteralPath $keyPath -PathType Leaf) {
        $keyBackup=Join-Path $backupRoot "administrators_authorized_keys"; Copy-Item -LiteralPath $keyPath -Destination $keyBackup -Force; $existingKeys=@(Get-Content -LiteralPath $keyPath)
    } else { $existingKeys=@() }
    if ($existingKeys -notcontains $PublicKey) { @($existingKeys + $PublicKey) | Set-Content -LiteralPath $keyPath -Encoding ascii }
    & icacls.exe $keyPath /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Failed to apply administrators_authorized_keys ACL." }
    $begin="# BEGIN VERAMESH SSH OPERATOR PLANE"; $end="# END VERAMESH SSH OPERATOR PLANE"
    $escapedBegin=[regex]::Escape($begin); $escapedEnd=[regex]::Escape($end)
    $config=[regex]::Replace($config, "(?ms)^"+$escapedBegin+".*?^"+$escapedEnd+"\s*", "")
    $block=@($begin,"PubkeyAuthentication yes","AllowUsers "+$UserName.ToLowerInvariant())
    if ($DisablePasswordAuthentication) { $block += "PasswordAuthentication no"; $block += "KbdInteractiveAuthentication no" }
    $block += $end
    $nl=[Environment]::NewLine; $newConfig=$config.TrimEnd()+$nl+$nl+($block -join $nl)+$nl
    Set-Content -LiteralPath $configPath -Value $newConfig -Encoding ascii
    & $sshd -t -f $configPath
    if ($LASTEXITCODE -ne 0) { Fail "sshd_config validation failed." }
    $rule=Get-NetFirewallRule -Name $firewallRule -ErrorAction SilentlyContinue
    if ($null -eq $rule) {
        New-NetFirewallRule -Name $firewallRule -DisplayName "OpenSSH SSH Server (sshd)" -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22 -Profile Any -RemoteAddress $RemoteAddress | Out-Null
    } else {
        $firewallExisted=$true; $oldFirewallRemote=@($rule | Get-NetFirewallAddressFilter | Select-Object -ExpandProperty RemoteAddress)
        Set-NetFirewallRule -Name $firewallRule -Enabled True -Profile Any | Out-Null
        Set-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -RemoteAddress $RemoteAddress | Out-Null
    }
    Set-Service -Name sshd -StartupType Automatic; Start-Service -Name sshd
    $service=Get-Service -Name sshd; if ($service.Status -ne "Running") { Fail "sshd did not reach Running state." }
    $listener=Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $listener) { Fail "sshd is running but no TCP/22 listener was observed." }
    $plan["status"]="PASS"; $plan["backup_root"]=$backupRoot; $plan["sshd_status"]=$service.Status.ToString(); $plan["listener_local_address"]=$listener.LocalAddress
    $plan | ConvertTo-Json -Depth 8
} catch {
    if ($null -ne $configBackup -and (Test-Path -LiteralPath $configBackup)) { Copy-Item -LiteralPath $configBackup -Destination $configPath -Force }
    if ($null -ne $keyBackup -and (Test-Path -LiteralPath $keyBackup)) { Copy-Item -LiteralPath $keyBackup -Destination $keyPath -Force } elseif ($null -eq $keyBackup -and (Test-Path -LiteralPath $keyPath)) { Remove-Item -LiteralPath $keyPath -Force -ErrorAction SilentlyContinue }
    if ($firewallExisted -and $null -ne $oldFirewallRemote) { $rule=Get-NetFirewallRule -Name $firewallRule -ErrorAction SilentlyContinue; if ($null -ne $rule) { Set-NetFirewallAddressFilter -AssociatedNetFirewallRule $rule -RemoteAddress $oldFirewallRemote -ErrorAction SilentlyContinue | Out-Null } } elseif (-not $firewallExisted) { Remove-NetFirewallRule -Name $firewallRule -ErrorAction SilentlyContinue }
    throw
}
