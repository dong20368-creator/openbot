$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'start-backend.ps1')
$stopFile = Join-Path $PSScriptRoot 'runtime/STOP'
if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
$python = Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
$script = Join-Path $PSScriptRoot 'supervise.py'
# The supervisor lock handles duplicate launches and an old supervisor exiting.
Start-Process -FilePath $python -ArgumentList ('"' + $script + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
