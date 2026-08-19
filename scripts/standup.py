"""Return the Go2 from StandDown to a balanced standing posture."""

import _bootstrap  # noqa: F401
from src.common_api import run_motion_program, send_action


async def routine(connection):
    print("Standing up...")
    await send_action(connection, "StandUp", settle_seconds=3.0)
    print("Enabling balance stand...")
    await send_action(connection, "BalanceStand", settle_seconds=2.0)


if __name__ == "__main__":
    run_motion_program(routine, prepare_balance=False)
