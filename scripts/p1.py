import asyncio

import _bootstrap  # noqa: F401
from src.common_api import move, run_motion_program, stop_robot

FORWARD_SPEED_MPS = 1.0
FORWARD_SECONDS = 1.9
RIGHT_SPEED_MPS = 1.0
RIGHT_SECONDS = 0.48

async def routine(connection):
    print("P1: moving diagonally forward...")
    await move(
        connection, x=FORWARD_SPEED_MPS, y=0.6, yaw=0.0,
        seconds=FORWARD_SECONDS,
    )
    await stop_robot(connection)
    await asyncio.sleep(1)

    print("P1: moving right...")
    await move(
        connection, x=0.0, y=-RIGHT_SPEED_MPS, yaw=0.0,
        seconds=RIGHT_SECONDS,
    )
    await stop_robot(connection)
    print("P1 complete")

if __name__ == "__main__":
    run_motion_program(routine)
