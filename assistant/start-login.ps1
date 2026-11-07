$ErrorActionPreference = 'Stop'
# An explicit pause persists across Windows logins.
if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'runtime/STOP')) { exit 0 }
& (Join-Path $PSScriptRoot 'start-bot.ps1')
