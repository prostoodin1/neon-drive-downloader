$ErrorActionPreference = "Stop"

$python = if (Test-Path ".venv\Scripts\python.exe") { ".venv\Scripts\python.exe" } else { "python" }

& $python -m pip install -r requirements.txt
& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "NeonDriveDownloader" `
    main.py

Write-Host "Готово: dist\NeonDriveDownloader.exe"
