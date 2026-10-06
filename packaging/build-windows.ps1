# Builds the Recall Windows app into dist\Recall\Recall.exe (plus dist\Recall-windows.zip).
# Run from the project folder in PowerShell:  powershell -ExecutionPolicy Bypass -File packaging\build-windows.ps1
# Needs Python 3.11+ for Windows (python.org) on PATH. Also used by .github/workflows/build.yml.
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

# Native commands don't throw on failure in Windows PowerShell, so check each exit code.
function Run { & $args[0] $args[1..($args.Count - 1)]; if ($LASTEXITCODE -ne 0) { throw "failed ($LASTEXITCODE): $args" } }

if (-not (Test-Path ".venv-win")) { Run python -m venv .venv-win }
$py = ".venv-win\Scripts\python.exe"
Run $py -m pip install --upgrade pip
Run $py -m pip install -r requirements.txt -r packaging\requirements-desktop.txt
# rapidocr also pulls in opencv-python; both install into cv2\, so keep just one copy
Run $py -m pip uninstall -y opencv-python
Run $py -m pip install --force-reinstall --no-deps opencv-python-headless
Run $py -m PyInstaller --noconfirm --clean packaging\recall.spec

Compress-Archive -Path dist\Recall -DestinationPath dist\Recall-windows.zip -Force
Write-Host "Built dist\Recall\Recall.exe and dist\Recall-windows.zip"
