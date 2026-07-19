$ErrorActionPreference = "Stop"

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
& $python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
& $python -m PyInstaller --noconfirm --clean "NeonDriveDownloader.spec"
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

Write-Host "Built: dist\NeonDriveDownloader.exe"
