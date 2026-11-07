$ErrorActionPreference='Stop'
$existing=Get-CimInstance Win32_Process -Filter "Name='wsl.exe'" | Where-Object { $_.CommandLine -match '-d WeChatAgent -u root --exec /bin/sleep infinity' }
if (-not $existing) {
    $p=Start-Process wsl.exe -ArgumentList @('-d','WeChatAgent','-u','root','--exec','/bin/sleep','infinity') -WindowStyle Hidden -PassThru
    $p.Id | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime/backend-keeper.pid') -Encoding ascii
}
# Native hub must be online before the worker starts. No desktop fallback.
wsl -d WeChatAgent -u root --exec docker start wechat-agent-v2-protocol | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Native WeChat hub could not start' }

