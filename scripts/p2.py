import asyncio

import _bootstrap  # noqa: F401
from src.common_api import move, run_motion_program, stop_robot

FORWARD_SPEED_MPS = 1.1
FORWARD_SECONDS = 1.2


async def routine(connection):
    print("P2: moving forward approximately 1.2 m...")
    await move(
        connection, x=FORWARD_SPEED_MPS, y=0.0, yaw=0.0,
        seconds=FORWARD_SECONDS,
    )
    await stop_robot(connection)
    await asyncio.sleep(1)
    print("P2 complete")


if __name__ == "__main__":
    run_motion_program(routine)
