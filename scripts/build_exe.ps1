# WUXICODE EXE build
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#   powershell -File scripts\build_exe.ps1 -Clean
#   powershell -File scripts\build_exe.ps1 -Name WUXICODE -SkipIcon
#
# Produces dist\<Name>.exe (single file, console subsystem) with the icon and
# the version resource from version.json baked into its properties.
#
# Paths are anchored to $PSScriptRoot, so the script works from any directory.
# Every PyInstaller flag below exists because its absence produces a specific
# failure:
#
#   --paths python / .     cli.py runs as a script, so sys.path[0] is python\wx
#                          and `from wx import ...` would not resolve. "." is
#                          for license_server, which session_manager imports.
#   --add-data wx/config  config.py resolves its package default as
#                         Path(__file__).parent / "config.json", which inside a
#                         onefile bundle resolves to _MEIPASS\wx\config.json.
#   --collect-binaries    _wx_core is imported lazily inside wx.require_core().
#   --hidden-import       wx.learn, wx.context and wx.ui pull modules in through
#                         __getattr__ and function bodies.
#   --exclude-module      torch/transformers/numpy are not runtime deps; a stray
#                         copy in site-packages would otherwise inflate the EXE.

[CmdletBinding()]
param(
    [string] $Name = "WUXICODE",
    [string] $Icon = "assets\icon.ico",
    [switch] $Clean,
    [switch] $Windowed,
    [switch] $SkipIcon
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Ok($msg) { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  [!!] $msg" -ForegroundColor Yellow }
function Die($msg) { Write-Host "  [XX] $msg" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------------------
Step "Preflight"
# ---------------------------------------------------------------------------

$python = $null
foreach ($cand in @("python", "py -3", "python3")) {
    $exe = $cand.Split(" ")[0]
    if (Get-Command $exe -ErrorAction SilentlyContinue) { $python = $cand; break }
}
if (-not $python) { Die "python not found on PATH" }

$pyVer = & $python -c "import sys;print('.'.join(map(str,sys.version_info[:3])))"
if ($LASTEXITCODE -ne 0) { Die "python is not runnable: $python" }
Ok "python $pyVer"

if ([version]$pyVer -lt [version]"3.10") {
    Die "python >= 3.10 required (pyproject: requires-python)"
}

# The native core. wx.cli calls require_core() for --doctor and the tokenizer,
# so a missing extension means a broken EXE rather than a degraded one.
$core = & $python -c "import _wx_core;print(_wx_core.__file__)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "_wx_core not importable - the EXE will ship without the native core"
    Warn "  fix: pip install -e .   (needs CMake >= 3.24 and MSVC)"
} else {
    Ok "native core: $core"
}

# ---------------------------------------------------------------------------
Step "Icon"
# ---------------------------------------------------------------------------

if (-not $SkipIcon) {
    if (-not (Test-Path $Icon)) {
        Warn "$Icon missing - generating it"
        & $python "scripts\make_icon.py"
        if ($LASTEXITCODE -ne 0) { Die "make_icon.py failed" }
    }
    if (Test-Path $Icon) {
        Ok "$Icon ($([math]::Round((Get-Item $Icon).Length / 1KB)) KB)"
    } else {
        Warn "no icon available - building without one"
        $Icon = $null
    }
} elseif (-not (Test-Path $Icon)) {
    $Icon = $null
}

# ---------------------------------------------------------------------------
Step "Version metadata"
# ---------------------------------------------------------------------------

$versionFile = $null
$vjPath = Join-Path $Root "version.json"
if (Test-Path $vjPath) {
    New-Item -ItemType Directory -Force -Path "build" | Out-Null
    $versionFile = "build\wx_version_info.txt"

    # PyInstaller parses --version-file itself and builds the binary
    # VS_VERSIONINFO resource from this text form (see
    # PyInstaller/utils/win32/versioninfo.py). Emitting it here keeps the EXE's
    # file properties single-sourced from version.json -- the same file CMake
    # bakes WX_VERSION_STRING from -- so the two cannot drift apart.
    $pyFile = Join-Path $env:TEMP "wx_make_version_info.py"
    [IO.File]::WriteAllText($pyFile, @'
"""Emit the text VS_VERSIONINFO block for PyInstaller --version-file."""

import json
import pathlib
import sys


def main():
    out_path = pathlib.Path(sys.argv[1])
    name = sys.argv[2]

    vj = json.loads(pathlib.Path(sys.argv[3]).read_text(encoding="utf-8"))
    ver = vj["version"]
    channel = vj["channel"]
    # FixedFileInfo wants four comma-separated fields.
    quad = ",".join(list(ver.split(".") + ["0"] * 4)[:4])

    strings = (
        ("CompanyName", "WUXICODE"),
        ("FileDescription", "WUXICODE coding agent"),
        ("FileVersion", ver),
        ("InternalName", name),
        ("LegalCopyright", "Copyright (c) 2026 ujin"),
        ("OriginalFilename", name + ".exe"),
        ("ProductName", "WUXICODE"),
        ("ProductVersion", ver),
        ("Comments", "Build channel: " + channel),
    )

    lines = [
        "# UTF-8",
        "",
        "VSVersionInfo(",
        "  ffi=FixedFileInfo(",
        f"    filevers=({quad}),",
        f"    prodvers=({quad}),",
        "    mask=0x3f,",
        "    flags=0x0,",
        "    OS=0x40004,",
        "    fileType=0x1,",
        "    subtype=0x0,",
        "    date=(0, 0)",
        "  ),",
        "  kids=[",
        "    StringFileInfo([",
        "      StringTable(",
        "        '040904B0',",
        "        [",
    ]
    # StringTable children are StringStruct objects, not bare pairs.
    lines += [f"          StringStruct('{k}', '{v}')," for k, v in strings]
    lines += [
        "        ]",
        "      )",
        "    ]),",
        "    VarFileInfo([VarStruct('Translation', [0x409, 1200])])",
        "  ]",
        ")",
        "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"{ver} {channel} {out_path.stat().st_size} bytes")


if __name__ == "__main__":
    main()
'@, (New-Object Text.UTF8Encoding $false))

    $verOut = & $python $pyFile $versionFile $Name $vjPath 2>&1 | Out-String
    if ($LASTEXITCODE -ne 0) { Die "version metadata generation failed:`n$verOut" }
    $vfBytes = (Get-Item $versionFile).Length
    if ($vfBytes -lt 120) { Die "version file looks wrong ($vfBytes bytes)" }
    Ok "version $($verOut.Trim()) -> $versionFile"
} else {
    Warn "version.json missing - building without version metadata"
}

# ---------------------------------------------------------------------------
Step "Version sources"
# ---------------------------------------------------------------------------

# A stale extension bakes an old WX_VERSION_STRING, so the EXE would report the
# old number from `--version` while its file properties carry the new one. One
# second here beats finding that out after shipping.
$check = & $python "scripts\check_version.py" 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Die "version sources disagree:`n$check" }
Ok (($check -split "`r?`n" | Where-Object { $_ -match "agree" } | Select-Object -First 1))

# ---------------------------------------------------------------------------
Step "PyInstaller"
# ---------------------------------------------------------------------------

$piVer = & $python -c "import PyInstaller;print(PyInstaller.__version__)" 2>$null
if ($LASTEXITCODE -ne 0) {
    Warn "installing pyinstaller"
    & $python -m pip install --quiet --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) { Die "pip install pyinstaller failed" }
    $piVer = & $python -c "import PyInstaller;print(PyInstaller.__version__)"
}
Ok "pyinstaller $piVer"

$piArgs = @(
    "-m", "PyInstaller"
    "--noconfirm"
    "--onefile"
    "--name", $Name
    "--paths", "python"
    "--paths", "."
    "--add-data", "python/wx/config.json;wx"
    "--collect-submodules", "wx"
    "--collect-submodules", "license_server"
    "--hidden-import", "_wx_core"
    "--hidden-import", "wx.cli"
    "--hidden-import", "wx.ui.banner"
    "--hidden-import", "wx.ui.brand"
    "--hidden-import", "wx.ui.history"
    "--hidden-import", "wx.ui.preview"
    "--hidden-import", "wx.ui.session_tabs"
    "--hidden-import", "wx.ui.token_meter"
    "--hidden-import", "wx.ui.width"
    "--hidden-import", "wx.ui.model_selector"
    "--hidden-import", "wx.ui.provider_manager"
    "--hidden-import", "wx.llm.openai_client"
    "--hidden-import", "wx.llm.router"
    "--hidden-import", "wx.context.assembly"
    "--hidden-import", "wx.learn.store"
    "--hidden-import", "wx.learn.capture"
    "--hidden-import", "wx.learn.extract"
    "--hidden-import", "wx.learn.inject"
    "--hidden-import", "wx.learn.digest"
    "--hidden-import", "wx.learn.index"
    "--hidden-import", "wx.modes.session_manager"
    "--hidden-import", "license_server.tiers"
    "--hidden-import", "httpx"
    "--hidden-import", "certifi"
    "--exclude-module", "torch"
    "--exclude-module", "transformers"
    "--exclude-module", "trl"
    "--exclude-module", "numpy"
    "--exclude-module", "scipy"
    "--exclude-module", "pandas"
    "--exclude-module", "matplotlib"
    "--exclude-module", "tkinter"
    "--exclude-module", "pytest"
    "--exclude-module", "PyQt5"
    "--exclude-module", "PySide6"
    "--noupx"
)

if ($Windowed) { $piArgs += "--windowed" } else { $piArgs += "--console" }
if ($Clean) { $piArgs += "--clean" }
if ($Icon) { $piArgs += @("--icon", $Icon) }
if ($versionFile) { $piArgs += @("--version-file", $versionFile) }
$piArgs += "python/wx/cli.py"

if ($Clean) {
    foreach ($d in @("build\WUXICODE", "dist")) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d }
    }
}
New-Item -ItemType Directory -Force -Path "build" | Out-Null
New-Item -ItemType Directory -Force -Path "dist" | Out-Null

Write-Host ""
Write-Host "  entry : python\wx\cli.py  (wx.cli:main)" -ForegroundColor DarkGray
Write-Host "  output: dist\$Name.exe" -ForegroundColor DarkGray
Write-Host "  build : 2-10 min depending on disk" -ForegroundColor DarkGray
Write-Host ""

$sw = [Diagnostics.Stopwatch]::StartNew()
& $python @piArgs
$code = $LASTEXITCODE
$sw.Stop()

if ($code -ne 0) { Die "pyinstaller exited $code after $([math]::Round($sw.Elapsed.TotalMinutes,1)) min" }

# ---------------------------------------------------------------------------
Step "Verify"
# ---------------------------------------------------------------------------

$exe = Join-Path $Root "dist\$Name.exe"
if (-not (Test-Path $exe)) { Die "dist\$Name.exe was not produced" }

$mb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Ok "$Name.exe  $mb MB  ($([math]::Round($sw.Elapsed.TotalMinutes,1)) min)"

# A onefile bundle unpacks to %TEMP%\_MEIxxxx on every launch, so a missing
# runtime DLL only surfaces at execution time. Running --version proves the
# bootloader unpacked, the interpreter started and wx.cli imported.
Write-Host ""
Write-Host "  smoke test: $Name.exe --version" -ForegroundColor DarkGray
$v = & $exe --version 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Die "smoke test failed:`n$v" }
Ok "runs: $($v.Trim())"

$doctor = & $exe --doctor 2>&1 | Out-String
$coreLine = ($doctor -split "`r?`n" | Where-Object { $_ -match "native core" } | Select-Object -First 1)
if ($coreLine -and $coreLine -match "OK") {
    Ok "native core inside the bundle"
} else {
    Warn "native core not reported OK by --doctor:"
    Write-Host $doctor
}

Write-Host ""
Write-Host "=== done ===" -ForegroundColor Green
Write-Host "  $exe" -ForegroundColor Cyan
Write-Host "  sha256 $((Get-FileHash $exe -Algorithm SHA256).Hash)" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  run   :  .\dist\$Name.exe" -ForegroundColor Gray
Write-Host "  config:  %USERPROFILE%\.wuxicode\config.json" -ForegroundColor Gray
Write-Host "  models:  point WX_LLM_BASE_URL at your local Ollama to use WX-1" -ForegroundColor Gray
Write-Host ""
