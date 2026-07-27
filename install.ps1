# OptMem installer for Windows PowerShell. Run it again to update: it only
# replaces the tool and launcher; `memo init` preserves existing memories.
#
#   irm https://raw.githubusercontent.com/doeixd/OptMem/main/install.ps1 | iex

$ErrorActionPreference = 'Stop'
$InstallDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.optmem'
$BaseUrl = 'https://raw.githubusercontent.com/doeixd/OptMem/main'

$Py = Get-Command py -ErrorAction SilentlyContinue
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Py -and -not $Python) {
    throw 'OptMem is one Python file, and this machine has no Python 3. Install Python 3, then run this command again.'
}

if ($Py) {
    & $Py.Source -3 -c 'import sys; assert sys.version_info >= (3, 7)' 2>$null
} else {
    & $Python.Source -c 'import sys; assert sys.version_info >= (3, 7)' 2>$null
}
if ($LASTEXITCODE -ne 0) {
    throw 'OptMem requires Python 3.7 or newer.'
}

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$Memo = Join-Path $InstallDir 'memo'
$Launcher = Join-Path $InstallDir 'memo.cmd'
$MemoNew = "$Memo.new"
$LauncherNew = "$Launcher.new"

try {
    Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/memo" -OutFile $MemoNew
    Invoke-WebRequest -UseBasicParsing -Uri "$BaseUrl/memo.cmd" -OutFile $LauncherNew
    if ($Py) {
        & $Py.Source -3 $MemoNew --help *> $null
    } else {
        & $Python.Source $MemoNew --help *> $null
    }
    if ($LASTEXITCODE -ne 0) {
        throw 'The downloaded OptMem file did not pass validation.'
    }
    Move-Item -Force $MemoNew $Memo
    Move-Item -Force $LauncherNew $Launcher
} finally {
    Remove-Item -Force -ErrorAction SilentlyContinue $MemoNew, $LauncherNew
}

Write-Host "Installed OptMem at $Launcher"
$OnPath = ($env:Path -split ';') -contains $InstallDir
if ($OnPath) {
    Write-Host 'The memo command is available on PATH.'
} else {
    Write-Host "PATH is optional; add $InstallDir if you want to type 'memo' directly."
}
Write-Host ''
& $Launcher init
