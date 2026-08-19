import asyncio
import math

import _bootstrap  # noqa: F401
from src.common_api import move, run_motion_program, stop_robot

FORWARD_SPEED_MPS = 1.0
FORWARD_SECONDS = 2.1
CIRCLE_SPEED_MPS = 0.8
CIRCLE_YAW_RAD_S = -1.65
CIRCLE_SECONDS = math.tau / abs(CIRCLE_YAW_RAD_S)


async def routine(connection):
    print("K1: moving diagonally forward...")
    await move(
        connection, x=FORWARD_SPEED_MPS, y=0.39, yaw=0.0,
        seconds=FORWARD_SECONDS,
    )
    await stop_robot(connection)
    await asyncio.sleep(1)

    print("K1: moving around a circle...")
    await move(
        connection, x=CIRCLE_SPEED_MPS, y=0.0, yaw=CIRCLE_YAW_RAD_S,
        seconds=CIRCLE_SECONDS,
    )
    await stop_robot(connection)
    print("K1 complete")


if __name__ == "__main__":
    run_motion_program(routine)
