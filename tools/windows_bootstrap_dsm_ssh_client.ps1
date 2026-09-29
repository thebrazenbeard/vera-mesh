[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string]$HostName,
    [Parameter(Mandatory = $true)] [string]$UserName,
    [string]$Alias = "thesimsvault",
    [int]$Port = 22,
    [string]$ExpectedHostKeyFingerprint = "",
    [string]$StateRoot = "$env:ProgramData\VeraMesh\ssh-client",
    [switch]$InspectHostKey,
    [switch]$Apply,
    [switch]$TestConnection
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
function Fail([string]$Message) { throw $Message }
foreach($name in @("ssh.exe","ssh-keygen.exe","ssh-keyscan.exe")) { if($null -eq (Get-Command $name -ErrorAction SilentlyContinue)) { Fail "$name is required." } }
if($Port -lt 1 -or $Port -gt 65535) { Fail "Port outside 1..65535." }
if([string]::IsNullOrWhiteSpace($Alias) -or $Alias.Contains(" ")) { Fail "Alias must be a non-empty single token." }
$keyPath = Join-Path $StateRoot "operator_ed25519"
$publicKeyPath = $keyPath + ".pub"
$knownHosts = Join-Path $StateRoot "known_hosts"
$configPath = Join-Path $StateRoot "config"
function Get-ScannedKeys {
    $lines = @(& ssh-keyscan.exe -p $Port $HostName 2>$null)
    $out = @()
    foreach($line in $lines) {
        if([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) { continue }
        $tmp = [IO.Path]::GetTempFileName()
        try {
            Set-Content -LiteralPath $tmp -Value $line -Encoding ascii
            $info = & ssh-keygen.exe -lf $tmp -E sha256
            if($LASTEXITCODE -ne 0) { continue }
            $parts = $info -split "\s+"
            if($parts.Count -lt 4) { continue }
            $out += [pscustomobject]@{ line=$line; fingerprint=$parts[1]; key_type=$parts[3] }
        } finally { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
    }
    return @($out)
}
if($InspectHostKey) {
    $keys = @(Get-ScannedKeys)
    if($keys.Count -eq 0) { Fail "No SSH host keys were returned by $HostName`:$Port." }
    [ordered]@{ schema="VERAMESH_SSH_HOST_KEY_INSPECTION_V1"; host=$HostName; port=$Port; keys=$keys } | ConvertTo-Json -Depth 8
    exit 0
}
$plan=[ordered]@{ schema="VERAMESH_WINDOWS_DSM_SSH_CLIENT_PLAN_V1"; host=$HostName; port=$Port; user=$UserName; alias=$Alias; state_root=$StateRoot; private_key=$keyPath; public_key=$publicKeyPath; expected_host_key_fingerprint=$ExpectedHostKeyFingerprint; apply=[bool]$Apply; test_connection=[bool]$TestConnection }
if(-not $Apply) { $plan | ConvertTo-Json -Depth 8; exit 0 }
if([string]::IsNullOrWhiteSpace($ExpectedHostKeyFingerprint) -or -not $ExpectedHostKeyFingerprint.StartsWith("SHA256:")) { Fail "-Apply requires an expected SHA256 host-key fingerprint obtained through a separately reviewed inspection." }
$keys=@(Get-ScannedKeys)
$match=@($keys | Where-Object { $_.fingerprint -eq $ExpectedHostKeyFingerprint })
if($match.Count -ne 1) { Fail "Expected host-key fingerprint was not returned exactly once by the target." }
New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
& icacls.exe $StateRoot /inheritance:r /grant:r "*S-1-5-18:(OI)(CI)(F)" "*S-1-5-32-544:(OI)(CI)(F)" | Out-Null
if($LASTEXITCODE -ne 0) { Fail "Failed to secure SSH client state directory." }
if(-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    & ssh-keygen.exe -q -t ed25519 -f $keyPath -N "" -C ("VeraMesh operator " + $env:COMPUTERNAME)
    if($LASTEXITCODE -ne 0) { Fail "ssh-keygen failed." }
}
if(-not (Test-Path -LiteralPath $publicKeyPath -PathType Leaf)) { Fail "Public key was not created." }
Set-Content -LiteralPath $knownHosts -Value $match[0].line -Encoding ascii
$id = $keyPath.Replace("\","/")
$kh = $knownHosts.Replace("\","/")
$cfg = @(
    "Host $Alias",
    "  HostName $HostName",
    "  Port $Port",
    "  User $UserName",
    "  IdentityFile $id",
    "  IdentitiesOnly yes",
    "  BatchMode yes",
    "  StrictHostKeyChecking yes",
    "  UserKnownHostsFile $kh",
    "  ConnectTimeout 10",
    "",
    "Host $Alias-root",
    "  HostName $HostName",
    "  Port $Port",
    "  User root",
    "  IdentityFile $id",
    "  IdentitiesOnly yes",
    "  BatchMode yes",
    "  StrictHostKeyChecking yes",
    "  UserKnownHostsFile $kh",
    "  ConnectTimeout 10"
)
Set-Content -LiteralPath $configPath -Value ($cfg -join [Environment]::NewLine) -Encoding ascii
foreach($path in @($keyPath,$publicKeyPath,$knownHosts,$configPath)) {
    & icacls.exe $path /inheritance:r /grant:r "*S-1-5-18:(F)" "*S-1-5-32-544:(F)" | Out-Null
    if($LASTEXITCODE -ne 0) { Fail "Failed to secure $path." }
}
$plan["status"]="CONFIGURED"
$plan["public_key"]=(Get-Content -LiteralPath $publicKeyPath -Raw).Trim()
if($TestConnection) {
    $output = & ssh.exe -F $configPath $Alias "hostname; id -u" 2>&1
    if($LASTEXITCODE -ne 0) { Fail ("SSH test failed: " + ($output -join " ")) }
    $plan["test_status"]="PASS"; $plan["test_output"]=@($output)
}
$plan | ConvertTo-Json -Depth 8
