<#
Build Recap Studio for Windows, in dist\, as Lite (the app, with no plugins) or Full Suite (the app with the
feature plugins on Recap Studio's own plugin list built in, in its plugins\ folder):
  Recap Studio\                                the app with its own Python and packages, and Recap Studio.exe
  Recap-Studio-Lite-<version>-portable.zip     that folder, zipped
  Recap-Studio-Lite-<version>-setup.exe        the installer, when Inno Setup 6 is installed (packaging\installer.iss)
  Recap-Studio-Full-Portable-<version>.zip     Full Suite's (-Edition full)
  Recap-Studio-Full-Setup-<version>.exe

  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.0 -Edition lite
  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.0 -Edition full

Lite's names end the way copies on 0.1.x look for their update (-setup.exe, -portable.zip), so those stay Lite;
Full Suite's don't. Both write EDITION next to VERSION, and from 0.2.0 on the updater picks its own edition's file
(recap\updates.py).

Full Suite's plugins come from the plugin list at the commit packaging\plugins.json pins (bundle_plugins.py). Before
tagging a release, move it to the list's latest commit (once its tests pass against this version) and tag that
commit of the list app-v<version>, so it's clear which plugins shipped.

GitHub Actions runs both for every v* tag (.github\workflows\build.yml). Needs internet: Python comes from
python-build-standalone (pinned below, checked against its SHA-256), the packages from PyPI and Full Suite's plugins
from GitHub.
#>
param(
    [string]$Version = "0.0.0-dev",
    [ValidateSet("lite", "full")]
    [string]$Edition = "lite",
    [string]$PluginsFrom = "",  # a checkout of the plugin list to take Full Suite's plugins from, instead of GitHub
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much faster without its progress bar

# a relocatable CPython: it runs from wherever the folder is unzipped or installed
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-pc-windows-msvc-install_only.tar.gz"
$PythonSha256 = "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5"

if ($Edition -eq "full") {
    $zipName = "Recap-Studio-Full-Portable-$Version.zip"
    $setupName = "Recap-Studio-Full-Setup-$Version"
} else {
    $zipName = "Recap-Studio-Lite-$Version-portable.zip"
    $setupName = "Recap-Studio-Lite-$Version-setup"
}

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$dist = Join-Path $root "dist"
$app = Join-Path $dist "Recap Studio"
$cache = Join-Path $root "build"
$tar = Join-Path $env:SystemRoot "System32\tar.exe"  # Windows' own tar (Git's would read C: as a host name)
New-Item -ItemType Directory -Force $dist, $cache | Out-Null
if (Test-Path $app) { Remove-Item -Recurse -Force $app }
New-Item -ItemType Directory -Force $app | Out-Null

Write-Host "== Python"
$archive = Join-Path $cache "python-3.12.16.tar.gz"
if (-not (Test-Path $archive) -or (Get-FileHash $archive -Algorithm SHA256).Hash -ne $PythonSha256) {
    Invoke-WebRequest $PythonUrl -OutFile "$archive.part"
    Move-Item -Force "$archive.part" $archive
}
if ((Get-FileHash $archive -Algorithm SHA256).Hash -ne $PythonSha256) {
    Remove-Item -Force $archive
    throw "The Python download doesn't match its SHA-256."
}
& $tar -xzf $archive -C $app  # makes $app\python
if ($LASTEXITCODE) { throw "Couldn't unpack Python." }
$py = Join-Path $app "python\python.exe"

Write-Host "== Packages"
# the CPU build of ONNX Runtime (requirements.txt), and ffmpeg from imageio-ffmpeg for the video; not compiled
# ahead (--no-compile): the packages' __pycache__ files make the longest paths (anthropic's go past 160
# characters), and Windows stops at 260 with the install folder included. Python compiles them on first use.
& $py -m pip install --no-warn-script-location --disable-pip-version-check --no-compile -r requirements.txt imageio-ffmpeg
if ($LASTEXITCODE) { throw "pip install failed." }

Write-Host "== App"
Copy-Item -Recurse (Join-Path $root "recap") (Join-Path $app "recap")
New-Item -ItemType Directory -Force (Join-Path $app ".claude\skills") | Out-Null
Copy-Item -Recurse (Join-Path $root ".claude\skills\recap-studio-narration") (Join-Path $app ".claude\skills\recap-studio-narration")
Copy-Item (Join-Path $root "README.md"), (Join-Path $root "LICENSE") $app
Get-ChildItem -Path $app -Recurse -Directory -Filter __pycache__ | Where-Object { $_.FullName -notlike "$app\python\*" } |
    Remove-Item -Recurse -Force
Set-Content -Encoding ascii (Join-Path $app "VERSION") $Version
Set-Content -Encoding ascii (Join-Path $app "EDITION") $Edition
$compile = @((Join-Path $app "python\Lib"), (Join-Path $app "recap"))

if ($Edition -eq "full") {
    Write-Host "== Full Suite's plugins"
    $bundle = @("-I", (Join-Path $PSScriptRoot "bundle_plugins.py"), $app)
    if ($PluginsFrom) { $bundle += @("--from", $PluginsFrom) }
    & $py @bundle
    if ($LASTEXITCODE) { throw "Couldn't bundle the plugins." }
    # Python packages a plugin needs that the app doesn't have (none do yet)
    foreach ($req in Get-ChildItem -Path (Join-Path $app "plugins\*\requirements.txt") -ErrorAction SilentlyContinue) {
        & $py -m pip install --no-warn-script-location --disable-pip-version-check --no-compile -r $req.FullName
        if ($LASTEXITCODE) { throw "pip install failed for $($req.Directory.Name)." }
    }
    $compile += Join-Path $app "plugins"
}

# Python's own library and the app compiled ahead, so the first start is quicker (a few files that don't compile
# are only tests)
& $py -m compileall -q -j 0 -x "site-packages" @compile | Out-Null
$longest = (Get-ChildItem -Recurse -File $app | ForEach-Object { $_.FullName.Length - $app.Length } | Measure-Object -Maximum).Maximum
Write-Host "Longest path inside the app folder: $longest characters (Windows allows 260 with the folder's own path)"

Write-Host "== Launcher"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $csc /nologo /target:winexe /optimize+ "/win32icon:packaging\launcher\icon.ico" /reference:System.Windows.Forms.dll `
    "/out:$app\Recap Studio.exe" packaging\launcher\RecapStudio.cs
if ($LASTEXITCODE) { throw "The launcher didn't compile." }

Write-Host "== Portable zip"
$zip = Join-Path $dist $zipName
if (Test-Path $zip) { Remove-Item -Force $zip }
Push-Location $dist
& $tar -a -cf $zip "Recap Studio"
$failed = $LASTEXITCODE
Pop-Location
if ($failed) { throw "Couldn't zip the app." }

if ($SkipInstaller) { Write-Host "Done (no installer: -SkipInstaller)."; exit 0 }
Write-Host "== Installer"
# Installed in Program Files, Python can't save its compiled packages there, so it would compile them again on
# every start: compile them now, for the installer only (the zip went without, for its shorter paths). They're
# at most 165 characters below the app's folder, well within Windows' limit under C:\Program Files.
& $py -m compileall -q -j 0 (Join-Path $app "python\Lib\site-packages") | Out-Null
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
foreach ($p in "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
               "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") {
    if (-not $iscc -and (Test-Path $p)) { $iscc = $p }
}
if (-not $iscc) {
    Write-Warning "Inno Setup 6 isn't installed, so there's no installer; the portable zip is in dist."
    exit 0
}
& $iscc /Qp "/DAppVersion=$Version" "/DEdition=$Edition" "/DOutputBase=$setupName" "/DAppDir=$app" "/DOutputDir=$dist" `
    packaging\installer.iss
if ($LASTEXITCODE) { throw "The installer didn't compile." }
Write-Host "Done: $dist"
