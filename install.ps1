# OptMem installer for Windows PowerShell. Run it again to update: it only
# replaces the tool and launcher; `memo init` preserves existing memories.
#
#   irm https://raw.githubusercontent.com/doeixd/OptMem/main/install.ps1 | iex

$ErrorActionPreference = 'Stop'
$InstallDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) '.optmem'
$BaseUrl = 'https://github.com/doeixd/OptMem/releases/latest/download'

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
$Comparison = [System.StringComparison]::OrdinalIgnoreCase
$NormalizedInstallDir = $InstallDir.TrimEnd('\')
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$UserParts = @($UserPath -split ';' | Where-Object { $_ })
$OnUserPath = $false
foreach ($Part in $UserParts) {
    if ([string]::Equals($Part.Trim().TrimEnd('\'), $NormalizedInstallDir, $Comparison)) {
        $OnUserPath = $true
        break
    }
}
if (-not $OnUserPath) {
    $NewUserPath = if ([string]::IsNullOrWhiteSpace($UserPath)) {
        $InstallDir
    } else {
        "$($UserPath.TrimEnd(';'));$InstallDir"
    }
    [Environment]::SetEnvironmentVariable('Path', $NewUserPath, 'User')
    Write-Host 'Added the memo command to your user PATH.'
} else {
    Write-Host 'The memo command is already configured on your user PATH.'
}
$OnProcessPath = $false
foreach ($Part in @($env:Path -split ';' | Where-Object { $_ })) {
    if ([string]::Equals($Part.Trim().TrimEnd('\'), $NormalizedInstallDir, $Comparison)) {
        $OnProcessPath = $true
        break
    }
}
if (-not $OnProcessPath) {
    $env:Path = "$InstallDir;$env:Path"
}
Write-Host "The 'memo' command is available in this PowerShell session."

$CompletionScript = Join-Path $InstallDir 'memo-completion.ps1'
& $Launcher completion powershell |
    Set-Content -LiteralPath $CompletionScript -Encoding UTF8
if ($LASTEXITCODE -ne 0) {
    throw 'Could not generate PowerShell completion.'
}
$ProfilePath = $PROFILE.CurrentUserAllHosts
$ProfileDir = Split-Path -Parent $ProfilePath
New-Item -ItemType Directory -Force -Path $ProfileDir | Out-Null
$CompletionSource = ". '$($CompletionScript.Replace("'", "''"))'"
$CompletionConfigured = Test-Path -LiteralPath $ProfilePath -PathType Leaf
if ($CompletionConfigured) {
    $CompletionConfigured = [bool](Select-String -LiteralPath $ProfilePath `
        -SimpleMatch $CompletionSource -Quiet)
}
if (-not $CompletionConfigured) {
    Add-Content -LiteralPath $ProfilePath -Value "`n# OptMem completion`n$CompletionSource"
}
. $CompletionScript
Write-Host "Installed PowerShell completion at $CompletionScript."
Write-Host ''
& $Launcher init
