import asyncio

from unitree_webrtc_connect import SPORT_CMD_MCF

import _bootstrap  # noqa: F401
from src.common_api import (
    move,
    require_success,
    run_motion_program,
    sport_request,
    stop_robot,
)

FORWARD_SPEED_MPS = 1.0
FORWARD_SECONDS = 1.25


async def routine(connection):
    print("K2: moving forward...")
    await move(
        connection, x=FORWARD_SPEED_MPS, y=0.0, yaw=0.0,
        seconds=FORWARD_SECONDS,
    )
    await stop_robot(connection)
    await asyncio.sleep(1)

    print("K2: sitting down...")
    response = await sport_request(connection, SPORT_CMD_MCF["Sit"])
    require_success(response, "Sit")
    await asyncio.sleep(3)

    # Sit and RiseSit are separate Unitree actions. Explicitly rise before the
    # program disconnects instead of leaving the robot in its seated mode.
    print("K2: rising from sit...")
    response = await sport_request(connection, SPORT_CMD_MCF["RiseSit"])
    require_success(response, "RiseSit")
    await asyncio.sleep(3)
    print("K2 complete")


if __name__ == "__main__":
    run_motion_program(routine)
