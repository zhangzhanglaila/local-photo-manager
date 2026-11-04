# Demo harnesses

Standalone Qt experiments kept for design reference and for side-by-side
comparison with the production widgets. Nothing here is imported by
`src/iPhoto` at runtime.

**These are not tests.** Pytest is configured with `testpaths = ["tests"]` in
`pyproject.toml`, so nothing under `demo/` is collected. Run a demo directly:

```powershell
.venv\Scripts\python.exe demo\edit\rotate.py
```

## Layout

| Path | What it is |
| --- | --- |
| `edit/` | Single-feature editing prototypes: crop, perspective, rotate, sharpen, denoise, vignette, definition, levels, aspect-ratio menu |
| `ui/` | Dashboard and widget prototypes: album dashboard, people dashboard, drag button |
| `curve/` | Tone-curve editor prototype (`curve.py`, `spline.py`) |
| `white-balance/` | White-balance panel prototype |
| `color-selector/` | Colour-picker prototype |
| `video/` | Video thumbnail-strip prototype. **Imported by `tests/test_video_demo_thumbnails.py`**, so keep this package path stable |
| `face-cluster/` | Early face-clustering prototype |
| `video-demo.py` | Backward-compatible launcher wrapper for the `demo/video/` package |

## Rules

- `demo/` is a scratch area. New product behaviour belongs in `src/iPhoto`, not
  here; see `AGENT.md` for the layer each feature should land in.
- `demo/video/` is the only part with a test dependency. Moving or renaming it
  breaks `tests/test_video_demo_thumbnails.py`, which puts that directory on
  `sys.path`.
- `edit/check_indicator.svg` is generated next to `edit/aspect.py` on first run
  and is committed so the checked-radio styling is deterministic.
- `face-cluster/runtime/` and `face-cluster/state/` are ignored scratch output,
  not source (`git check-ignore` them before adding files).
