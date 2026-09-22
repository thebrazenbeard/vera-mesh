param(
    [string]$ServiceRoot = "C:\ProgramData\VeraMesh",
    [string]$ProgramRoot = "C:\Program Files\VeraMesh\VeraPortAgent"
)

$ErrorActionPreference = "Stop"

function Get-SafeHash([string]$Path) {
    try {
        return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
    }
    catch {
        return $null
    }
}

function Get-FileObservation([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return [ordered]@{
            path = $Path
            exists = $false
        }
    }
    $item = Get-Item -LiteralPath $Path -Force
    $value = [ordered]@{
        path = $item.FullName
        exists = $true
        kind = $(if ($item.PSIsContainer) { "directory" } else { "file" })
        last_write_time_utc = $item.LastWriteTimeUtc.ToString("o")
    }
    if (-not $item.PSIsContainer) {
        $value.length = $item.Length
        $value.sha256 = Get-SafeHash $item.FullName
    }
    return $value
}

function Read-JsonSafe([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json)
    }
    catch {
        return [ordered]@{
            read_error = $_.Exception.Message
        }
    }
}

function Get-AclObservation([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }
    try {
        $acl = Get-Acl -LiteralPath $Path
        return [ordered]@{
            path = $Path
            owner = $acl.Owner
            access = @(
                $acl.Access | ForEach-Object {
                    [ordered]@{
                        identity = $_.IdentityReference.Value
                        rights = $_.FileSystemRights.ToString()
                        type = $_.AccessControlType.ToString()
                        inherited = [bool]$_.IsInherited
                        inheritance_flags = $_.InheritanceFlags.ToString()
                        propagation_flags = $_.PropagationFlags.ToString()
                    }
                }
            )
        }
    }
    catch {
        return [ordered]@{
            path = $Path
            error = $_.Exception.Message
        }
    }
}

$service = Get-CimInstance Win32_Service -Filter "Name='VeraPortAgent'" -ErrorAction SilentlyContinue
$pythonClass = $null
try {
    $pythonClass = (Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\VeraPortAgent\PythonClass" -ErrorAction Stop)."(default)"
}
catch {
}

$configCandidates = @(
    (Join-Path $ServiceRoot "veraport.json"),
    (Join-Path $ServiceRoot "config.json")
)
$configPath = $configCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
$config = $null
if ($configPath) {
    $config = Read-JsonSafe $configPath
}

$stateCandidates = @()
if ($config -and $config.state_db) {
    $stateCandidates += [string]$config.state_db
}
$stateCandidates += @(
    (Join-Path $ServiceRoot "state\veraport.sqlite3"),
    (Join-Path $ServiceRoot "state\agent.sqlite3")
)
$statePath = $stateCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1

$listener = @()
$bindPort = $null
if ($config -and $config.bind_port) {
    $bindPort = [int]$config.bind_port
    try {
        $listener = @(
            Get-NetTCPConnection -State Listen -LocalPort $bindPort -ErrorAction Stop |
                ForEach-Object {
                    $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
                    [ordered]@{
                        local_address = $_.LocalAddress
                        local_port = $_.LocalPort
                        owning_pid = $_.OwningProcess
                        process_name = $(if ($proc) { $proc.ProcessName } else { $null })
                        process_path = $(if ($proc) {
                            try { $proc.Path } catch { $null }
                        } else { $null })
                    }
                }
        )
    }
    catch {
        $listener = @()
    }
}

$directUrlFiles = @()
if (Test-Path -LiteralPath $ProgramRoot) {
    $directUrlFiles = @(
        Get-ChildItem -LiteralPath $ProgramRoot -Filter "direct_url.json" -File -Recurse -ErrorAction SilentlyContinue |
            ForEach-Object {
                [ordered]@{
                    path = $_.FullName
                    content = (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8)
                    sha256 = Get-SafeHash $_.FullName
                }
            }
    )
}

$metadataFiles = @()
if (Test-Path -LiteralPath $ProgramRoot) {
    $metadataFiles = @(
        Get-ChildItem -LiteralPath $ProgramRoot -Filter "METADATA" -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.DirectoryName -match 'veraport_agent_reference-.*\.dist-info' } |
            ForEach-Object {
                $name = $null
                $version = $null
                foreach ($line in (Get-Content -LiteralPath $_.FullName -Encoding UTF8)) {
                    if ($line -like "Name:*") { $name = $line.Substring(5).Trim() }
                    if ($line -like "Version:*") { $version = $line.Substring(8).Trim() }
                }
                [ordered]@{
                    path = $_.FullName
                    name = $name
                    version = $version
                    sha256 = Get-SafeHash $_.FullName
                }
            }
    )
}

$implementationNames = @(
    "windows_service.py",
    "lappy_host.py",
    "service_config.py",
    "protocol.py",
    "executor.py",
    "controller_runtime.py",
    "hot_session.py",
    "tls_transport.py"
)
$implementation = @()
if (Test-Path -LiteralPath $ProgramRoot) {
    foreach ($name in $implementationNames) {
        $matches = @(
            Get-ChildItem -LiteralPath $ProgramRoot -Filter $name -File -Recurse -ErrorAction SilentlyContinue
        )
        foreach ($match in $matches) {
            $implementation += [ordered]@{
                name = $name
                path = $match.FullName
                length = $match.Length
                sha256 = Get-SafeHash $match.FullName
                last_write_time_utc = $match.LastWriteTimeUtc.ToString("o")
            }
        }
    }
}

$identityFiles = @()
$identityDir = Join-Path $ServiceRoot "identity"
if (Test-Path -LiteralPath $identityDir -PathType Container) {
    $identityFiles = @(
        Get-ChildItem -LiteralPath $identityDir -File -Force -ErrorAction SilentlyContinue |
            ForEach-Object {
                [ordered]@{
                    name = $_.Name
                    length = $_.Length
                    sha256 = Get-SafeHash $_.FullName
                    last_write_time_utc = $_.LastWriteTimeUtc.ToString("o")
                }
            }
    )
}

$controllerFiles = @()
$controllerDir = Join-Path $ServiceRoot "controller"
if (Test-Path -LiteralPath $controllerDir -PathType Container) {
    $controllerFiles = @(
        Get-ChildItem -LiteralPath $controllerDir -File -Force -ErrorAction SilentlyContinue |
            ForEach-Object {
                [ordered]@{
                    name = $_.Name
                    length = $_.Length
                    sha256 = Get-SafeHash $_.FullName
                    last_write_time_utc = $_.LastWriteTimeUtc.ToString("o")
                }
            }
    )
}

$configSummary = $null
if ($config) {
    $configSummary = [ordered]@{
        path = $configPath
        bind_host = $config.bind_host
        bind_port = $config.bind_port
        allowed_roots = @($config.allowed_roots)
        state_db = $config.state_db
        tls_cert = $config.tls_cert
        tls_key = $config.tls_key
        workstation_key = $(if ($config.workstation_key) { $config.workstation_key } else { $config.workstation_identity })
        controller_trust = $(if ($config.controller_trust) { $config.controller_trust } else { $config.controllers })
        allow_process_exec = $config.allow_process_exec
        allow_non_loopback_listener = $config.allow_non_loopback_listener
        max_lanes = $config.max_lanes
        max_inflight = $config.max_inflight
        max_read_bytes = $config.max_read_bytes
    }
}

$result = [ordered]@{
    schema = "VERAPORT_EXISTING_WINDOWS_INSTALL_INSPECTION_V1"
    read_only = $true
    observed_at_utc = [DateTime]::UtcNow.ToString("o")
    host = [ordered]@{
        computer_name = $env:COMPUTERNAME
        os = (Get-CimInstance Win32_OperatingSystem).Caption
        os_version = (Get-CimInstance Win32_OperatingSystem).Version
    }
    service = $(if ($service) {
        [ordered]@{
            present = $true
            name = $service.Name
            display_name = $service.DisplayName
            state = $service.State
            start_mode = $service.StartMode
            start_name = $service.StartName
            path_name = $service.PathName
            process_id = $service.ProcessId
            python_class = $pythonClass
        }
    } else {
        [ordered]@{ present = $false }
    })
    config = $configSummary
    listener = $listener
    state = $(if ($statePath) {
        $stateItem = Get-Item -LiteralPath $statePath
        [ordered]@{
            path = $stateItem.FullName
            exists = $true
            length = $stateItem.Length
            last_write_time_utc = $stateItem.LastWriteTimeUtc.ToString("o")
            sha256 = Get-SafeHash $stateItem.FullName
            hash_may_be_null_when_live_locked = $true
        }
    } else {
        [ordered]@{ exists = $false }
    })
    identity_files = $identityFiles
    controller_files = $controllerFiles
    package_provenance = [ordered]@{
        program_root = $ProgramRoot
        direct_url = $directUrlFiles
        metadata = $metadataFiles
        implementation = $implementation
    }
    acls = @(
        Get-AclObservation $ServiceRoot
        Get-AclObservation $identityDir
        Get-AclObservation $controllerDir
    ) | Where-Object { $_ -ne $null }
    effects = [ordered]@{
        service_stopped = $false
        service_started = $false
        files_written = $false
        firewall_changed = $false
        state_database_opened = $false
    }
}

$result | ConvertTo-Json -Depth 16
