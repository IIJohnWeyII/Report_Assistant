$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Create .venv and install requirements.txt first; see README.md.' }
if (-not (Test-Path -LiteralPath '.env')) { throw 'Missing .env. See README.md.' }
Write-Host 'Report Assistant: http://127.0.0.1:8000 (Ctrl+C to stop web server)'
& $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000
