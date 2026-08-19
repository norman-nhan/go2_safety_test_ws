"""Perform a gentle wet-dog-style shake by oscillating torso roll."""

import math

import _bootstrap  # noqa: F401
from src.common_api import oscillate_body_pose, run_body_pose_program
from src.config import section

CONFIG = section("scenarios")["body_motion"]


async def routine(connection):
    print("Wet-dog-style shaking...")
    await oscillate_body_pose(
        connection,
        axis="roll",
        amplitude=math.radians(float(CONFIG["wet_shake_degrees"])),
        frequency_hz=float(CONFIG["frequency_hz"]),
        seconds=float(CONFIG["duration_seconds"]),
        update_hz=float(CONFIG["update_hz"]),
    )


if __name__ == "__main__":
    run_body_pose_program(routine)
