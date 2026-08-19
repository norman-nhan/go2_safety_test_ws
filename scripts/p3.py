import asyncio

import _bootstrap  # noqa: F401
from src.common_api import move, rotate_degrees, run_motion_program, stop_robot

BACKWARD_SPEED_MPS = 1.2
BACKWARD_SECONDS = 1.2


async def routine(connection):
    print("P3: turning 190 degrees...")
    await rotate_degrees(connection, 190, speed_rad_s=1.5)
    await asyncio.sleep(1)

    print("P3: moving backward approximately 1.2 m...")
    await move(
        connection, x=-BACKWARD_SPEED_MPS, y=0.0, yaw=0.0,
        seconds=BACKWARD_SECONDS,
    )
    await stop_robot(connection)
    print("P3 complete")


if __name__ == "__main__":
    run_motion_program(routine)
