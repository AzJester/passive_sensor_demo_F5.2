# Creates a virtual environment, installs dependencies, and runs the tests.
# Native programs report progress on the error stream. That must not stop the script,
# so failures are detected from exit codes.
$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot
if ($PSScriptRoot.Length -gt 120) {
    Write-Warning "This folder path is long ($($PSScriptRoot.Length) characters). Python on Windows fails on paths over 260 characters. If the install or the tests fail, move the folder to a short path such as C:\PAS."
}
$launcher = $null
foreach ($candidate in @('py -3', 'python', 'python3')) {
    $parts = $candidate.Split(' ')
    $rest = @()
    if ($parts.Length -gt 1) { $rest = $parts[1..($parts.Length - 1)] }
    if (-not (Get-Command $parts[0] -ErrorAction SilentlyContinue)) { continue }
    $version = & $parts[0] @rest -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $version -and ([version]"$version" -ge [version]'3.11')) { $launcher = $parts; break }
}
if (-not $launcher) {
    Write-Host 'Python 3.11 or later was not found. Install 64-bit Python from https://www.python.org/downloads/windows/ and include the py launcher.' -ForegroundColor Red
    exit 1
}
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    $rest = @()
    if ($launcher.Length -gt 1) { $rest = $launcher[1..($launcher.Length - 1)] }
    & $launcher[0] @rest -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host 'Could not create the virtual environment.' -ForegroundColor Red; exit 1 }
}
& '.\.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Dependency installation failed. Check the network connection, or install from requirements-lock.txt.' -ForegroundColor Red
    exit 1
}
Write-Host ''
Write-Host 'Running the software tests. This takes a few minutes.'
& '.\.venv\Scripts\python.exe' -m unittest discover -s tests -t . -b
if ($LASTEXITCODE -ne 0) {
    Write-Host 'The software tests failed on this computer. Do not use the sensor until they pass.' -ForegroundColor Red
    exit 1
}
Write-Host ''
Write-Host 'Ready. Double-click Start-Simulation.cmd to try it without hardware.'
Write-Host 'For the microphone array, follow README.md, section "Run with the array".'
exit 0
