param([switch]$Incremental)

$ErrorActionPreference = "Stop"

function Copy-RuntimeLicense {
    param([string]$Uri, [string]$CachePath, [string]$Destination, [string]$Marker)
    if (-not (Test-Path -LiteralPath $CachePath -PathType Leaf)) {
        New-Item -ItemType Directory -Path (Split-Path $CachePath -Parent) -Force | Out-Null
        for ($Attempt = 1; $Attempt -le 3; $Attempt++) {
            try {
                $LicenseResponse = Invoke-WebRequest -Uri $Uri -ErrorAction Stop
                $LicenseText = [string]$LicenseResponse.Content
                if ($LicenseText -notmatch [regex]::Escape($Marker)) {
                    throw "下载的许可证文本无效，不得发行"
                }
                Set-Content -LiteralPath $CachePath -Value $LicenseText -Encoding utf8NoBOM
                break
            } catch {
                if ($Attempt -eq 3) { throw }
                Start-Sleep -Seconds 2
            }
        }
    }
    $CachedText = Get-Content -LiteralPath $CachePath -Raw -ErrorAction Stop
    if ($CachedText -notmatch [regex]::Escape($Marker)) {
        throw "缓存的许可证文本无效：$CachePath"
    }
    Copy-Item -LiteralPath $CachePath -Destination $Destination -Force -ErrorAction Stop
}

$ProjectRoot = $PSScriptRoot
$ApplicationName = "BaldrForceModifier"
$ConfiguredPython = if ($env:CONDA_PREFIX) {
    Join-Path $env:CONDA_PREFIX "python.exe"
} else {
    $null
}
$DefaultCondaPython = if ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE "miniforge3\envs\py314\python.exe"
} else {
    $null
}
$PythonExecutable = if ($ConfiguredPython -and (Test-Path -LiteralPath $ConfiguredPython -PathType Leaf)) {
    $ConfiguredPython
} elseif ($DefaultCondaPython -and (Test-Path -LiteralPath $DefaultCondaPython -PathType Leaf)) {
    $DefaultCondaPython
} else {
    (Get-Command python -ErrorAction Stop).Source
}

if (-not (Test-Path -LiteralPath $PythonExecutable -PathType Leaf)) {
    throw "找不到项目 Python 环境：$PythonExecutable"
}
$SourceDirectory = Join-Path $ProjectRoot "src"
$ProjectConfigurationPath = Join-Path $ProjectRoot "pyproject.toml"
$Version = (& $PythonExecutable -c "import sys; sys.path.insert(0, sys.argv[1]); import bfmod; print(bfmod.__version__)" $SourceDirectory | Out-String).Trim()
if (-not $Version) {
    throw "无法从 bfmod.__version__ 读取发行版本"
}
$ProjectVersion = (& $PythonExecutable -c "import sys, tomllib; print(tomllib.load(open(sys.argv[1], 'rb'))['project']['version'])" $ProjectConfigurationPath | Out-String).Trim()
if ($ProjectVersion -ne $Version) {
    throw "版本不一致：bfmod=$Version，pyproject=$ProjectVersion"
}
$RuntimeVersions = & $PythonExecutable -c "import PySide6, sys; print(f'{sys.version_info.major}.{sys.version_info.minor}'); print(PySide6.__version__)"
if ($LASTEXITCODE -ne 0 -or $RuntimeVersions.Count -ne 2) {
    throw "无法确定构建环境的 Python 和 PySide6 版本"
}
$PythonVersion = $RuntimeVersions[0].Trim()
$PySideVersion = $RuntimeVersions[1].Trim()
$PythonLicensePath = Join-Path (Split-Path $PythonExecutable -Parent) "LICENSE_PYTHON.txt"
if (-not (Test-Path -LiteralPath $PythonLicensePath -PathType Leaf)) {
    throw "构建环境缺少 Python 许可证：$PythonLicensePath"
}
$VersionInfoText = Get-Content -LiteralPath (Join-Path $ProjectRoot "version_info.txt") -Raw -ErrorAction Stop
$VersionParts = @($Version.Split('.') | ForEach-Object { [int]$_ })
if ($VersionParts.Count -ne 3) {
    throw "发行版本必须使用三段数字格式：$Version"
}
$PeVersionTuple = "($($VersionParts[0]), $($VersionParts[1]), $($VersionParts[2]), 0)"
$RequiredVersionResourceFragments = @(
    "filevers=$PeVersionTuple",
    "prodvers=$PeVersionTuple",
    "StringStruct('FileVersion', '$Version')",
    "StringStruct('ProductVersion', '$Version')"
)
foreach ($VersionResourceFragment in $RequiredVersionResourceFragments) {
    if ($VersionInfoText -notmatch [regex]::Escape($VersionResourceFragment)) {
        throw "version_info.txt 的版本资源与 $Version 不一致：$VersionResourceFragment"
    }
}
$ReleaseArchiveName = "$ApplicationName-v$Version-windows-x64.zip"
$VersionedDistributionRoot = Join-Path $ProjectRoot "dist\v$Version-release"
$OriginalBuildPath = $env:PATH
$RuntimeLibraryBin = Join-Path (Split-Path $PythonExecutable -Parent) "Library\bin"

Push-Location $ProjectRoot
try {
    # 限定 DLL 搜索路径，避免把开发机其他工具的同名 ICU/Qt DLL 打入发行包。
    $BuildRuntimeDirectory = Split-Path $PythonExecutable -Parent
    $BuildSearchPaths = @($BuildRuntimeDirectory)
    if (Test-Path -LiteralPath $RuntimeLibraryBin -PathType Container) {
        $BuildSearchPaths += $RuntimeLibraryBin
    }
    $BuildSearchPaths += (Join-Path $env:WINDIR "System32")
    $BuildSearchPaths += $env:WINDIR
    $env:PATH = $BuildSearchPaths -join [IO.Path]::PathSeparator
    $CleanArguments = if ($Incremental) { @() } else { @("--clean") }
    & $PythonExecutable -m PyInstaller `
        --noconfirm `
        @CleanArguments `
        --windowed `
        --onedir `
        --distpath $VersionedDistributionRoot `
        --name $ApplicationName `
        --paths ".\src" `
        --version-file ".\version_info.txt" `
        ".\src\bfmod\launcher.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败，退出代码：$LASTEXITCODE"
    }
    $DistributionDirectory = Join-Path $VersionedDistributionRoot $ApplicationName
    $SmokeProcess = Start-Process `
        -FilePath (Join-Path $DistributionDirectory "$ApplicationName.exe") `
        -ArgumentList "--smoke-test" `
        -WindowStyle Hidden `
        -PassThru
    if (-not $SmokeProcess.WaitForExit(30000)) {
        $SmokeProcess.Kill()
        throw "发行包启动自检超时，不生成发行 ZIP"
    }
    if ($SmokeProcess.ExitCode -ne 0) {
        throw "发行包启动自检失败，退出代码：$($SmokeProcess.ExitCode)"
    }
    $LicenseDirectory = Join-Path $DistributionDirectory "LICENSES"
    $ReleaseDirectory = Join-Path $ProjectRoot "release"
    $ReleaseArchivePath = Join-Path $ReleaseDirectory $ReleaseArchiveName
    $ChecksumPath = Join-Path $ReleaseDirectory "SHA256SUMS.txt"

    Copy-Item -LiteralPath ".\README.md" -Destination $DistributionDirectory -Force -ErrorAction Stop
    Copy-Item -LiteralPath ".\RELEASE_NOTES.md" -Destination $DistributionDirectory -Force -ErrorAction Stop
    Copy-Item -LiteralPath ".\SECURITY.md" -Destination $DistributionDirectory -Force -ErrorAction Stop
    Copy-Item -LiteralPath ".\LICENSE" -Destination $DistributionDirectory -Force -ErrorAction Stop
    Copy-Item -LiteralPath ".\THIRD_PARTY_NOTICES.txt" -Destination $DistributionDirectory -Force -ErrorAction Stop
    New-Item -ItemType Directory -Path $LicenseDirectory -Force -ErrorAction Stop | Out-Null
    $LicenseCache = Join-Path $ProjectRoot "build\licenses\PySide6-$PySideVersion"
    Copy-RuntimeLicense `
        -Uri "https://code.qt.io/cgit/pyside/pyside-setup.git/plain/LICENSES/LGPL-3.0-only.txt?h=$PySideVersion" `
        -CachePath (Join-Path $LicenseCache "LGPL-3.0-only.txt") `
        -Destination (Join-Path $LicenseDirectory "LGPL-3.0-only.txt") `
        -Marker 'GNU LESSER GENERAL PUBLIC LICENSE'
    Copy-RuntimeLicense `
        -Uri "https://code.qt.io/cgit/pyside/pyside-setup.git/plain/LICENSES/GPL-3.0-only.txt?h=$PySideVersion" `
        -CachePath (Join-Path $LicenseCache "GPL-3.0-only.txt") `
        -Destination (Join-Path $LicenseDirectory "GPL-3.0-only.txt") `
        -Marker 'GNU GENERAL PUBLIC LICENSE'
    Copy-Item `
        -LiteralPath $PythonLicensePath `
        -Destination (Join-Path $LicenseDirectory "Python-$PythonVersion.txt") `
        -Force `
        -ErrorAction Stop

    New-Item -ItemType Directory -Path $ReleaseDirectory -Force -ErrorAction Stop | Out-Null
    Compress-Archive `
        -LiteralPath $DistributionDirectory `
        -DestinationPath $ReleaseArchivePath `
        -CompressionLevel Optimal `
        -Force `
        -ErrorAction Stop
    $ArchiveHash = (Get-FileHash -LiteralPath $ReleaseArchivePath -Algorithm SHA256 -ErrorAction Stop).Hash
    Set-Content `
        -LiteralPath $ChecksumPath `
        -Value "$ArchiveHash  $ReleaseArchiveName" `
        -Encoding utf8NoBOM `
        -NoNewline `
        -ErrorAction Stop

    Write-Host "构建完成：$DistributionDirectory\$ApplicationName.exe"
    Write-Host "发行包：$ReleaseArchivePath"
    Write-Host "SHA-256：$ArchiveHash"
}
finally {
    $env:PATH = $OriginalBuildPath
    Pop-Location
}
