$ErrorActionPreference = "Stop"

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
& $python -m PyInstaller --noconfirm --clean "NeonDriveDownloader.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

& $python -m PyInstaller --noconfirm --clean "NeonDriveDownloader-OneFile.spec"
if ($LASTEXITCODE -ne 0) { throw "Legacy onefile build failed." }
Copy-Item -LiteralPath "dist\NeonDriveDownloader-Legacy.exe" `
    -Destination "dist\NeonDriveDownloader.exe" -Force

$portable = "dist\NeonDriveDownloader-Portable.zip"
if (Test-Path -LiteralPath $portable) { Remove-Item -LiteralPath $portable -Force }
Compress-Archive -Path "dist\NeonDriveDownloader\*" -DestinationPath $portable -CompressionLevel Optimal

$version = & $python -c "from neon_drive import __version__; print(__version__)"
$versionParts = @(
    [regex]::Matches($version, "\d+") |
        Select-Object -First 4 |
        ForEach-Object { $_.Value }
)
while ($versionParts.Count -lt 4) { $versionParts += "0" }
$fileVersion = $versionParts -join "."
$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path ${env:ProgramFiles(x86)} "Inno Setup 6\ISCC.exe"),
    (Join-Path $env:ProgramFiles "Inno Setup 6\ISCC.exe")
)
$iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if ($iscc) {
    & $iscc "/DMyAppVersion=$version" "/DMyAppFileVersion=$fileVersion" "installer.iss"
    if ($LASTEXITCODE -ne 0) { throw "Installer build failed." }
} else {
    Write-Warning "Inno Setup 6 is not installed; the Setup EXE was skipped locally."
}

Write-Host "Built app: dist\NeonDriveDownloader\NeonDriveDownloader.exe"
Write-Host "Built portable archive: $portable"
Write-Host "Built compatibility EXE: dist\NeonDriveDownloader.exe"
if ($iscc) { Write-Host "Built installer: dist\NeonDriveDownloader-Setup.exe" }
