"""
main.py — Photo-finish timing system, Step 1 entry point.

Architecture
------------
┌─────────────────────────────────────────────────────────────┐
│                         main loop                           │
│                                                             │
│  CameraManager ──frame──► LineScanBuilder ──column──►       │
│                               │                             │
│                               └──column──► MotionDetector   │
│                                               │             │
│                                  elapsed_s ◄──┘             │
│                                       │                     │
│                                  callback                   │
│                                       ▼                     │
│                             TimingRecorder.record_crossing  │
└─────────────────────────────────────────────────────────────┘

Future additions (later steps)
-------------------------------
  Step 2 — LoRa / ESP32 start signal  →  calls recorder.start_race()
  Step 3 — UDP transmitter            →  reads recorder.session.crossings
  Step 4 — Flask / FastAPI web UI     →  reads recorder + builder.composite

Run
---
    python main.py [--simulate] [--autostart] [--preview]

Options
    --simulate    Force simulation mode (no camera required)
    --autostart   Start the race clock immediately (overrides config)
    --preview     Open an OpenCV preview window
    --save-on-exit  Save composite PNG when you press Ctrl-C
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

import config
from camera_manager  import CameraManager
from line_scan_builder import LineScanBuilder
from motion_detector  import MotionDetector
from timing_recorder  import TimingRecorder


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s  %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# CLI arguments
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Photo-finish line-scan timing system — Step 1"
    )
    p.add_argument(
        "--simulate",
        action="store_true",
        help="Run in simulation mode (no camera required)",
    )
    p.add_argument(
        "--autostart",
        action="store_true",
        help="Start the race clock immediately on launch",
    )
    p.add_argument(
        "--preview",
        action="store_true",
        help="Show a live OpenCV preview window",
    )
    p.add_argument(
        "--save-on-exit",
        action="store_true",
        dest="save_on_exit",
        help="Save the composite PNG when the program exits",
    )
    p.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable DEBUG-level logging",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Shutdown handler
# ---------------------------------------------------------------------------

class _GracefulShutdown:
    """Catches SIGINT / SIGTERM and sets a flag the main loop checks."""

    def __init__(self) -> None:
        self.requested = False
        signal.signal(signal.SIGINT,  self._handler)
        signal.signal(signal.SIGTERM, self._handler)

    def _handler(self, *_) -> None:
        print("\n[shutdown requested]")
        self.requested = True


# ---------------------------------------------------------------------------
# Performance statistics
# ---------------------------------------------------------------------------

class _PerfStats:
    """Tracks realtime frame-rate and logs it periodically."""

    def __init__(self, report_interval_s: float = 5.0) -> None:
        self._interval  = report_interval_s
        self._last_time = time.monotonic()
        self._count     = 0
        self.log = logging.getLogger("perf")

    def tick(self) -> None:
        self._count += 1
        now = time.monotonic()
        if now - self._last_time >= self._interval:
            fps = self._count / (now - self._last_time)
            self.log.info("Capture rate: %.1f fps  (%d frames)", fps, self._count)
            self._count    = 0
            self._last_time = now


# ---------------------------------------------------------------------------
# Main capture loop
# ---------------------------------------------------------------------------

def run_capture_loop(
    args: argparse.Namespace,
    camera:   CameraManager,
    builder:  LineScanBuilder,
    detector: MotionDetector,
    recorder: TimingRecorder,
    shutdown: _GracefulShutdown,
) -> None:
    """
    The hot path: called for every frame captured by the camera.

    Intentionally kept as a plain function (not a class) so the data flow
    is easy to read and profile.
    """
    log = logging.getLogger("loop")
    stats = _PerfStats()

    # Preview is only updated every N frames to reduce OpenCV overhead.
    PREVIEW_EVERY_N = 15

    log.info("Entering capture loop. Press Ctrl-C to stop.")

    for frame in camera.frames():
        if shutdown.requested:
            break

        # ----------------------------------------------------------------
        # 1. Extract the scan column and append to the composite.
        # ----------------------------------------------------------------
        column = builder.append_frame(frame)  # returns the (H, 3) column

        # ----------------------------------------------------------------
        # 2. Tell the recorder which composite column we're on so it can
        #    embed the index in crossing records (useful for the web UI to
        #    place markers on the image).
        # ----------------------------------------------------------------
        recorder.set_composite_column(builder.total_columns - 1)

        # ----------------------------------------------------------------
        # 3. Motion detection.
        #
        #    We pass elapsed_seconds into the detector; the detector then
        #    passes it straight to the callback if a crossing fires.  This
        #    keeps the timing module fully decoupled from wall-clock calls.
        # ----------------------------------------------------------------
        elapsed = recorder.elapsed_seconds
        detector.process_column(column, elapsed)

        # ----------------------------------------------------------------
        # 4. Performance tracking.
        # ----------------------------------------------------------------
        stats.tick()

        # ----------------------------------------------------------------
        # 5. Optional live preview.
        # ----------------------------------------------------------------
        if args.preview and (builder.total_columns % PREVIEW_EVERY_N == 0):
            builder.preview_window()

    log.info("Capture loop ended.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    _configure_logging(args.verbose)
    log = logging.getLogger("main")

    # Apply CLI overrides.
    if args.autostart:
        config.AUTO_START_CLOCK = True

    # Ensure output directory exists.
    Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    # ----------------------------------------------------------------
    # Build the pipeline components.
    # ----------------------------------------------------------------

    # Timing recorder — owns the race clock.
    recorder = TimingRecorder()

    # Motion detector — fires callbacks when an athlete crosses.
    detector = MotionDetector()
    detector.register_callback(recorder.record_crossing)

    # Line-scan builder — accumulates the composite image.
    builder = LineScanBuilder()

    # Shutdown sentinel.
    shutdown = _GracefulShutdown()

    log.info(
        "Photo-finish system ready.\n"
        "  Scan column : %d\n"
        "  Max fps     : %d\n"
        "  Auto-start  : %s\n"
        "  Simulate    : %s",
        config.SCAN_COLUMN,
        config.CAMERA_FPS,
        config.AUTO_START_CLOCK,
        args.simulate,
    )

    # ----------------------------------------------------------------
    # LoRa start-signal hook (Step 2 placeholder).
    # ----------------------------------------------------------------
    # In Step 2 a LoRa listener thread will call recorder.start_race()
    # when the RF start signal is received.  For now we either auto-start
    # or wait for the user to press Enter.
    # ----------------------------------------------------------------
    if not config.AUTO_START_CLOCK:
        print("\nPress Enter to start the race clock (Step 2: LoRa signal)…", end="", flush=True)
        try:
            input()
        except EOFError:
            pass  # Non-interactive mode — proceed without waiting.
        recorder.start_race()

    # ----------------------------------------------------------------
    # Camera context manager: start → loop → stop.
    # ----------------------------------------------------------------
    try:
        with CameraManager() as camera:
            run_capture_loop(
                args, camera, builder, detector, recorder, shutdown
            )
    except KeyboardInterrupt:
        pass
    finally:
        # ----------------------------------------------------------------
        # Shutdown: print summary, optionally save outputs.
        # ----------------------------------------------------------------
        log.info("Shutting down …")
        print("\n" + recorder.summary())

        if args.save_on_exit or recorder.session.crossings:
            log.info("Saving composite image …")
            path = builder.save()
            log.info("Saving session log …")
            recorder.save_log()
            print(f"\nComposite saved to: {path}")

        # ----------------------------------------------------------------
        # UDP transmitter hook (Step 3 placeholder).
        # ----------------------------------------------------------------
        # from udp_transmitter import UDPTransmitter
        # tx = UDPTransmitter()
        # for crossing in recorder.session.crossings:
        #     tx.send(crossing.elapsed_str)

        # ----------------------------------------------------------------
        # Web UI hook (Step 4 placeholder).
        # ----------------------------------------------------------------
        # from web_ui import create_app
        # app = create_app(recorder, builder)
        # app.run(host=config.WEB_HOST, port=config.WEB_PORT)


if __name__ == "__main__":
    main()
