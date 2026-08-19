"""Make a standing Go2 shake its fixed head left and right."""

import asyncio
import math
import os

from unitree_webrtc_connect import (
    RTC_TOPIC,
    SPORT_CMD_MCF,
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)


ROBOT_IP = os.getenv("GO2IP", "10.0.0.61")

# The Go2 head is fixed to its torso, so body yaw creates the head shake.
HEAD_YAW_RAD = math.radians(10.0)
SHAKE_HZ = 1.5
SHAKE_SECONDS = 4.0
UPDATE_HZ = 15.0


async def sport_request(connection, command, parameter=None):
    request = {"api_id": SPORT_CMD_MCF[command]}
    if parameter is not None:
        request["parameter"] = parameter

    return await connection.datachannel.pub_sub.publish_request_new(
        RTC_TOPIC["SPORT_MOD"], request
    )


def require_success(response, command):
    status = response.get("data", {}).get("header", {}).get("status", {})
    if status.get("code") != 0:
        raise RuntimeError(f"{command} failed: {status}")


async def set_body_pose(connection, *, roll=0.0, pitch=0.0, yaw=0.0):
    response = await sport_request(
        connection,
        "Euler",
        {"x": roll, "y": pitch, "z": yaw},
    )
    require_success(response, "Euler")


async def shake_head(connection):
    """Oscillate torso yaw to move the fixed head left and right."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    period = 1.0 / UPDATE_HZ

    while loop.time() - start < SHAKE_SECONDS:
        elapsed = loop.time() - start
        yaw = HEAD_YAW_RAD * math.sin(math.tau * SHAKE_HZ * elapsed)
        await set_body_pose(connection, yaw=yaw)
        await asyncio.sleep(period)

    await set_body_pose(connection)


async def main():
    connection = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=ROBOT_IP,
        aes_128_key=os.getenv("UNITREE_AES_128_KEY") or None,
    )
    connected = False

    try:
        print(f"Connecting to Go2 at {ROBOT_IP}...")
        await connection.connect()
        connected = True

        print("Enabling balance stand...")
        response = await sport_request(connection, "BalanceStand")
        require_success(response, "BalanceStand")
        await asyncio.sleep(2)

        print("Shaking head left and right...")
        await shake_head(connection)
        print("Head shake complete")
    finally:
        if connected:
            try:
                await set_body_pose(connection)
                await asyncio.sleep(0.5)
            finally:
                await connection.disconnect()


if __name__ == "__main__":
    confirmation = input(
        "Place the Go2 on a flat, dry, non-slip floor and keep the remote ready. "
        "Type SHAKE to continue: "
    )
    if confirmation != "SHAKE":
        raise SystemExit("Cancelled; no command was sent.")

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted")
