$ErrorActionPreference = "Stop"

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

Push-Location $ProjectRoot
try {
    & $PythonExecutable -m PyInstaller `
        --noconfirm `
        --clean `
        --windowed `
        --onedir `
        --name $ApplicationName `
        --paths ".\src" `
        --version-file ".\version_info.txt" `
        ".\src\bfmod\launcher.py"
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller 构建失败，退出代码：$LASTEXITCODE"
    }
    $DistributionDirectory = Join-Path $ProjectRoot "dist\$ApplicationName"
    $LicenseDirectory = Join-Path $DistributionDirectory "LICENSES"
    $ReleaseDirectory = Join-Path $ProjectRoot "release"
    $ReleaseArchivePath = Join-Path $ReleaseDirectory $ReleaseArchiveName
    $ChecksumPath = Join-Path $ReleaseDirectory "SHA256SUMS.txt"

    Copy-Item -LiteralPath ".\README.md" -Destination $DistributionDirectory -Force -ErrorAction Stop
    Copy-Item -LiteralPath ".\SECURITY.md" -Destination $DistributionDirectory -Force -ErrorAction Stop
    Copy-Item -LiteralPath ".\LICENSE" -Destination $DistributionDirectory -Force -ErrorAction Stop
    Copy-Item -LiteralPath ".\THIRD_PARTY_NOTICES.txt" -Destination $DistributionDirectory -Force -ErrorAction Stop
    New-Item -ItemType Directory -Path $LicenseDirectory -Force -ErrorAction Stop | Out-Null
    Invoke-WebRequest `
        -Uri "https://code.qt.io/cgit/pyside/pyside-setup.git/plain/LICENSES/LGPL-3.0-only.txt?h=$PySideVersion" `
        -OutFile (Join-Path $LicenseDirectory "LGPL-3.0-only.txt") `
        -ErrorAction Stop
    $LgplText = Get-Content -LiteralPath (Join-Path $LicenseDirectory "LGPL-3.0-only.txt") -Raw -ErrorAction Stop
    if ($LgplText -notmatch 'GNU LESSER GENERAL PUBLIC LICENSE') {
        throw "下载的 LGPL 文本无效，不得发行"
    }
    Invoke-WebRequest `
        -Uri "https://code.qt.io/cgit/pyside/pyside-setup.git/plain/LICENSES/GPL-3.0-only.txt?h=$PySideVersion" `
        -OutFile (Join-Path $LicenseDirectory "GPL-3.0-only.txt") `
        -ErrorAction Stop
    $GplText = Get-Content -LiteralPath (Join-Path $LicenseDirectory "GPL-3.0-only.txt") -Raw -ErrorAction Stop
    if ($GplText -notmatch 'GNU GENERAL PUBLIC LICENSE') {
        throw "下载的 GPL 文本无效，不得发行"
    }
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
    Pop-Location
}
