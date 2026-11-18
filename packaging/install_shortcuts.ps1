<#
.SYNOPSIS
    Create the desktop shortcuts for iPhotron (windowed + console variant).

.DESCRIPTION
    The icon must live in the user profile, not in the repository: this checkout has
    hardened ACLs, and the Explorer process is denied read access there, so a shortcut
    pointing at ``<repo>\packaging\app.ico`` silently falls back to the generic
    document icon. Copying the icon to ``%LOCALAPPDATA%\iPhoto\app.ico`` fixes it.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File packaging\install_shortcuts.ps1
#>
param(
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'

$pythonw = Join-Path $RepoRoot '.venv\Scripts\pythonw.exe'
$python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$launcher = Join-Path $RepoRoot 'run_iphoto.pyw'

foreach ($required in @($pythonw, $python, $launcher)) {
    if (-not (Test-Path $required)) {
        throw "missing required file: $required"
    }
}

# The icon has to be readable by explorer.exe, which cannot read the repository path.
$iconDir = Join-Path $env:LOCALAPPDATA 'iPhoto'
New-Item -ItemType Directory -Force -Path $iconDir | Out-Null
$icon = Join-Path $iconDir 'app.ico'
Copy-Item (Join-Path $RepoRoot 'packaging\app.ico') $icon -Force

$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
try {
    $specs = @(
        @{ Name = 'iPhotron.lnk'; Target = $pythonw; Arguments = "`"$launcher`""; Description = 'iPhotron 照片库' },
        @{ Name = 'iPhotron (日志).lnk'; Target = $python; Arguments = '-m iPhoto.gui.main'; Description = 'iPhotron（显示控制台日志，排障用）' }
    )
    foreach ($spec in $specs) {
        $path = Join-Path $desktop $spec.Name
        Remove-Item $path -Force -ErrorAction SilentlyContinue
        $shortcut = $shell.CreateShortcut($path)
        $shortcut.TargetPath = $spec.Target
        $shortcut.Arguments = $spec.Arguments
        $shortcut.WorkingDirectory = $RepoRoot
        $shortcut.IconLocation = "$icon,0"
        $shortcut.Description = $spec.Description
        $shortcut.Save()
        Write-Host "created $path"
    }
}
finally {
    [System.Runtime.InteropServices.Marshal]::ReleaseComObject($shell) | Out-Null
}

Start-Process 'ie4uinit.exe' -ArgumentList '-show' -NoNewWindow -Wait -ErrorAction SilentlyContinue
Write-Host "icon: $icon"
