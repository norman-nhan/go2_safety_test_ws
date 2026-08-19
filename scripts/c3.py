"""Move briefly after detecting a STOP palm."""

import _bootstrap  # noqa: F401
from src.gesture_api import run_gesture_trigger


if __name__ == "__main__":
    run_gesture_trigger("STOP")
