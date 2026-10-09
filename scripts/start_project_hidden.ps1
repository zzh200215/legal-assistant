param(
    [int]$ApiPort = 8001,
    [int]$WebPort = 5176
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$frontend = Join-Path $root 'frontend'
$logDir = Join-Path $root 'logs'
$frontendTmp = Join-Path $frontend 'tmp'
New-Item -ItemType Directory -Force -Path $logDir, $frontendTmp | Out-Null

$python = if (Test-Path 'D:\AI\ACD\envs\llmXM\python.exe') {
    'D:\AI\ACD\envs\llmXM\python.exe'
} else {
    (Get-Command python).Source
}
$node = if (Test-Path 'D:\AI\nodejs\node.exe') {
    'D:\AI\nodejs\node.exe'
} else {
    (Get-Command node).Source
}
$viteEntry = Join-Path $frontend 'node_modules\vite\bin\vite.js'
if (-not (Test-Path $viteEntry)) { throw "Vite entry not found: $viteEntry" }

function Start-HiddenProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [string]$StandardOutput,
        [string]$StandardError
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $FilePath
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.Arguments = ($Arguments | ForEach-Object {
        '"' + $_.Replace('"', '\"') + '"'
    }) -join ' '

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -Action {
        if ($EventArgs.Data) { Add-Content -LiteralPath $using:StandardOutput -Value $EventArgs.Data }
    } | Out-Null
    Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -Action {
        if ($EventArgs.Data) { Add-Content -LiteralPath $using:StandardError -Value $EventArgs.Data }
    } | Out-Null
}

Start-HiddenProcess -FilePath $python `
    -Arguments @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$ApiPort") `
    -WorkingDirectory $root `
    -StandardOutput (Join-Path $logDir 'backend-hidden.out.log') `
    -StandardError (Join-Path $logDir 'backend-hidden.err.log')

Start-HiddenProcess -FilePath $node `
    -Arguments @($viteEntry, '--host', '127.0.0.1', '--port', "$WebPort") `
    -WorkingDirectory $frontend `
    -StandardOutput (Join-Path $frontendTmp 'vite-hidden.out.log') `
    -StandardError (Join-Path $frontendTmp 'vite-hidden.err.log')

Write-Output "Started hidden API on $ApiPort and Vite on $WebPort."
