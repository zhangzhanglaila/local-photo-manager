# AGENT.md - iPhotron Development Principles

This file is the working guide for coding agents and contributors. It reflects
the current vNext state: the production runtime has converged on
`RuntimeContext -> LibrarySession -> application ports/services`, and legacy
compatibility code is quarantined under `src/iPhoto/legacy/`.

## 1. Current Architecture Status

- The vNext cleanup is complete for production source code.
- Production runtime code must not import `iPhoto.legacy` or `iPhoto.models.*`.
- Legacy compatibility and old domain-repository code live only in
  `src/iPhoto/legacy/`. That subtree is temporary quarantine for historical
  behavior tests and is planned for removal in the next major release.
- GUI, CLI, file watchers, Qt workers, and future automation entry points must
  enter library behavior through `RuntimeContext`, `LibrarySession`, and
  application-level surfaces.
- New business logic belongs in application use cases/services, session
  surfaces, domain values/pure services, or infrastructure adapters. GUI code
  is presentation and Qt transport only.
- Both architecture violations that used to be outstanding are fixed, and
  `tools/check_architecture.py` exits 0. Treat a new violation as a bug in the
  change that introduced it, not as an accepted baseline.
- The test suite is green (`1473 passed, 34 skipped, 0 failed`); see §9 for how
  to run it and for the rules that keep it that way.

The authoritative refactor status is tracked in:

- `docs/refactor/04-implementation-checklist.md`
- `docs/refactor/05-current-progress.md`

## 2. Product Invariants

- **Folder-native library.** A folder is an album. Users can browse folders
  without an import step.
- **Local-first.** Core library, browsing, editing, Live Photo, People, and Maps
  behavior is local. Optional runtimes must degrade gracefully when unavailable.
- **Non-destructive editing.** Visual edits are stored in `.ipo` sidecars.
  Original media is not overwritten by normal editing.
- **Explicit metadata write-back only.** Assign Location is the explicit
  exception: it persists the location locally first, then best-effort writes GPS
  metadata to the original file through ExifTool and reports warnings on
  failure.
- **Rebuildable facts vs durable choices.** Scan facts, thumbnails, Live Photo
  materialization, and People runtime snapshots can be rebuilt. Favorites,
  hidden/trash state, pinned items, album order, manual metadata, People names,
  covers, groups, group order, hidden flags, and manual faces must survive
  rescans and rebuilds.
- **Cross-platform desktop first.** macOS, Windows, and Linux remain supported.
  Platform-specific rendering, maps, ExifTool, FFmpeg, and AI behavior must be
  isolated behind adapters or runtime discovery.

## 3. Runtime And Layering Rules

The production dependency direction is:

```text
gui -> bootstrap/runtime -> application -> domain
infrastructure -> application ports / domain values
bounded contexts -> application ports / domain values
```

Forbidden directions:

```text
domain -> application/gui/infrastructure
application -> gui/concrete cache/concrete infrastructure
infrastructure/cache/core/io/library/people -> gui
gui -> iPhoto.cache.index_store        (ask bootstrap/ for a helper instead)
production runtime -> iPhoto.legacy
production runtime -> iPhoto.models.*
```

`tools/check_architecture.py` enforces the first five; it currently exits 0. When
GUI needs concrete storage that no session surface exposes, add a small helper in
`bootstrap/` (see `bootstrap/embedding_index.py`) instead of importing the store.

Key runtime objects:

- `RuntimeContext`: process composition root, current settings/theme/recent
  libraries, active `LibrarySession` lifecycle.
- `LibrarySession`: library-scoped adapters and surfaces for assets, state,
  scanning, album metadata, People, Maps, thumbnails, edit sidecars, location,
  asset lifecycle, and file operations.
- `LibraryRuntimeController`: GUI/runtime controller bound to the active
  session; it should not re-create standalone compatibility services.

Compatibility code is not a production extension point. Do not add new features
to `src/iPhoto/legacy/app.py`, `src/iPhoto/legacy/appctx.py`,
`src/iPhoto/legacy/bootstrap/*`, or other quarantine modules.

## 4. Files And State

Album markers:

- `.iphoto.album.json`: folder-local album manifest. **Only used when no custom
  workspace is configured** (self-contained album layout).
- `.iPhoto/manifest.json`: manifest inside the album/library workspace. This is
  where manifests are written when `workspace_base` is configured.
- `.iphoto.album`: legacy minimal marker, still honoured for discovery but no
  longer created by `ensure_manifest()`.

Manifest location rules:

- `utils.pathutils.album_manifest_path(root)` is the single place that decides
  where a manifest is written: `<workspace>/<library>/.iPhoto/manifest.json` when
  a workspace base is configured, otherwise `<root>/.iphoto.album.json`.
- `utils.pathutils.find_album_manifest(root)` reads it and **migrates** a legacy
  in-library manifest into the workspace once (id and content preserved).
- Do not build `root / ".iphoto.album.json"` by hand anywhere else: that is how
  the manifest ended up inside the photo folder before.

Library workspace:

```text
/<WorkspaceBase>/<LibraryName>/.iPhoto/     (or /<LibraryRoot>/.iPhoto/ without one)
  global_index.db       # SQLite index and current asset/state repository store
  manifest.json         # Album manifest (workspace mode)
  links.json            # Live Photo compatibility materialization
  ocr_index.db          # Rebuildable OCR index
  thumbs/               # Rebuildable thumbnail cache (JPEG, quality 85)
  cache/shaders/        # Qt shader cache; resolved from settings at startup
  faces/
    face_index.db       # Rebuildable People runtime snapshot
    face_state.db       # Durable People user decisions
    thumbnails/         # Rebuildable cropped face thumbnails
  manifest.bak/         # Manifest/links backup area
  locks/                # File-level locks for JSON sidecars
```

The photo folder itself must contain **only** photos and videos. The shader
cache resolution runs before `RuntimeContext` exists, so it reads
`workspace_base` from `settings.json` directly (`bootstrap/qt_shader_cache.py`);
otherwise it falls back to `<library>/.iPhoto` and writes into the photo folder.

State rules:

- `global_index.db` is the current source of truth for asset scan rows,
  pagination, Live Photo roles, trash/favorite/hidden flags, face scan status,
  and the repository-backed user-state boundary.
- `links.json` is derived compatibility materialization for Live Photo payloads;
  target runtime behavior should read roles through repository/session surfaces.
- `cache/thumbs/` and People thumbnails are disposable.
- `faces/face_index.db` is rebuildable; `faces/face_state.db` is durable.
- `.ipo` sidecars are the durable source of non-destructive edit parameters.
- Scan merge must be idempotent and must not implicitly clear durable user
  state.

Trash identity rules:

- Trashed assets are indexed under the canonical `rel` prefix `.Trash/`
  (`parent_album_path == ".Trash"`). Queries, the All Photos exclusion, and
  restore all key off that identity, so it must not change.
- The physical trash directory is `ensure_work_dir(library_root) / ".Trash"`,
  which is inside `.iPhoto/` and can therefore sit **outside** the library root
  when a custom workspace base is configured. Never derive a trashed asset's
  path as `library_root / rel`.
- Resolve asset paths with `utils.pathutils.resolve_asset_path()`; it handles
  absolute `rel` values and the relocated trash directory.

## 5. Module Responsibilities

- `bootstrap/`: `RuntimeContext`, `LibrarySession`, and session-bound services
  that wire application behavior to the current library root. It is also the home
  for narrow helpers that own a concrete infrastructure dependency on behalf of
  presentation code — `qt_shader_cache.py` (settings-driven cache location) and
  `embedding_index.py` (stored CLIP embeddings for the AI search worker).
- `application/ports/`: public application boundary protocols, including
  `AssetRepositoryPort`, `LibraryStateRepositoryPort`, `MediaScannerPort`,
  `PeopleIndexPort`, `MapRuntimePort`, `EditSidecarPort`,
  `LocationAssetServicePort`, and `MapInteractionServicePort`.
- `application/use_cases/`: owning use cases for workflows such as scanning.
- `application/services/`: application-level services for album manifests,
  pinned state, location queries, map interaction, and explicit location
  assignment.
- `domain/`: dataclasses, value objects, query models, and pure domain services.
  Domain code must not perform IO or import Qt/SQLite/runtime adapters. Value
  objects consumed by application services belong here even when the GUI renders
  them (for example `domain/models/trail.py`).
- `infrastructure/`: concrete adapters for SQLite-backed state, manifests,
  `.ipo` sidecars, ExifTool, FFmpeg, maps runtime discovery, thumbnail caches,
  filesystem scanning, and runtime services.
- `cache/index_store/`: current SQLite global index implementation used behind
  repository/session surfaces. GUI and application code must not bypass the
  session boundary to call it directly.
- `gui/`: PySide6 views, widgets, controllers, viewmodels, coordinators, menus,
  and Qt task/signal adapters. It owns presentation state, not durable workflow
  rules.
- `library/`: runtime controller, tree/watch/scan coordination, trash and album
  filesystem shell code bound to session services.
- `people/`: optional People runtime, scan coordination, repositories, manual
  faces, stable People state, groups, covers, hidden flags, and service API.
- `maps/`: optional offline Maps runtime, tile parsing, OBF/native widget/helper
  integration, search, and map rendering internals.
- `core/`: pure or rendering-oriented algorithms for Live Photo pairing,
  adjustment math, geometry, export transforms, filters, raw loading, and
  preview backends.
- `io/`: metadata extraction, scanner adapters, and sidecar parsing helpers.
- `utils/`: cross-cutting helpers with no workflow knowledge. `timeutils.py`
  converts datetimes to epoch values without relying on platform `mktime` (which
  raises `OSError` on Windows around the epoch), and `pathutils.py` owns the
  work-directory, trash and album-manifest path rules.
- `ai/`: OCR engine/worker and the shared ONNX thread policy
  (`ai/onnx_threads.py`: `intra_op_num_threads <= 4`, overridable via
  `IPHOTO_FACE_THREADS`). Background AI scans must never default to "one thread
  per core".
- `legacy/`: quarantine only. No production imports and no new functionality.

## 6. Coding Rules

- Prefer existing session/application patterns over adding new facades.
- Use application ports before introducing cross-layer behavior.
- Keep GUI workers thin: they adapt Qt threading/progress and call session or
  application services.
- Use `Path` and shared path normalizers for filesystem paths. Never string-build
  paths.
- Use schema validation for album/link JSON payloads where a schema exists.
- Use atomic writes for manifest, links, settings, sidecars, and user state
  files.
- Use SQLite transactions for multi-row writes and scan merges.
- Use ExifTool/FFmpeg wrappers from `utils/`; never shell-concatenate user
  paths.
- Return warnings for recoverable external-tool failures without corrupting
  local state.
- Keep comments focused on non-obvious intent, boundaries, or failure modes.

## 7. Bounded Context Rules

### People

- InsightFace/ONNXRuntime are optional. Missing AI runtime must not break
  browsing, editing, Live Photo, Maps, or library state.
- Scan commits may rebuild `face_index.db`, but must preserve and repair
  `face_state.db`.
- Names, covers, hidden flags, person order, groups, group order, pinned state,
  group covers, manual faces, and group caches are durable user state.
- Do not merge people with incompatible hidden state.
- UI mutations must route through the session-bound People service or explicit
  test doubles.

### Maps

- Maps are optional. Missing native OBF/helper/widget runtime must show graceful
  fallback.
- Runtime availability belongs behind `MapRuntimePort`.
- Location asset aggregation and marker-click semantics belong behind session
  location/map interaction surfaces.
- Qt overlay painting, pointer hit testing, drag cursors, and widget event
  filters remain GUI transport details.

### Thumbnails

- Thumbnail generation and cache lookup must not block the UI thread.
- Memory/disk cache hits must avoid re-running generators.
- Thumbnail rendering may apply `.ipo` edit state, but durable edit persistence
  belongs behind edit sidecar/session services.

### Edit

- All normal edits are non-destructive and stored in `.ipo` sidecars.
- Editing math belongs in `core/`; persistence belongs behind `EditSidecarPort`
  or session edit services.
- QRhi/Metal/OpenGL backend choice must not leak into product workflow rules.

## 8. Rendering And Maps Platform Rules

- macOS media preview should default to QRhi/Metal when available; OpenGL is a
  diagnostic or compatibility fallback.
- Windows/Linux may use QRhi/OpenGL-backed paths depending on runtime support.
- Legacy OpenGL maps use the `QOpenGLWindow + createWindowContainer()` surface
  where required to avoid transparent-window composition issues.
- Native OsmAnd widget/helper selection belongs to maps runtime adapters and
  widget factories.
- Packaged builds must include required QSB shaders and maps extension runtime
  assets when those features are enabled.

## 9. Testing And Verification

Run architecture checks after boundary changes:

```powershell
.venv\Scripts\python.exe tools\check_architecture.py
.venv\Scripts\python.exe -m pytest tests/architecture -q
```

Run the whole suite before claiming a change is done:

```powershell
.venv\Scripts\python.exe -m pytest tests -q
```

Current baseline (keep it green; do not "fix" a red suite by weakening
assertions):

```text
1473 passed, 34 skipped, 0 failed   (~73 s, three consecutive identical runs)
```

Use targeted regression tests for changed behavior:

```powershell
.venv\Scripts\python.exe -m pytest tests/application/test_runtime_context.py tests/application/test_library_session.py -q
.venv\Scripts\python.exe -m pytest tests/test_gallery_collection_scan_refresh.py tests/test_asset_grid_visible_range.py tests/test_asset_grid_wheel.py -q
.venv\Scripts\python.exe -m pytest tests/performance -q
```

Required guardrail expectations:

- `application/` has no GUI or concrete persistence imports.
- `infrastructure/` has no GUI imports.
- `gui/` has no `iPhoto.cache.index_store` imports.
- production source has no `iPhoto.legacy` or `iPhoto.models.*` imports.
- GUI runtime has no compatibility service factory fallback.
- Architecture checks are **not** yet wired into CI: `.github/` contains issue
  templates only. Run them locally until a workflow exists.

Test-writing rules that this suite depends on:

- Platform-specific behaviour declares its own precondition (`pytest.skip` for
  macOS packaging, Windows-only `QWindow` masks, and similar) instead of failing
  on another OS.
- Tests must not depend on machine configuration such as `IPHOTO_EXIFTOOL_PATH`;
  point the code at a stub inside the test.
- A `QThread` that is destroyed while still running aborts the process and the
  crash surfaces in an unrelated test. `tests/conftest.py` tracks every started
  thread and stops it politely after each test — never call `terminate()`.
- Assert user-visible text through `tr(...)`, never as a literal English string.

`tests/gui` is excluded from the default run, and part of it is stale (for
example `tests/gui/viewmodels/test_gallery_collection_store.py` still assumes
synchronous window loading). New coverage for the store belongs in
`tests/test_gallery_collection_scan_refresh.py`, which does run by default.

Pytest configuration lives in a single place: `[tool.pytest.ini_options]` in
`pyproject.toml`. Do not reintroduce a `pytest.ini`, a `[pytest]` section in
`tox.ini`, or a `[tool:pytest]` block in `setup.cfg`. A second config file
silently takes precedence over `pyproject.toml` and drops `testpaths`, the
`tests/ui`/`tests/gui` ignores, and `NUMBA_DISABLE_JIT`, which makes a bare
`pytest` run collect GUI tests and the root-level manual scripts.

## 10. Release And Documentation Rules

- Keep `README.md` product-facing and concise.
- Keep `docs/architecture.md` as the current architecture entry point.
- Keep `docs/refactor/*` as the detailed vNext migration record and verification
  log.
- Do not treat archived refactor documents under `docs/finished/` as current
  implementation instructions.
- Release validation may include manual Qt GUI smoke testing and opening an
  existing library, but these are product acceptance checks rather than
  architecture guardrail replacements.

## 11. Repository Layout And Commit Discipline

### Commit after every completed feature

Each completed, self-contained piece of work gets its own commit. Do not batch
several unrelated features into one commit, and do not leave finished work
uncommitted at the end of a turn.

- Use Conventional Commits (`refactor:`, `fix:`, `chore:`, `docs:`, `test:`).
- Write the commit message in Chinese (简体). The Conventional Commit prefix and
  any code identifiers stay in English; the subject and body are Chinese. The
  maintainer reads Chinese, and this keeps the history consistent.
- The message body must state the concrete problem, not restate the diff. Name
  the file, path, or API that was wrong and what it is now.
- Commit only after the change is verified. State in the body how it was
  verified (which test file, which script, which manual check).
- Keep formatting-only and behavior-changing work in separate commits.
- Do not use `git add -A` blindly; stage the paths that belong to the feature.
  A stray untracked file (a generated SVG, a library marker, a log) must not
  ride along.

### Top-level layout

```text
.github/          CI, issue templates
docs/             architecture.md is the entry point; refactor/ is the vNext log
src/iPhoto/       production package (application, domain, infrastructure, gui)
src/maps/         optional OsmAnd offline map runtime
tests/            pytest suite; this is the only directory pytest collects
tools/            check_*.py architecture guards, manual/ for by-hand scripts
scripts/          packaging, model download, sync helpers
demo/             standalone design prototypes; not imported by src/
extension/        downloaded CLIP model cache (ignored)
downloads/        downloaded release archives (ignored)
```

### Paths that are contracts

Do not move or rename these. They are resolved by code, by tests, or by
packaging, and a tidy-up that moves them silently breaks a feature:

| Path | Why it is fixed |
| --- | --- |
| `src/maps/tiles/extension/` | `MapSourceSpec.osmand_default()` and the Windows Nuitka build expect this exact root |
| `src/extension/models/` | default `IPHOTO_FACE_MODEL_DIR` |
| `extension/models/clip-vit-base-patch32/` | `main_coordinator` and `scripts/download_clip_model.py` |
| `demo/video/` | `tests/test_video_demo_thumbnails.py` puts it on `sys.path` |
| `src/iPhoto/` package tree | architecture guardrails and 1467 tests index modules by path |

Everything else is free to reorganize, provided references in code, tests and
docs are updated in the same commit.

This guide is authoritative for new production work. When it conflicts with old
examples, follow the vNext runtime/session boundary and update the stale
example as part of the change.
