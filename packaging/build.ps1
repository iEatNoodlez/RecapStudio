<#
Build Recap Studio for Windows, in dist\:
  Recap Studio\                         the app with its own Python and packages, and Recap Studio.exe
  Recap-Studio-<version>-portable.zip   that folder, zipped
  Recap-Studio-<version>-setup.exe      the installer, when Inno Setup 6 is installed (packaging\installer.iss)

  powershell -ExecutionPolicy Bypass -File packaging\build.ps1 -Version 1.0.0

GitHub Actions runs it for every v* tag (.github\workflows\build.yml). Needs internet: Python comes from
python-build-standalone (pinned below, checked against its SHA-256) and the packages from PyPI.
#>
param(
    [string]$Version = "0.0.0-dev",
    [switch]$SkipInstaller
)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much faster without its progress bar

# a relocatable CPython: it runs from wherever the folder is unzipped or installed
$PythonUrl = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.12.16%2B20261026-x86_64-pc-windows-msvc-install_only.tar.gz"
$PythonSha256 = "590dfcbe3a34c07301e6332e13e2188b98655cd072b08afa45a7998dc4c22bc5"

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
# Python's own library and the app compiled ahead, so the first start is quicker (a few files that don't compile
# are only tests)
& $py -m compileall -q -j 0 -x "site-packages" (Join-Path $app "python\Lib") (Join-Path $app "recap") | Out-Null
$longest = (Get-ChildItem -Recurse -File $app | ForEach-Object { $_.FullName.Length - $app.Length } | Measure-Object -Maximum).Maximum
Write-Host "Longest path inside the app folder: $longest characters (Windows allows 260 with the folder's own path)"

Write-Host "== Launcher"
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
& $csc /nologo /target:winexe /optimize+ "/win32icon:packaging\launcher\icon.ico" /reference:System.Windows.Forms.dll `
    "/out:$app\Recap Studio.exe" packaging\launcher\RecapStudio.cs
if ($LASTEXITCODE) { throw "The launcher didn't compile." }

Write-Host "== Portable zip"
$zip = Join-Path $dist "Recap-Studio-$Version-portable.zip"
if (Test-Path $zip) { Remove-Item -Force $zip }
Push-Location $dist
& $tar -a -cf $zip "Recap Studio"
$failed = $LASTEXITCODE
Pop-Location
if ($failed) { throw "Couldn't zip the app." }

if ($SkipInstaller) { Write-Host "Done (no installer: -SkipInstaller)."; exit 0 }
Write-Host "== Installer"
$iscc = (Get-Command iscc.exe -ErrorAction SilentlyContinue).Source
foreach ($p in "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
               "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") {
    if (-not $iscc -and (Test-Path $p)) { $iscc = $p }
}
if (-not $iscc) {
    Write-Warning "Inno Setup 6 isn't installed, so there's no installer; the portable zip is in dist."
    exit 0
}
& $iscc /Qp "/DAppVersion=$Version" "/DAppDir=$app" "/DOutputDir=$dist" packaging\installer.iss
if ($LASTEXITCODE) { throw "The installer didn't compile." }
Write-Host "Done: $dist"
