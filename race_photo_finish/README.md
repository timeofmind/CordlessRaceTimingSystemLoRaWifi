# Photo-Finish Timing System — Step 1

Line-scan photo-finish camera for a Raspberry Pi 5 + IMX296 Global Shutter Camera.

---

## Hardware

| Component | Notes |
|-----------|-------|
| Raspberry Pi 5 (8 GB) | Main compute |
| Raspberry Pi Global Shutter Camera (IMX296) | 1456×1088, up to 60 fps full-frame |
| LoRa V3 / ESP32 | Start signal (Step 2) |
| Outdoor LED matrix / ESP32 | Displays final time (Step 3) |

---

## Quick start

```bash
# 1. Clone / copy files to the Pi
cd ~/photo_finish

# 2. Install dependencies
pip install -r requirements.txt --break-system-packages

# 3. Aim the camera at the finish line, then run:
python main.py --autostart --preview --save-on-exit
```

Press **Ctrl-C** to stop the capture. The composite image and session JSON
are saved in `output/`.

---

## Command-line options

```
python main.py [options]

--simulate       Run without a camera (synthetic athlete crossing at ~3 s)
--autostart      Start the race clock immediately (no Enter required)
--preview        Open a live OpenCV window showing the growing composite
--save-on-exit   Always save the composite PNG on exit (even if no crossings)
-v / --verbose   Enable DEBUG-level logging
```

---

## Configuration

Edit **`config.py`** before running:

| Parameter | Default | Effect |
|-----------|---------|--------|
| `SCAN_COLUMN` | 728 (centre) | x-pixel of the finish line in the frame |
| `CAMERA_FPS` | 60 | Target capture rate |
| `CAMERA_ROI` | `None` | Crop for higher fps (see comments in config.py) |
| `BACKGROUND_WARMUP_FRAMES` | 90 | Frames used to build background model |
| `MOTION_PIXEL_FRACTION` | 0.10 | % of column that must change to fire |
| `MOTION_PIXEL_THRESHOLD` | 25 | Per-pixel change threshold (0–255) |
| `CROSSING_DEBOUNCE_S` | 0.5 | Min gap between events |
| `AUTO_START_CLOCK` | `False` | Skip the "press Enter" prompt |

---

## Increasing frame rate

The IMX296 can exceed 60 fps when you reduce its active area via `CAMERA_ROI`.
Set a narrow horizontal strip centred vertically on the finish line:

```python
# config.py
CAMERA_ROI = (0, 444, 1456, 200)   # centre 200-row strip  →  ~120 fps
CAMERA_ROI = (0, 494, 1456, 100)   # centre 100-row strip  →  ~200 fps
SENSOR_HEIGHT = 200                 # must match ROI height
```

---

## Module map

```
photo_finish/
├── config.py            All tuneable constants
├── camera_manager.py    picamera2 wrapper; yields BGR frames
├── line_scan_builder.py Extracts columns; builds composite image
├── motion_detector.py   Background subtraction; fires crossing callbacks
├── timing_recorder.py   Race clock; event log; JSON persistence
├── main.py              Entry point; wires modules together
└── output/              Saved composites and session logs (git-ignored)
```

---

## Roadmap

| Step | Module | Status |
|------|--------|--------|
| 1 | Camera + line-scan + motion detection | ✅ This step |
| 2 | LoRa / ESP32 start signal | `lora_listener.py` (placeholder in main.py) |
| 3 | UDP → LED display | `udp_transmitter.py` (placeholder in main.py) |
| 4 | Flask / FastAPI web UI | `web_ui.py` (placeholder in main.py) |

---

## How line-scan photo finish works

```
Time →
┌──────────────────────────────────────────┐
│ Frame 0  │ Frame 1  │ Frame 2  │ …       │
│  col 728 │  col 728 │  col 728 │         │
│  ↓       │  ↓       │  ↓       │         │
│ [pixel]  │ [pixel]  │ [pixel]  │         │   ← composite image
│ [pixel]  │ [pixel]  │ [pixel]  │         │
│  …       │  …       │  …       │         │
└──────────────────────────────────────────┘
          Horizontal axis = time
          Vertical  axis = finish-line height
```

An athlete moving perpendicular to the finish line appears as an undistorted
silhouette because their full height is captured in the single column at the
moment they cross. Objects stationary relative to the ground smear to the
left as time passes, making bib numbers and lane markers visible as
horizontal streaks — the same effect seen in traditional film photo-finish
cameras.
