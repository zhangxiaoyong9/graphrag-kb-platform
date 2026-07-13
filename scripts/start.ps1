[CmdletBinding()]
param(
    [string]$DbPath = $(if ($env:KB_DB_PATH) { $env:KB_DB_PATH } else { "kb.db" }),
    [string]$DataRoot = $(if ($env:KB_DATA_ROOT) { $env:KB_DATA_ROOT } else { "." }),
    [string]$HostAddress = $(if ($env:KB_HOST) { $env:KB_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:KB_PORT) { [int]$env:KB_PORT } else { 8000 }),
    [switch]$SkipInstall,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "uv is not installed or is not available on PATH."
}

if (-not $SkipInstall -and -not (Test-Path ".venv")) {
    Write-Host "[setup] Installing Python dependencies..."
    & uv sync
    if ($LASTEXITCODE -ne 0) { throw "uv sync failed." }
}

if (-not $SkipBuild -and -not (Test-Path "web/dist")) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        throw "npm is required to build web/dist."
    }
    Write-Host "[setup] Building the dashboard..."
    Push-Location web
    try {
        if (-not (Test-Path "node_modules")) {
            if (Test-Path "package-lock.json") { & npm ci } else { & npm install }
            if ($LASTEXITCODE -ne 0) { throw "npm dependency installation failed." }
        }
        & npm run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    }
    finally { Pop-Location }
}

Write-Host "[setup] Applying database migrations..."
& uv run alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Database migration failed." }

$server = $null
$worker = $null
try {
    Write-Host "[start] API: http://${HostAddress}:${Port}"
    $server = Start-Process uv -ArgumentList @(
        "run", "python", "-m", "kb_platform.server",
        "`"$DbPath`"", "`"$DataRoot`"", $HostAddress, "$Port"
    ) -NoNewWindow -PassThru

    Write-Host "[start] Worker"
    $worker = Start-Process uv -ArgumentList @(
        "run", "python", "-m", "kb_platform.worker", "`"$DbPath`""
    ) -NoNewWindow -PassThru

    while (-not $server.HasExited -and -not $worker.HasExited) {
        Start-Sleep -Milliseconds 500
        $server.Refresh()
        $worker.Refresh()
    }

    if ($server.HasExited) { throw "API server exited with code $($server.ExitCode)." }
    if ($worker.HasExited) { throw "Worker exited with code $($worker.ExitCode)." }
}
finally {
    Write-Host "`n[stop] Stopping KB Platform..."
    foreach ($process in @($server, $worker)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
            $process.WaitForExit(5000) | Out-Null
        }
    }
}
