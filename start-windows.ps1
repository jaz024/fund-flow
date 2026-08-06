[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

$projectRoot = $PSScriptRoot
$webAddress = "127.0.0.1"
$webPort = 3000
$apiPort = 8765
$webUrl = "http://${webAddress}:${webPort}"
$apiHealthUrl = "http://127.0.0.1:${apiPort}/api/health"
$apiProcess = $null
$webProcess = $null

Set-Location -LiteralPath $projectRoot

function Test-LocalUrl {
    param([Parameter(Mandatory = $true)][string]$Url)

    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Wait-ForLocalUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [Parameter(Mandatory = $true)][int]$TimeoutSeconds,
        $StartedProcess
    )

    for ($second = 0; $second -lt $TimeoutSeconds; $second++) {
        if (Test-LocalUrl -Url $Url) {
            return $true
        }
        if ($null -ne $StartedProcess -and $StartedProcess.HasExited) {
            return $false
        }
        Start-Sleep -Seconds 1
    }

    return $false
}

function Stop-StartedProcessTree {
    param($StartedProcess)

    if ($null -eq $StartedProcess) {
        return
    }

    try {
        if (-not $StartedProcess.HasExited) {
            $taskkill = Join-Path $env:SystemRoot "System32\taskkill.exe"
            & $taskkill /PID $StartedProcess.Id /T /F 2>$null | Out-Null
        }
    }
    catch {
        # The process may already have closed while Windows was cleaning it up.
    }
}

try {
    Write-Host "Starting Fund Flow..." -ForegroundColor Cyan

    $pythonCommand = Get-Command "py.exe" -ErrorAction SilentlyContinue
    $pythonArguments = @("-3")
    if ($null -eq $pythonCommand) {
        $pythonCommand = Get-Command "python.exe" -ErrorAction SilentlyContinue
        $pythonArguments = @()
    }
    if ($null -eq $pythonCommand) {
        throw "Python 3 was not found. Install Python 3.10 or newer from https://www.python.org/downloads/windows/"
    }

    $pythonExecutable = $pythonCommand.Source
    $pythonVersionArguments = @($pythonArguments) + @(
        "-c",
        "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
    )
    & $pythonExecutable @pythonVersionArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python is too old. Install Python 3.10 or newer."
    }

    $nodeCommand = Get-Command "node.exe" -ErrorAction SilentlyContinue
    if ($null -eq $nodeCommand) {
        throw "Node.js was not found. Install Node.js 22.13 or newer from https://nodejs.org/"
    }
    $nodeExecutable = $nodeCommand.Source
    & $nodeExecutable -e "const [major, minor] = process.versions.node.split('.').map(Number); process.exit(major > 22 || (major === 22 && minor >= 13) ? 0 : 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "Node.js is too old. Install Node.js 22.13 or newer."
    }

    $vinextScript = Join-Path $projectRoot "node_modules\vinext\dist\cli.js"
    if (-not (Test-Path -LiteralPath $vinextScript)) {
        $npxCommand = Get-Command "npx.cmd" -ErrorAction SilentlyContinue
        if ($null -eq $npxCommand) {
            $npxCommand = Get-Command "npx.exe" -ErrorAction SilentlyContinue
        }
        if ($null -eq $npxCommand) {
            throw "npx was not found. Reinstall Node.js and try again."
        }

        Write-Host "First run: installing the web app packages. This may take a few minutes..." -ForegroundColor Yellow
        $npxExecutable = $npxCommand.Source
        & $npxExecutable --yes "pnpm@11.9.0" install --frozen-lockfile
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $vinextScript)) {
            throw "Package installation failed. Check the internet connection and try again."
        }
    }

    if ($null -eq (Get-Command "ffmpeg.exe" -ErrorAction SilentlyContinue)) {
        Write-Host "Note: FFmpeg was not found. Market data will work, but MP4 generation requires FFmpeg." -ForegroundColor DarkYellow
    }

    if (Test-LocalUrl -Url $apiHealthUrl) {
        Write-Host "The data service is already running and will be reused."
    }
    else {
        $env:FUND_FLOW_API_PORT = [string]$apiPort
        $apiStartArguments = @($pythonArguments) + @("local_server.py")
        $apiProcess = Start-Process `
            -FilePath $pythonExecutable `
            -ArgumentList $apiStartArguments `
            -WorkingDirectory $projectRoot `
            -NoNewWindow `
            -PassThru

        if (-not (Wait-ForLocalUrl -Url $apiHealthUrl -TimeoutSeconds 25 -StartedProcess $apiProcess)) {
            throw "The data service could not start. Close other Fund Flow windows and try again."
        }
    }

    if (Test-LocalUrl -Url $webUrl) {
        Write-Host "The website is already running. Opening ${webUrl}" -ForegroundColor Green
        Start-Process $webUrl

        if ($null -ne $apiProcess) {
            Write-Host "Keep this window open. Closing it will stop the data service started here."
            while (-not $apiProcess.HasExited) {
                Start-Sleep -Seconds 1
            }
        }
        exit 0
    }

    $webStartArguments = "`"${vinextScript}`" dev --hostname ${webAddress} --port ${webPort}"
    $webProcess = Start-Process `
        -FilePath $nodeExecutable `
        -ArgumentList $webStartArguments `
        -WorkingDirectory $projectRoot `
        -NoNewWindow `
        -PassThru

    if (-not (Wait-ForLocalUrl -Url $webUrl -TimeoutSeconds 90 -StartedProcess $webProcess)) {
        throw "The website did not start within 90 seconds. Read the message above and try again."
    }

    Write-Host "The website is ready. Opening ${webUrl}" -ForegroundColor Green
    Write-Host "Keep this window open. Closing it will stop the app."
    Start-Process $webUrl

    while (-not $webProcess.HasExited) {
        Start-Sleep -Seconds 1
    }

    if ($webProcess.ExitCode -ne 0) {
        throw "The website stopped unexpectedly."
    }
}
catch {
    Write-Host ""
    Write-Host "Startup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Stop-StartedProcessTree -StartedProcess $webProcess
    Stop-StartedProcessTree -StartedProcess $apiProcess
}
