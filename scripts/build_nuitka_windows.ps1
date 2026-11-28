param(
    [string]$PythonExe = "",
    [string]$OutputDir = "build",
    [ValidateRange(1, 64)]
    [int]$Jobs = [Math]::Max(1, [Environment]::ProcessorCount),
    [string]$IconPath = "",
    [ValidateSet("disable", "attach", "force")]
    [string]$ConsoleMode = "disable",
    [switch]$RebuildNativeRuntime,
    [switch]$SkipNativeRuntimeSync
)

$ErrorActionPreference = "Stop"

function Assert-Exists {
    param([Parameter(Mandatory = $true)][string]$PathToCheck)
    if (-not (Test-Path $PathToCheck)) {
        throw "Required path does not exist: $PathToCheck"
    }
}

function Sync-NativeRuntime {
    param(
        [Parameter(Mandatory = $true)][string]$SourceDir,
        [Parameter(Mandatory = $true)][string]$DestinationDir
    )

    $requiredFiles = @(
        'osmand_render_helper.exe',
        'osmand_native_widget.dll',
        'OsmAndCore_shared.dll',
        'OsmAndCoreTools_shared.dll'
    )

    foreach ($fileName in $requiredFiles) {
        Assert-Exists (Join-Path $SourceDir $fileName)
    }

    New-Item -ItemType Directory -Force -Path $DestinationDir | Out-Null
    foreach ($fileName in $requiredFiles) {
        Copy-Item -LiteralPath (Join-Path $SourceDir $fileName) -Destination $DestinationDir -Force
    }
    Get-ChildItem -Path $SourceDir -Filter '*.dll' -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $DestinationDir -Force
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$srcRoot = Join-Path $repoRoot 'src'
$mainScript = Join-Path $srcRoot 'iPhoto\gui\main.py'
$nativeBuildScript = Join-Path $repoRoot 'tools\osmand_render_helper_native\build_native_widget_msvc.ps1'
$nativeDistDir = Join-Path $repoRoot 'tools\osmand_render_helper_native\dist-msvc'
$extensionBinDir = Join-Path $srcRoot 'maps\tiles\extension\bin'
# One canonical model root for every registry. Face packs are the extracted
# ``buffalo_*`` directories; the .zip files are only upstream downloads and are
# never read at runtime, so they are not shipped.
$faceModelRoot = Join-Path $repoRoot 'extension\models'
$clipModelDir = Join-Path $faceModelRoot 'clip-vit-base-patch32'
$faceModelPacks = @()
if (Test-Path -LiteralPath $faceModelRoot) {
    $faceModelPacks = @(
        Get-ChildItem -LiteralPath $faceModelRoot -Directory -Filter 'buffalo_*' |
            Sort-Object Name
    )
}

Assert-Exists $repoRoot
Assert-Exists $srcRoot
Assert-Exists $mainScript
Assert-Exists $nativeBuildScript
if ($faceModelPacks.Count -eq 0) {
    Write-Warning "No face model packs under $faceModelRoot; the packaged app will download them on first use."
}

if (-not $PythonExe) {
    $venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    }
    else {
        $PythonExe = 'python'
    }
}

if (-not $IconPath) {
    # Prefer the generated application icon; fall back to the older repo logo.
    $packagedIcon = Join-Path $repoRoot 'packaging\app.ico'
    if (Test-Path $packagedIcon) {
        $IconPath = $packagedIcon
    }
    else {
        $defaultIcon = Join-Path $repoRoot 'logo_new.ico'
        if (Test-Path $defaultIcon) {
            $IconPath = $defaultIcon
        }
    }
}

if ($RebuildNativeRuntime) {
    & $nativeBuildScript -BuildType Release -Jobs $Jobs
    if ($LASTEXITCODE -ne 0) {
        throw "Native runtime rebuild failed with exit code $LASTEXITCODE"
    }
}

if (-not $SkipNativeRuntimeSync) {
    Assert-Exists $nativeDistDir
    Sync-NativeRuntime -SourceDir $nativeDistDir -DestinationDir $extensionBinDir
    Write-Host "Synced native map runtime into: $extensionBinDir"
}

$arguments = @(
    '-m', 'nuitka',
    '--standalone',
    "--jobs=$Jobs",
    '--msvc=latest',
    '--lto=yes',
    '--follow-imports',
    '--python-flag=no_site',
    '--enable-plugin=pyside6',
    '--include-qt-plugins=qml,multimedia,platforms',
    "--windows-console-mode=$ConsoleMode",
    '--assume-yes-for-downloads',
    '--nofollow-import-to=numba',
    '--nofollow-import-to=llvmlite',
    '--nofollow-import-to=albumentations',
    '--nofollow-import-to=albucore',
    '--nofollow-import-to=pydantic',
    '--nofollow-import-to=pydantic_core',
    '--nofollow-import-to=typing_inspection',
    '--nofollow-import-to=iPhoto.tests',
    '--nofollow-import-to=pytest',
    '--include-package=iPhoto',
    '--include-package=maps',
    '--include-package=OpenGL',
    '--include-package=OpenGL_accelerate',
    '--include-package=cv2',
    '--include-package=reverse_geocoder',
    '--include-package=insightface',
    '--include-package=onnxruntime',
    # OCR models live inside the RapidOCR wheel; without this include they are dropped
    # and text search silently stops working in the packaged build.
    '--include-package=rapidocr_onnxruntime',
    "--output-dir=$OutputDir",
    "--include-data-dir=$clipModelDir=extension/models/clip-vit-base-patch32",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\schemas')=iPhoto/schemas",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\gui\ui\icon')=iPhoto/gui/ui/icon",
    "--include-data-dir=$(Join-Path $srcRoot 'iPhoto\gui\ui\qml')=iPhoto/gui/ui/qml",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\gl_image_viewer.frag')=iPhoto/gui/ui/widgets/gl_image_viewer.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\gl_image_viewer.vert')=iPhoto/gui/ui/widgets/gl_image_viewer.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.frag')=iPhoto/gui/ui/widgets/image_viewer_rhi.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.frag.qsb')=iPhoto/gui/ui/widgets/image_viewer_rhi.frag.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.vert')=iPhoto/gui/ui/widgets/image_viewer_rhi.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_rhi.vert.qsb')=iPhoto/gui/ui/widgets/image_viewer_rhi.vert.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.frag')=iPhoto/gui/ui/widgets/image_viewer_overlay.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.frag.qsb')=iPhoto/gui/ui/widgets/image_viewer_overlay.frag.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.vert')=iPhoto/gui/ui/widgets/image_viewer_overlay.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\image_viewer_overlay.vert.qsb')=iPhoto/gui/ui/widgets/image_viewer_overlay.vert.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.frag')=iPhoto/gui/ui/widgets/video_renderer.frag",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.frag.qsb')=iPhoto/gui/ui/widgets/video_renderer.frag.qsb",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.vert')=iPhoto/gui/ui/widgets/video_renderer.vert",
    "--include-data-file=$(Join-Path $srcRoot 'iPhoto\gui\ui\widgets\video_renderer.vert.qsb')=iPhoto/gui/ui/widgets/video_renderer.vert.qsb",
    "--include-data-dir=$(Join-Path $srcRoot 'maps\tiles')=maps/tiles",
    "--include-data-file=$(Join-Path $srcRoot 'maps\style.json')=maps/style.json",
    "--include-data-dir=$(Join-Path $srcRoot 'maps\map_widget\qml')=maps/map_widget/qml"
)

# Bundle each extracted face pack (not the archives) so a release build works
# offline and FaceAnalysis finds ``<install>/extension/models/<pack>``.
foreach ($facePack in $faceModelPacks) {
    $hasDetector = Get-ChildItem -LiteralPath $facePack.FullName -Filter 'det_*.onnx' -File -ErrorAction SilentlyContinue
    $hasRecogniser = Get-ChildItem -LiteralPath $facePack.FullName -Filter 'w600k*.onnx' -File -ErrorAction SilentlyContinue
    if (-not $hasDetector -or -not $hasRecogniser) {
        Write-Warning "Skipping incomplete face pack $($facePack.FullName): det_*.onnx and w600k*.onnx are both required."
        continue
    }
    $arguments += "--include-data-dir=$($facePack.FullName)=extension/models/$($facePack.Name)"
}

if ($IconPath) {
    Assert-Exists $IconPath
    $arguments += "--windows-icon-from-ico=$IconPath"
}

$arguments += $mainScript

& $PythonExe @arguments
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE"
}
