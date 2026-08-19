"""Put the Go2 into its supported StandDown posture."""

import _bootstrap  # noqa: F401
from src.common_api import run_motion_program, send_action, stop_robot


async def routine(connection):
    await stop_robot(connection)
    print("Crouching with StandDown...")
    await send_action(connection, "StandDown", settle_seconds=3.0)


if __name__ == "__main__":
    run_motion_program(routine, prepare_balance=False)
