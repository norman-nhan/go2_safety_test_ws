"""Move briefly after detecting a waving gesture."""

import _bootstrap  # noqa: F401
from src.gesture_api import run_gesture_trigger


if __name__ == "__main__":
    run_gesture_trigger("WAVING", move_delay_seconds=7.0)
