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
$transcriptStarted = $false
$startupLog = Join-Path $projectRoot "startup-windows.log"
$chinaRegistry = "https://registry.npmmirror.com"
$officialRegistry = "https://registry.npmjs.org"

Set-Location -LiteralPath $projectRoot

try {
    Start-Transcript -LiteralPath $startupLog -Append | Out-Null
    $transcriptStarted = $true
}
catch {
    # Logging is helpful for support, but a locked log file must not block startup.
}

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

function Test-WindowsWebPackages {
    param(
        [Parameter(Mandatory = $true)][string]$VinextScript,
        [Parameter(Mandatory = $true)][string]$NodeArchitecture
    )

    if (-not (Test-Path -LiteralPath $VinextScript -PathType Leaf)) {
        return $false
    }

    $pnpmModules = Join-Path $projectRoot "node_modules\.pnpm"
    if (-not (Test-Path -LiteralPath $pnpmModules -PathType Container)) {
        return $false
    }

    $nativePackagePattern = "@next+swc-win32-${NodeArchitecture}-msvc@*"
    $nativePackages = @(
        Get-ChildItem `
            -LiteralPath $pnpmModules `
            -Directory `
            -ErrorAction SilentlyContinue | Where-Object { $_.Name -like $nativePackagePattern }
    )

    return $nativePackages.Count -gt 0
}

function Install-WindowsWebPackages {
    param(
        [Parameter(Mandatory = $true)][string]$NpxExecutable,
        [Parameter(Mandatory = $true)][string]$VinextScript,
        [Parameter(Mandatory = $true)][string]$NodeArchitecture
    )

    $registries = @($chinaRegistry, $officialRegistry)
    $previousRegistry = $env:npm_config_registry

    try {
        foreach ($registry in $registries) {
            Write-Host "Installing from ${registry} ..." -ForegroundColor Yellow
            $env:npm_config_registry = $registry

            & $NpxExecutable `
                --yes `
                "--registry=${registry}" `
                "pnpm@11.9.0" `
                install `
                --frozen-lockfile `
                "--registry=${registry}"

            if (
                $LASTEXITCODE -eq 0 -and
                (Test-WindowsWebPackages `
                    -VinextScript $VinextScript `
                    -NodeArchitecture $NodeArchitecture)
            ) {
                return
            }

            Write-Host "That download source did not complete. Trying the next source..." -ForegroundColor DarkYellow
        }
    }
    finally {
        $env:npm_config_registry = $previousRegistry
    }

    throw "Package installation failed. Check the internet connection, then try again. Details were saved in startup-windows.log."
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

    $nodeArchitecture = (& $nodeExecutable -p "process.arch").Trim()
    if ($nodeArchitecture -notin @("x64", "arm64")) {
        throw "This Windows processor type (${nodeArchitecture}) is not supported by the included web packages."
    }

    $vinextScript = Join-Path $projectRoot "node_modules\vinext\dist\cli.js"
    if (-not (Test-WindowsWebPackages -VinextScript $vinextScript -NodeArchitecture $nodeArchitecture)) {
        $npxCommand = Get-Command "npx.cmd" -ErrorAction SilentlyContinue
        if ($null -eq $npxCommand) {
            $npxCommand = Get-Command "npx.exe" -ErrorAction SilentlyContinue
        }
        if ($null -eq $npxCommand) {
            throw "npx was not found. Reinstall Node.js and try again."
        }

        $nodeModules = Join-Path $projectRoot "node_modules"
        if (Test-Path -LiteralPath $nodeModules) {
            Write-Host "The copied packages are incomplete or belong to another operating system." -ForegroundColor Yellow
            Write-Host "Rebuilding them safely for this Windows computer..." -ForegroundColor Yellow
            Remove-Item -LiteralPath $nodeModules -Recurse -Force
        }
        else {
            Write-Host "First run: installing the web app packages. This may take a few minutes..." -ForegroundColor Yellow
        }

        $npxExecutable = $npxCommand.Source
        Install-WindowsWebPackages `
            -NpxExecutable $npxExecutable `
            -VinextScript $vinextScript `
            -NodeArchitecture $nodeArchitecture
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
    if ($transcriptStarted) {
        try {
            Stop-Transcript | Out-Null
        }
        catch {
            # Windows may already have closed the transcript during shutdown.
        }
    }
}
