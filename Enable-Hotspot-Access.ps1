$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host 'Windows administrator permission is needed to allow this local connection through the firewall.'
    Write-Host 'This allows the page over TCP and the Python video stream over UDP, only on your hotspot subnet.'
    $taskArgs = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    $taskProcess = Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList $taskArgs -Wait -PassThru
    if ($taskProcess.ExitCode -ne 0) { throw 'Firewall setup failed or administrator permission was declined.' }
    Write-Host 'Hotspot access is enabled. Open the phone link again.'
    exit
}
$networkFile = Join-Path $PSScriptRoot '.runtime\network.json'
if (-not (Test-Path -LiteralPath $networkFile)) { throw 'Run Start-Mirror.cmd first, then run this shortcut again.' }
$network = Get-Content -LiteralPath $networkFile -Raw | ConvertFrom-Json
$address = [System.Net.IPAddress]::Parse($network.ip)
if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) { throw 'An IPv4 address is required.' }
if (-not (Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -eq $network.ip)) { throw 'Your hotspot address changed. Restart the mirror, then retry.' }
$port = [int]$network.port
if ($port -lt 1024 -or $port -gt 65535) { throw 'Invalid mirror port.' }
$subnet = [string]$network.subnet
if ($subnet -notmatch '^\d{1,3}(\.\d{1,3}){3}/\d{1,2}$') { throw 'Invalid hotspot subnet.' }
# Only replace the dedicated rule created by this app.
Get-NetFirewallRule -Name 'LocalScreenMirror-Hotspot' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'LocalScreenMirror-Hotspot' -DisplayName 'Local Screen Mirror - hotspot only' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -LocalAddress $network.ip -RemoteAddress $subnet -Profile Any | Out-Null
$pythonProgram = [string]$network.python
if (-not $pythonProgram -or -not (Test-Path -LiteralPath $pythonProgram)) { throw 'Restart the updated mirror before enabling video access.' }
Get-NetFirewallRule -Name 'LocalScreenMirror-Video' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'LocalScreenMirror-Video' -DisplayName 'Local Screen Mirror - local video' -Direction Inbound -Action Allow -Protocol UDP -Program $pythonProgram -LocalAddress $network.ip -RemoteAddress $subnet -Profile Any | Out-Null
Write-Host 'The scoped hotspot firewall rule is ready.'
