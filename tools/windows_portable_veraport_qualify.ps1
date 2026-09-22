$ErrorActionPreference = "Stop"

$SourceSha = "13a16019f3ec45acd576eb22f75138e6820d6262"
$PythonVersion = "3.11.9"
$Root = Join-Path $env:TEMP "VeraMesh-Portable-Qualification"
$PythonDir = Join-Path $Root "python"
$SourceDir = Join-Path $Root "source"
$Archive = Join-Path $Root "vera-mesh.zip"
$GetPip = Join-Path $Root "get-pip.py"

Write-Host "VeraMesh portable qualification"
Write-Host "Source: $SourceSha"
Write-Host "No Git install. No system Python install. No service install."

Remove-Item $Root -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $Root | Out-Null
New-Item -ItemType Directory -Path $PythonDir | Out-Null
New-Item -ItemType Directory -Path $SourceDir | Out-Null

$PythonZip = Join-Path $Root "python-embed.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$SourceUrl = "https://github.com/thebrazenbeard/vera-mesh/archive/$SourceSha.zip"
$GetPipUrl = "https://bootstrap.pypa.io/get-pip.py"

Write-Host "Downloading portable Python $PythonVersion..."
Invoke-WebRequest -UseBasicParsing -Uri $PythonUrl -OutFile $PythonZip

Write-Host "Downloading VeraMesh exact source..."
Invoke-WebRequest -UseBasicParsing -Uri $SourceUrl -OutFile $Archive

Write-Host "Downloading pip bootstrap..."
Invoke-WebRequest -UseBasicParsing -Uri $GetPipUrl -OutFile $GetPip

Expand-Archive -Path $PythonZip -DestinationPath $PythonDir -Force
Expand-Archive -Path $Archive -DestinationPath $SourceDir -Force

$Pth = Get-ChildItem $PythonDir -Filter "python311._pth" | Select-Object -First 1
if (-not $Pth) {
    throw "Portable Python _pth file not found."
}
$PthText = Get-Content $Pth.FullName
$PthText = $PthText -replace '^#import site$', 'import site'
Set-Content -Path $Pth.FullName -Value $PthText -Encoding ASCII

$Python = Join-Path $PythonDir "python.exe"
if (-not (Test-Path $Python)) {
    throw "Portable Python executable missing."
}

Write-Host "Bootstrapping pip into portable Python..."
& $Python $GetPip --disable-pip-version-check
if ($LASTEXITCODE -ne 0) {
    throw "get-pip failed with exit code $LASTEXITCODE"
}

$Repo = Get-ChildItem $SourceDir -Directory | Select-Object -First 1
if (-not $Repo) {
    throw "Extracted VeraMesh source directory not found."
}
$Package = Join-Path $Repo.FullName "reference\veraport_agent"
if (-not (Test-Path $Package)) {
    throw "VeraPort package path missing: $Package"
}

Write-Host "Installing VeraPort only into the temporary portable Python..."
& $Python -m pip install --disable-pip-version-check $Package
if ($LASTEXITCODE -ne 0) {
    throw "VeraPort package install failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Running real VeraPort loopback TLS/application-auth qualification..."
& $Python -m veraport_agent.qualify_local --source-sha $SourceSha
if ($LASTEXITCODE -ne 0) {
    throw "VeraPort qualification failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Qualification command completed."
Write-Host "Temporary bootstrap files are under: $Root"
