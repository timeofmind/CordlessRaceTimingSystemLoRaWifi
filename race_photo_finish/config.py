"""
config.py — Central configuration for the photo finish timing system.

All tuneable parameters live here. No magic numbers elsewhere in the codebase.
"""

# ---------------------------------------------------------------------------
# Camera
# ---------------------------------------------------------------------------

# Target capture frame rate. The IMX296 global shutter sensor tops out at
# ~60 fps full-frame (1456×1088). Raise to e.g. 200 if you set a smaller
# ScalerCrop / ROI further below.
CAMERA_FPS: int = 60

# Full sensor dimensions for the IMX296 (Raspberry Pi Global Shutter Camera).
SENSOR_WIDTH:  int = 1456
SENSOR_HEIGHT: int = 1088

# Optional region-of-interest crop (x, y, width, height) in sensor pixels.
# Reducing the active area lets the sensor run at higher frame rates.
# Set to None to use the full frame.
#   Example for a thin horizontal strip centred vertically:
#   ROI = (0, 394, 1456, 300)   → ~120 fps
#   ROI = (0, 494, 1456, 100)   → ~200+ fps
CAMERA_ROI: tuple[int, int, int, int] | None = None

# Pixel format fed to OpenCV. "BGR888" keeps things compatible with cv2
# without any colour-space conversion overhead.
CAMERA_FORMAT: str = "BGR888"

# ---------------------------------------------------------------------------
# Line-scan geometry
# ---------------------------------------------------------------------------

# Horizontal pixel column extracted from every frame (0 = left edge).
# Aim this at the physical finish line in the camera's field of view.
SCAN_COLUMN: int = 728  # centre of the 1456-px-wide sensor by default

# Maximum width (in frames/columns) of the composite image kept in RAM.
# At 60 fps this is 60 seconds of imagery. Increase for longer races.
MAX_COMPOSITE_WIDTH: int = 3600  # columns  (= 60 s × 60 fps)

# Output directory for saved composite images and event logs.
OUTPUT_DIR: str = "output"

# ---------------------------------------------------------------------------
# Motion / crossing detection
# ---------------------------------------------------------------------------

# Number of "quiet" frames used to build the initial background column.
BACKGROUND_WARMUP_FRAMES: int = 90  # 1.5 s at 60 fps

# Fraction of pixels in the column that must exceed the per-pixel threshold
# before a crossing is declared.
MOTION_PIXEL_FRACTION: float = 0.10  # 10 % of column height

# Per-pixel absolute difference (0–255) counted as "changed".
MOTION_PIXEL_THRESHOLD: int = 25

# Minimum gap between successive crossing events (seconds).
# Prevents one athlete triggering dozens of timestamps.
CROSSING_DEBOUNCE_S: float = 0.5

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

# When True the race clock starts immediately on launch (useful for testing
# without the LoRa start-signal module wired up).
AUTO_START_CLOCK: bool = False

# ---------------------------------------------------------------------------
# UDP output  (Step 3 — LED display)
# ---------------------------------------------------------------------------
UDP_TARGET_IP:   str = "192.168.1.50"
UDP_TARGET_PORT: int = 5005

# ---------------------------------------------------------------------------
# Web UI  (Step 4 — Flask / FastAPI)
# ---------------------------------------------------------------------------
WEB_HOST: str = "0.0.0.0"
WEB_PORT: int = 8080
