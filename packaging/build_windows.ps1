[CmdletBinding()]
param(
    [string]$PipIndexUrl
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
Set-Location -LiteralPath $ProjectRoot

$PipInstallArgs = @("--retries", "5", "--timeout", "120")
if (-not [string]::IsNullOrWhiteSpace($PipIndexUrl)) {
    if (-not [System.Uri]::IsWellFormedUriString($PipIndexUrl, [System.UriKind]::Absolute)) {
        throw "PipIndexUrl must be an absolute URL."
    }
    $PipInstallArgs = @("--index-url", $PipIndexUrl) + $PipInstallArgs
}

function Get-SafeWorkspacePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    $separator = [System.IO.Path]::DirectorySeparatorChar
    $rootPrefix = $ProjectRoot.TrimEnd($separator, [System.IO.Path]::AltDirectorySeparatorChar) + $separator
    if (-not $fullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to access a path outside the workspace: $fullPath"
    }
    return $fullPath
}

function Remove-SafeWorkspaceTree {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = Get-SafeWorkspacePath $Path
    if (-not (Test-Path -LiteralPath $fullPath)) {
        return
    }
    $item = Get-Item -LiteralPath $fullPath -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to recursively remove a reparse point: $($item.FullName)"
    }
    Remove-Item -LiteralPath $item.FullName -Recurse -Force
}

function Remove-SafeWorkspaceFile {
    param([Parameter(Mandatory = $true)][string]$Path)

    $fullPath = Get-SafeWorkspacePath $Path
    if (Test-Path -LiteralPath $fullPath) {
        $item = Get-Item -LiteralPath $fullPath -Force
        if ($item.PSIsContainer) {
            throw "Expected a file but found a directory: $fullPath"
        }
        Remove-Item -LiteralPath $item.FullName -Force
    }
}

$VenvPath = Get-SafeWorkspacePath (Join-Path $ProjectRoot ".packaging-venv")
$BuildRoot = Get-SafeWorkspacePath (Join-Path $ProjectRoot "build")
$ReleaseRoot = Get-SafeWorkspacePath (Join-Path $ProjectRoot "release")
$PythonDist = Get-SafeWorkspacePath (Join-Path $BuildRoot "python")
$EggInfo = Get-SafeWorkspacePath (Join-Path $ProjectRoot "chat_online_lan.egg-info")

$Version = (& py -c "from chat_online.version import __version__; print(__version__)").Trim()
if ($LASTEXITCODE -ne 0 -or -not $Version) {
    throw "Could not determine the application version."
}
if ($Version -ne "7.0.0") {
    throw "Update packaging/version_info.txt before building version $Version."
}
$ReleaseName = "ChatOnline-$Version-windows-x64"
$ReleaseDir = Get-SafeWorkspacePath (Join-Path $ReleaseRoot $ReleaseName)
$ZipPath = Get-SafeWorkspacePath (Join-Path $ReleaseRoot "$ReleaseName.zip")
$ZipHashPath = Get-SafeWorkspacePath (Join-Path $ReleaseRoot "$ReleaseName.zip.sha256")

try {
    Remove-SafeWorkspaceTree $VenvPath
    Remove-SafeWorkspaceTree $BuildRoot
    Remove-SafeWorkspaceTree $ReleaseDir
    Remove-SafeWorkspaceTree $PythonDist
    Remove-SafeWorkspaceTree $EggInfo
    Remove-SafeWorkspaceFile $ZipPath
    Remove-SafeWorkspaceFile $ZipHashPath

    Write-Host "[1/9] Creating isolated build environment..."
    & py -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Could not create the packaging virtual environment." }
    $Python = Join-Path $VenvPath "Scripts\python.exe"

    Write-Host "[2/9] Installing runtime and build dependencies..."
    & $Python -m pip install --upgrade pip @PipInstallArgs
    if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
    & $Python -m pip install @PipInstallArgs -e ".[dev]" -r ".\packaging\requirements-build.txt"
    if ($LASTEXITCODE -ne 0) { throw "Could not install packaging dependencies." }

    Write-Host "[3/9] Running source checks..."
    $PreviousQtPlatform = $env:QT_QPA_PLATFORM
    $env:QT_QPA_PLATFORM = "offscreen"
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
    & $Python -m ruff check chat_online tests tools "聊天.py"
    if ($LASTEXITCODE -ne 0) { throw "Ruff failed." }
    & $Python -m compileall -q chat_online tests tools "聊天.py"
    if ($LASTEXITCODE -ne 0) { throw "Compilation check failed." }
    $env:QT_QPA_PLATFORM = $PreviousQtPlatform

    Write-Host "[4/9] Generating Windows resources..."
    & $Python ".\tools\generate_windows_icon.py" ".\packaging\chat-online.ico"
    if ($LASTEXITCODE -ne 0) { throw "Could not generate the Windows icon." }

    Write-Host "[5/9] Building shared GUI and CLI executable bundle..."
    New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null
    & $Python -m PyInstaller `
        --noconfirm `
        --clean `
        --distpath $ReleaseRoot `
        --workpath (Join-Path $BuildRoot "pyinstaller") `
        ".\packaging\ChatOnline.spec"
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }
    if (-not (Test-Path -LiteralPath $ReleaseDir)) {
        throw "Expected release directory was not produced: $ReleaseDir"
    }

    Write-Host "[6/9] Building wheel and source archive..."
    New-Item -ItemType Directory -Path $PythonDist -Force | Out-Null
    & $Python -m build --no-isolation --outdir $PythonDist
    if ($LASTEXITCODE -ne 0) { throw "Python package build failed." }

    Write-Host "[7/9] Adding documentation, source archives, and license notices..."
    Copy-Item -LiteralPath ".\README.md", ".\CHANGELOG.md", ".\LICENCE", ".\packaging\THIRD_PARTY_NOTICES.md" -Destination $ReleaseDir
    $ReleaseDocs = Join-Path $ReleaseDir "docs\images"
    New-Item -ItemType Directory -Path $ReleaseDocs -Force | Out-Null
    Get-ChildItem -LiteralPath ".\docs\images" -File | Copy-Item -Destination $ReleaseDocs
    $ReleaseSource = Join-Path $ReleaseDir "source"
    New-Item -ItemType Directory -Path $ReleaseSource -Force | Out-Null
    Get-ChildItem -LiteralPath $PythonDist -File | Copy-Item -Destination $ReleaseSource
    & $Python ".\tools\collect_release_licenses.py" $ReleaseDir
    if ($LASTEXITCODE -ne 0) { throw "Could not collect third-party licenses." }

    Write-Host "[8/9] Smoke-testing packaged executables..."
    & $Python ".\tools\smoke_windows_release.py" $ReleaseDir
    if ($LASTEXITCODE -ne 0) { throw "Packaged release smoke tests failed." }

    Write-Host "[9/9] Writing checksums and ZIP archive..."
    $ManifestPath = Join-Path $ReleaseDir "SHA256SUMS.txt"
    $ManifestLines = Get-ChildItem -LiteralPath $ReleaseDir -Recurse -File |
        Where-Object { $_.FullName -ne $ManifestPath } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = [System.IO.Path]::GetRelativePath($ReleaseDir, $_.FullName).Replace("\", "/")
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            "$hash  $relative"
        }
    [System.IO.File]::WriteAllLines(
        $ManifestPath,
        [string[]]$ManifestLines,
        [System.Text.UTF8Encoding]::new($false)
    )
    Compress-Archive -LiteralPath $ReleaseDir -DestinationPath $ZipPath -CompressionLevel Optimal
    $ZipHash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    [System.IO.File]::WriteAllText(
        $ZipHashPath,
        "$ZipHash  $([System.IO.Path]::GetFileName($ZipPath))`n",
        [System.Text.UTF8Encoding]::new($false)
    )

    Write-Host "Release directory: $ReleaseDir"
    Write-Host "Release ZIP:       $ZipPath"
    Write-Host "ZIP SHA-256:       $ZipHash"
}
finally {
    Write-Host "Cleaning temporary virtual environment and build directories..."
    Remove-SafeWorkspaceTree $VenvPath
    Remove-SafeWorkspaceTree $BuildRoot
    Remove-SafeWorkspaceTree $EggInfo
}
