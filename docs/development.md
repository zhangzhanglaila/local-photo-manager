# Development Guide

Development environment, architecture boundaries, tests, packaging, and commit
conventions for **iPhoto**.

---

## Prerequisites

| Requirement | Version |
|-------------|---------|
| Python | 3.12+ |
| ExifTool | Latest (in `PATH`, or set `IPHOTO_EXIFTOOL_PATH`) |
| FFmpeg | Latest (video thumbnails/export) |
| PySide6 | Installed by `pip install -e .` |

Linux RAW support also needs `libraw`:

```bash
sudo apt install libraw-dev       # Debian/Ubuntu
sudo dnf install LibRaw-devel     # Fedora
```

## Setup

```bash
git clone https://github.com/zhangzhanglaila/local-photo-manager.git
cd local-photo-manager

python -m venv .venv
.venv\Scripts\activate            # Windows
# source .venv/bin/activate       # macOS/Linux

pip install -e .
```

Optional AI dependencies:

```bash
pip install -e ".[ai]"            # OCR + ONNX Runtime
pip install -e ".[agent]"         # semantic search
pip install insightface onnxruntime  # face recognition
```

Run the app:

```bash
.venv\Scripts\python.exe -m iPhoto.gui.main
```

## Project Layout

```text
src/iPhoto/        production package
src/maps/          optional OsmAnd map runtime
tests/             pytest suite
tools/             architecture checks and manual scripts
scripts/           packaging/model-download/release helpers
packaging/         installer and icon assets
docs/              current architecture/development/security/build docs
```

Inside `src/iPhoto/`:

```text
gui/             Qt widgets, controllers, coordinators, view models
application/     use cases, ports, DTOs; no GUI imports
domain/          pure domain values and services
infrastructure/  SQLite, ExifTool, FFmpeg, AI adapters
bootstrap/       RuntimeContext, LibrarySession composition
library/         scan/session/album runtime
people/          face clustering and people state
ai/              OCR, face detection, model loading
agent/           search-model services
cache/           index and thumbnail storage
io/              file discovery and media scanning
```

## Architecture Boundaries

Production dependency direction:

```text
gui -> bootstrap/runtime -> application -> domain
infrastructure -> application ports / domain values
bounded contexts -> application ports / domain values
```

Rules:

- GUI is presentation and Qt transport only.
- New business logic belongs in application use cases/services or domain values.
- Production code must not import `iPhoto.legacy` or `iPhoto.models.*`.
- Do not recreate `src/iPhoto/legacy/`.
- `gui/` must not import `iPhoto.cache.index_store` directly; ask `bootstrap/`
  for a helper.

Read [AGENT.md](../AGENT.md) and [architecture.md](architecture.md) before
architecture-sensitive work.

## Tests

```bash
# Architecture guards
.venv\Scripts\python.exe tools\check_architecture.py
.venv\Scripts\python.exe -m pytest tests/architecture -q

# Full suite
.venv\Scripts\python.exe -m pytest tests -q

# Focused file
.venv\Scripts\python.exe -m pytest tests/test_background_tasks.py -q
```

Notes:

- `tests/gui` and `tests/ui` are excluded by default; run them explicitly when
  changing those areas.
- The native OsmAnd probe in `tests/test_photo_map_view.py` can abort the Qt
  process at teardown on some Windows machines. Exclude that file when checking
  the rest of the suite:

```bash
.venv\Scripts\python.exe -m pytest tests -q --ignore=tests/test_photo_map_view.py
```

- The suite is expected to stay green with the map file excluded; use the
  command above for the exact count on your machine.
- A test that starts a `QThread` must stop it before the test ends; leaked
  running threads surface as crashes in unrelated tests.

## AI Development

Model locations:

| Purpose | Default | Override |
|---------|---------|----------|
| Semantic search CLIP/SigLIP2 | `extension/models/` | `IPHOTO_CLIP_MODEL_DIR` |
| Face recognition | `extension/models/` | `IPHOTO_FACE_MODEL_DIR` |
| OCR (RapidOCR) | `extension/models/rapidocr/` | auto-download |
| Maps extension | `src/maps/tiles/extension/` | `IPHOTO_OSMAND_EXTENSION_ROOT` |

Face and OCR scans are serialized by `iPhoto.ai.scan_gate` and pause while the
user is viewing media. Inference threads are bounded by
`IPHOTO_FACE_THREADS` (default `min(4, CPU/2)`).

## Build And Package

- Windows executable: [misc/BUILD_EXE.md](misc/BUILD_EXE.md)
- Linux `.deb`: [misc/BUILD_DEB.md](misc/BUILD_DEB.md)
- Windows launcher: `run_iphoto.pyw` (used by `pythonw.exe`; keeps stdout/stderr
  redirected to `%LOCALAPPDATA%\iPhoto\launcher.*`)
- Icon/installer assets: `packaging/`

## Debugging

```powershell
# Console logs
.venv\Scripts\python.exe -m iPhoto.gui.main

# Diagnostics
$env:IPHOTO_DIAG = "1"
```

Log file: `%LOCALAPPDATA%\iPhoto\iPhoto.log`.

Qt debugging:

```bash
export QT_DEBUG_PLUGINS=1   # macOS/Linux
```

## Commit Conventions

- One self-contained feature/fix per commit.
- Conventional Commits prefix in English; Chinese subject/body.
- State what was wrong and what changed, not just a file list.
- State the verification command(s) in the body.
- Keep formatting-only and behavior-changing work separate.
- Do not use `git add -A`; stage intended paths only.

## Documentation Rules

- `README.md` is the product-facing overview and setup guide.
- `docs/architecture.md` is the current architecture entry point.
- Keep only current reference docs under `docs/`; completed migration records
  have been removed.
- Historical notable changes belong in [CHANGELOG.md](CHANGELOG.md).
