"""Move forward, then stop after detecting a STOP palm."""

import _bootstrap  # noqa: F401
from src.config import section
from src.gesture_api import run_gesture_stop

C2_CONFIG = section("scenarios")["c2"]


if __name__ == "__main__":
    run_gesture_stop(
        "STOP",
        forward_speed=float(C2_CONFIG["forward_speed"]),
        stop_delay_seconds=float(C2_CONFIG["stop_delay_seconds"]),
        max_move_seconds=float(C2_CONFIG["max_move_seconds"]),
    )
