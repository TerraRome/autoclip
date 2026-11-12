# Jalankan AutoClip (fork Terra) di Windows tanpa build installer.
# Backend berjalan dalam mode desktop (tanpa Redis/Celery), frontend lewat Vite.
#
# Sekali saja:  powershell -ExecutionPolicy Bypass -File .\jalankan_windows.ps1 -Setup
# Selanjutnya:  powershell -ExecutionPolicy Bypass -File .\jalankan_windows.ps1
param([switch]$Setup)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if ($Setup -or -not (Test-Path ".venv")) {
  Write-Host "== Membuat virtualenv Python dan memasang dependensi ==" -ForegroundColor Cyan
  if (Get-Command py -ErrorAction SilentlyContinue) { py -3.11 -m venv .venv } else { python -m venv .venv }
  .\.venv\Scripts\python -m pip install --upgrade pip
  .\.venv\Scripts\python -m pip install -r requirements.txt
  Push-Location frontend; npm ci; Pop-Location
}

if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue) -and -not $env:AUTOCLIP_FFPROBE_PATH) {
  Write-Host "ffmpeg/ffprobe belum ada di PATH. Pasang dulu:  winget install Gyan.FFmpeg  lalu buka terminal baru." -ForegroundColor Yellow
  exit 1
}

$env:AUTOCLIP_DESKTOP_MODE = "true"
$env:PYTHONUTF8 = "1"

Write-Host "== Backend: http://127.0.0.1:8000 ==" -ForegroundColor Cyan
$backend = Start-Process -PassThru -NoNewWindow ".\.venv\Scripts\python" `
  -ArgumentList "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"

try {
  Write-Host "== Frontend: http://localhost:3000 (buka di browser) ==" -ForegroundColor Cyan
  Start-Process "http://localhost:3000"
  Push-Location frontend
  npm run dev -- --port 3000
} finally {
  Pop-Location
  if ($backend -and -not $backend.HasExited) { Stop-Process -Id $backend.Id -Force }
}
