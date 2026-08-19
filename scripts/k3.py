import asyncio

import _bootstrap  # noqa: F401
from src.common_api import move, run_motion_program, stop_robot

ZIGZAG_FORWARD_MPS = 0.35
ZIGZAG_SIDE_MPS = 0.30
ZIGZAG_LEG_SECONDS = 1.0
ZIGZAG_LEGS = 4 # repititions of zigzag motion (2 legs per zigzag)


async def routine(connection):
    print("K3: moving forward in a zigzag while facing forward...")
    for leg in range(ZIGZAG_LEGS):
        side_speed = ZIGZAG_SIDE_MPS if leg % 2 == 0 else -ZIGZAG_SIDE_MPS
        await move(
            connection, x=ZIGZAG_FORWARD_MPS, y=side_speed, yaw=0.0,
            seconds=ZIGZAG_LEG_SECONDS,
        )
    await stop_robot(connection)
    await asyncio.sleep(1)
    print("K3 complete")


if __name__ == "__main__":
    run_motion_program(routine)
