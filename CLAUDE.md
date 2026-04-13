# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Committing changes

Every change you make to this repo must end with `git add` and `git commit`. Stage the specific files you touched (not `git add -A`), write a concise message describing the change, and commit before reporting the task complete. This is standing authorization — do not ask first.

## System overview

A multi-device race timing system. Devices communicate over LoRa (long range, start signal) or Wi-Fi/UDP (shorter range, finish time to displays). The repo is split by device role; each role has its own `main.py` entry point.

- `race_photo_finish/` — Raspberry Pi 5 + IMX296 Global Shutter Camera. Builds a line-scan photo-finish composite and detects crossings. This is the only subsystem with working code today (Step 1).
- `race_start/` — Pi-side LoRa listener that will call `recorder.start_race()` when the start gun fires (Step 2, skeleton).
- `race_finish_display/` — Pi-side UDP transmitter that pushes finish times to an ESP32-driven LED matrix (Step 3, skeleton).
- `shared/` — cross-subsystem helpers (e.g. `timing_utils.format_mmss_mmm`).
- `firmware/` — optional ESP32 `.ino` / MicroPython sources for the LoRa start node and LED display.

The roadmap in `race_photo_finish/README.md` is the source of truth for which step is active. `race_photo_finish/main.py` contains explicit placeholder hooks (commented `# Step 2 / Step 3 / Step 4` blocks) showing where the other subsystems plug in — wire new integrations there rather than refactoring the pipeline.

## Photo-finish pipeline

The hot path in `race_photo_finish/main.py::run_capture_loop` is intentionally a plain function, not a class, so data flow stays profileable:

```
CameraManager.frames() → LineScanBuilder.append_frame() → column
                                                           ↓
                                    MotionDetector.process_column(column, elapsed)
                                                           ↓ (callback on crossing)
                                    TimingRecorder.record_crossing(elapsed)
```

Key decoupling rule: `MotionDetector` never reads the wall clock. `TimingRecorder` owns the race clock and passes `elapsed_seconds` into the detector each frame; the detector forwards it to the crossing callback. Preserve this when extending — do not add `time.monotonic()` calls inside the detector or builder.

`CameraManager` falls back to a synthetic simulation source when `picamera2` is unavailable or `--simulate` is set, so the pipeline runs on a dev laptop.

## Running

The photo-finish code uses sibling imports (`import config`, `from camera_manager import …`), so run it from inside its own directory:

```bash
cd race_photo_finish
pip install -r requirements.txt --break-system-packages   # on Pi OS Bookworm
python main.py --autostart --preview --save-on-exit
```

Common flags: `--simulate` (no camera), `--autostart` (skip Enter prompt), `--preview` (OpenCV window), `--save-on-exit`, `-v` (DEBUG logs). Ctrl-C writes the composite PNG and session JSON to `race_photo_finish/output/`.

All tunables live in `race_photo_finish/config.py` — `SCAN_COLUMN`, `CAMERA_FPS`, `CAMERA_ROI`, motion thresholds, debounce. To exceed 60 fps, narrow `CAMERA_ROI` to a horizontal strip centred on the finish line and update `SENSOR_HEIGHT` to match.

## Repo state

No packaging (`pyproject.toml` / `setup.py`), test suite, linter config, or CI. Each subsystem is run directly with `python main.py` from inside its own directory. The photo-finish code uses sibling imports rather than package imports; new subsystems can follow the same convention.
