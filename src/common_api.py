"""Shared Go2 connection and movement helpers for the scenario scripts.

Most scenario files only need to define an async ``routine(connection)`` and
pass it to ``run_motion_program``. The call chain is:

    run_motion_program(routine)       asks for the MOVE confirmation
        -> execute_motion(routine)    starts the asyncio event loop work
            -> connected_robot()      connects and enables BalanceStand
                -> routine(connection) runs the scenario's movement sequence

When the routine finishes, fails, or is interrupted, ``connected_robot`` sends
StopMove and disconnects. Scenario routines normally use ``move``,
``rotate_degrees``, ``stop_robot``, and (when necessary) ``sport_request``.
"""

import asyncio
import json
import math
import os
import random
import time
from contextlib import asynccontextmanager

from unitree_webrtc_connect import (
    RTC_TOPIC,
    SPORT_CMD_MCF,
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)

from src.config import section

ROBOT_IP = os.getenv("GO2IP", "10.0.0.61")
COMMAND_HZ = 10
CONNECTION_CONFIG = section("connection")


async def sport_request(connection, api_id, parameter=None):
    """Send one sport-mode command and wait for the robot's response.

    Use this for one-time commands such as BalanceStand, Sit, GetState, and
    StopMove. This function only transports the command; call
    ``require_success`` afterward to validate the returned status.
    """
    request = {"api_id": api_id}
    if parameter is not None:
        request["parameter"] = parameter

    return await connection.datachannel.pub_sub.publish_request_new(
        RTC_TOPIC["SPORT_MOD"], request
    )


def sport_request_no_reply(connection, api_id, parameter=None):
    """Send one sport-mode command without waiting for a response.

    Continuous velocity control must refresh Move commands several times per
    second. Waiting for a response to every refresh adds unnecessary latency,
    so ``move`` uses this lower-level no-reply form internally.
    """
    generated_id = int(time.time() * 1000) % 2147483648 + random.randint(0, 1000)
    request = {
        "header": {
            "identity": {"id": generated_id, "api_id": api_id},
            "policy": {"priority": 0, "noreply": True},
        },
        "parameter": json.dumps(parameter) if parameter is not None else "",
        "binary": [],
    }
    connection.datachannel.pub_sub.publish_without_callback(
        RTC_TOPIC["SPORT_MOD"], request
    )


def require_success(response, command):
    """Raise RuntimeError when a Unitree command response reports failure."""
    status = response.get("data", {}).get("header", {}).get("status", {})
    if status.get("code") != 0:
        raise RuntimeError(f"{command} failed: {status}")


async def stop_robot(connection):
    """Send StopMove, verify it succeeded, and allow the robot to settle."""
    response = await sport_request(connection, SPORT_CMD_MCF["StopMove"])
    require_success(response, "StopMove")
    await asyncio.sleep(0.5)


async def move(connection, *, x, y, yaw, seconds, hz=COMMAND_HZ):
    """Refresh a velocity command for a fixed open-loop duration.

    ``x`` is forward/backward speed in m/s, ``y`` is lateral speed in m/s,
    and ``yaw`` is angular speed in rad/s. Positive/negative directions use
    the Go2 body coordinate frame. ``seconds`` controls duration and ``hz``
    controls how often the command is refreshed.

    This helper does not send StopMove at the end, which lets a scenario join
    consecutive movement segments smoothly. Call ``stop_robot`` explicitly
    whenever the robot must become stationary.
    """
    period = 1.0 / hz
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        sport_request_no_reply(
            connection,
            SPORT_CMD_MCF["Move"],
            {"x": x, "y": y, "z": yaw},
        )
        await asyncio.sleep(period)


async def rotate_degrees(connection, degrees, speed_rad_s=0.5):
    """Rotate by an estimated angle using timed yaw, then stop.

    Positive and negative ``degrees`` rotate in opposite directions.
    This is open-loop: the angle is estimated from yaw speed multiplied by
    time and is not corrected using localization or odometry.
    """
    if speed_rad_s <= 0:
        raise ValueError("speed_rad_s must be positive")
    direction = 1.0 if degrees >= 0 else -1.0
    duration = math.radians(abs(degrees)) / speed_rad_s
    await move(
        connection,
        x=0.0,
        y=0.0,
        yaw=direction * speed_rad_s,
        seconds=duration,
    )
    await stop_robot(connection)


async def print_robot_state(connection):
    """Request and print a small set of useful MCF state fields."""
    keys = [
        "state",
        "bodyHeight",
        "speedLevel",
        "gait",
        "continuousGait",
        "economicGait",
    ]
    response = await sport_request(connection, SPORT_CMD_MCF["GetState"], keys)
    require_success(response, "GetState")
    raw_data = response.get("data", {}).get("data", "")
    if raw_data:
        print(f"MCF state: {json.loads(raw_data)}")


@asynccontextmanager
async def connected_robot(prepare_balance=True):
    """Provide a ready connection and guarantee stop/disconnect cleanup.

    This async context manager performs the shared lifecycle that used to be
    repeated in every scenario:

    1. Create and connect the WebRTC client.
    2. Optionally enable BalanceStand and check the response.
    3. Yield the connection to the scenario routine.
    4. Send StopMove and disconnect in ``finally``.

    The ``finally`` block also runs when a routine raises an exception.
    """
    connection = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=ROBOT_IP,
        aes_128_key=CONNECTION_CONFIG.get("aes_128_key") or None,
    )
    connected = False
    try:
        print(f"Connecting to Go2 at {ROBOT_IP}...")
        await connection.connect()
        connected = True
        if prepare_balance:
            print("Enabling MCF balance stand...")
            response = await sport_request(
                connection, SPORT_CMD_MCF["BalanceStand"]
            )
            require_success(response, "BalanceStand")
            await asyncio.sleep(2)
        yield connection
    finally:
        if connected:
            print("Stopping...")
            try:
                await stop_robot(connection)
            finally:
                await connection.disconnect()


async def execute_motion(routine, *, prepare_balance=True):
    """Run one scenario routine inside the managed robot connection."""
    async with connected_robot(prepare_balance=prepare_balance) as connection:
        await routine(connection)


def run_motion_program(routine, *, prepare_balance=True):
    """User-facing entry point used at the bottom of P/K scenario files.

    It requires the exact safety confirmation ``MOVE``, creates the asyncio
    event loop, delegates connection management to ``execute_motion``, and
    converts Ctrl+C into a clean interruption message.

    Example::

        async def routine(connection):
            await move(connection, x=0.2, y=0.0, yaw=0.0, seconds=1.0)
            await stop_robot(connection)

        if __name__ == "__main__":
            run_motion_program(routine)
    """
    confirmation = input(
        "Clear the area around the Go2 and keep the remote/e-stop ready. "
        "Type MOVE to continue: "
    )
    if confirmation != "MOVE":
        raise SystemExit("Cancelled; no command was sent.")
    try:
        asyncio.run(execute_motion(routine, prepare_balance=prepare_balance))
    except KeyboardInterrupt:
        print("\nInterrupted")


async def send_action(connection, command, *, settle_seconds=0.0):
    """Send one named MCF posture/action command and validate its response."""
    response = await sport_request(connection, SPORT_CMD_MCF[command])
    require_success(response, command)
    if settle_seconds > 0:
        await asyncio.sleep(settle_seconds)


async def set_body_pose(connection, *, roll=0.0, pitch=0.0, yaw=0.0):
    """Set torso Euler angles in radians while the robot is standing."""
    response = await sport_request(
        connection,
        SPORT_CMD_MCF["Euler"],
        {"x": roll, "y": pitch, "z": yaw},
    )
    require_success(response, "Euler")


async def oscillate_body_pose(
    connection, *, axis, amplitude, frequency_hz, seconds, update_hz
):
    """Oscillate one torso Euler axis sinusoidally, then restore neutral."""
    if axis not in {"roll", "pitch", "yaw"}:
        raise ValueError("axis must be roll, pitch, or yaw")
    loop = asyncio.get_running_loop()
    started = loop.time()
    period = 1.0 / update_hz
    try:
        while loop.time() - started < seconds:
            elapsed = loop.time() - started
            value = amplitude * math.sin(math.tau * frequency_hz * elapsed)
            pose = {"roll": 0.0, "pitch": 0.0, "yaw": 0.0}
            pose[axis] = value
            await set_body_pose(connection, **pose)
            await asyncio.sleep(period)
    finally:
        await set_body_pose(connection)


async def execute_body_pose(routine):
    """Run an Euler routine with the lifecycle used by the working originals.

    Unlike walking scenarios, Euler posture scenarios restore a neutral pose,
    allow it to settle, and disconnect without sending the generic StopMove
    cleanup afterward.
    """
    connection = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=ROBOT_IP,
        aes_128_key=CONNECTION_CONFIG.get("aes_128_key") or None,
    )
    connected = False
    try:
        print(f"Connecting to Go2 at {ROBOT_IP}...")
        await connection.connect()
        connected = True
        print("Enabling MCF balance stand...")
        response = await sport_request(connection, SPORT_CMD_MCF["BalanceStand"])
        require_success(response, "BalanceStand")
        await asyncio.sleep(2)
        await routine(connection)
    finally:
        if connected:
            try:
                await set_body_pose(connection)
                await asyncio.sleep(0.5)
            finally:
                await connection.disconnect()


def run_body_pose_program(routine):
    """Confirm safety and run a nod, head shake, or body-shake routine."""
    confirmation = input(
        "Place the Go2 on a flat, dry, non-slip floor and keep the e-stop ready. "
        "Type SHAKE to continue: "
    )
    if confirmation != "SHAKE":
        raise SystemExit("Cancelled; no command was sent.")
    try:
        asyncio.run(execute_body_pose(routine))
    except KeyboardInterrupt:
        print("\nInterrupted")
