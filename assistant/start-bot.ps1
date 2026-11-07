$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-backend.ps1')
$stopFile = Join-Path $PSScriptRoot 'runtime/STOP'
if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
$python = Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
$script = Join-Path $PSScriptRoot 'supervise.py'
$existing = Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'pythonw.exe' -and $_.CommandLine -like "*$script*" }
if (-not $existing) { Start-Process -FilePath $python -ArgumentList ('"' + $script + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden }
