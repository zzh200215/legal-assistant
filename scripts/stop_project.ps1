param(
    [int[]]$Ports = @(8001, 5176)
)

$ErrorActionPreference = 'SilentlyContinue'
$connections = Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in $Ports }
$processIds = $connections | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($processId in $processIds) {
    Stop-Process -Id $processId -Force
}
Write-Output "Stopped project services on ports: $($Ports -join ', ')."
