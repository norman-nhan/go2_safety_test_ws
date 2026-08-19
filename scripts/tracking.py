"""YOLO person tracking with camera-based yaw and distance control."""

import asyncio
import json
import random
import time
from pathlib import Path

import cv2
from aiortc.mediastreams import MediaStreamError
from ultralytics import YOLO
from unitree_webrtc_connect import (
    OBSTACLES_AVOID_API,
    RTC_TOPIC,
    SPORT_CMD_MCF,
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)

import _bootstrap  # noqa: F401
from src.common_api import (
    CONNECTION_CONFIG,
    ROBOT_IP,
    require_success,
    sport_request,
)
from src.config import CONFIG_PATH, section

CONFIG = section("tracking")
MODEL_PATH = Path(CONFIG["model_path"])
if not MODEL_PATH.is_absolute():
    MODEL_PATH = CONFIG_PATH.parent / MODEL_PATH


async def obstacle_request(connection, api_id, parameter=None):
    """Send one request through the dedicated obstacle-avoidance service."""
    request = {"api_id": api_id}
    if parameter is not None:
        request["parameter"] = parameter
    return await connection.datachannel.pub_sub.publish_request_new(
        RTC_TOPIC["OBSTACLES_AVOID"], request
    )


def obstacle_move(connection, *, x=0.0, y=0.0, yaw=0.0):
    """Refresh an avoidance-filtered velocity without waiting for a reply."""
    request_id = int(time.time() * 1000) % 2147483648 + random.randint(0, 1000)
    request = {
        "header": {
            "identity": {
                "id": request_id,
                "api_id": OBSTACLES_AVOID_API["MOVE"],
            },
            "policy": {"priority": 0, "noreply": True},
        },
        "parameter": json.dumps({"x": x, "y": y, "yaw": yaw, "mode": 0}),
        "binary": [],
    }
    connection.datachannel.pub_sub.publish_without_callback(
        RTC_TOPIC["OBSTACLES_AVOID"], request
    )


async def enable_obstacle_control(connection):
    """Enable avoidance and route API velocity commands through that service."""
    response = await obstacle_request(
        connection, OBSTACLES_AVOID_API["SWITCH_SET"], {"enable": True}
    )
    require_success(response, "ObstacleAvoidanceEnable")
    response = await obstacle_request(
        connection,
        OBSTACLES_AVOID_API["USE_REMOTE_COMMAND_FROM_API"],
        {"is_remote_commands_from_api": True},
    )
    require_success(response, "ObstacleAvoidanceApiControl")
    await asyncio.sleep(0.5)
    print("Obstacle avoidance enabled for tracking commands.")


async def disable_obstacle_control(connection):
    """Stop avoidance movement and return command ownership to the robot."""
    obstacle_move(connection)
    await asyncio.sleep(0.2)
    response = await obstacle_request(
        connection,
        OBSTACLES_AVOID_API["USE_REMOTE_COMMAND_FROM_API"],
        {"is_remote_commands_from_api": False},
    )
    require_success(response, "ObstacleAvoidanceApiRelease")


class YoloPersonDetector:
    """Select the largest person and expose smoothed centering/area errors."""

    def __init__(self):
        print(f"Loading YOLO model {MODEL_PATH} on device {CONFIG['device']}...")
        self.model = YOLO(str(MODEL_PATH))
        self.target_error = None
        self.target_area = None
        self.last_target_at = None

    def detect(self, frame):
        """Run one inference and annotate the selected person."""
        result = self.model.predict(
            source=frame,
            classes=[0],
            conf=float(CONFIG["confidence"]),
            imgsz=int(CONFIG["image_size"]),
            device=str(CONFIG["device"]),
            verbose=False,
        )[0]
        people = []
        if result.boxes is not None:
            boxes = result.boxes.xyxy.detach().cpu().numpy()
            confidences = result.boxes.conf.detach().cpu().numpy()
            polygons = result.masks.xy if result.masks is not None else [None] * len(boxes)
            people = list(zip(boxes, confidences, polygons))
        target = max(
            people,
            key=lambda item: (item[0][2] - item[0][0]) * (item[0][3] - item[0][1]),
            default=None,
        )
        now = time.monotonic()
        if target is not None:
            box, _, polygon = target
            error = float(
                ((box[0] + box[2]) / 2 - frame.shape[1] / 2)
                / (frame.shape[1] / 2)
            )
            pixels = (
                cv2.contourArea(polygon.astype("float32"))
                if polygon is not None and len(polygon) >= 3
                else max(0.0, float(box[2] - box[0]))
                * max(0.0, float(box[3] - box[1]))
            )
            area = pixels / float(frame.shape[0] * frame.shape[1])
            smoothing = float(CONFIG["target_smoothing"])
            fresh = (
                self.last_target_at is not None
                and now - self.last_target_at <= float(CONFIG["detection_timeout"])
            )
            self.target_error = (
                smoothing * error + (1 - smoothing) * self.target_error
                if fresh and self.target_error is not None else error
            )
            self.target_area = (
                smoothing * area + (1 - smoothing) * self.target_area
                if fresh and self.target_area is not None else area
            )
            self.last_target_at = now
        for box, confidence, polygon in people:
            x1, y1, x2, y2 = (int(value) for value in box)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            if polygon is not None:
                cv2.polylines(frame, [polygon.astype("int32")], True, (0, 200, 255), 2)
            cv2.putText(
                frame, f"person {float(confidence):.2f}", (x1, max(22, y1 - 8)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 0), 2,
            )
        return frame


async def _motion_controller(connection, detector, stop_event):
    """Center a fresh person target and maintain configured apparent size."""
    period = 0.1
    try:
        while not stop_event.is_set():
            fresh = (
                detector.last_target_at is not None
                and time.monotonic() - detector.last_target_at
                <= float(CONFIG["detection_timeout"])
            )
            yaw = 0.0
            forward = 0.0
            if fresh and abs(detector.target_error) > float(CONFIG["center_deadband"]):
                magnitude = min(
                    float(CONFIG["max_yaw_speed"]),
                    max(
                        float(CONFIG["min_yaw_speed"]),
                        float(CONFIG["yaw_kp"]) * abs(detector.target_error),
                    ),
                )
                direction = float(CONFIG["yaw_direction"])
                yaw = magnitude * (direction if detector.target_error > 0 else -direction)
            if fresh and detector.target_area < float(CONFIG["min_person_area"]):
                forward = float(CONFIG["forward_speed"])
            elif (
                fresh
                and bool(CONFIG["allow_backward"])
                and detector.target_area > float(CONFIG["max_person_area"])
            ):
                forward = -float(CONFIG["backward_speed"])
            obstacle_move(connection, x=forward, y=0.0, yaw=yaw)
            await asyncio.sleep(period)
    finally:
        obstacle_move(connection)


async def _run_tracking():
    """Own the detector, camera, controller, and safe shutdown lifecycle."""
    minimum = float(CONFIG["min_person_area"])
    maximum = float(CONFIG["max_person_area"])
    if not 0 <= minimum < maximum <= 1:
        raise ValueError("tracking person-area values must satisfy 0 <= min < max <= 1")
    detector = YoloPersonDetector()
    stop_event = asyncio.Event()
    ready = asyncio.Event()
    video_done = asyncio.Event()
    controller = None
    connection = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=ROBOT_IP,
        aes_128_key=CONNECTION_CONFIG.get("aes_128_key") or None,
    )

    async def receive(track):
        try:
            while not stop_event.is_set():
                try:
                    frame = await track.recv()
                except MediaStreamError:
                    stop_event.set()
                    return
                image = frame.to_ndarray(format="bgr24")
                annotated = await asyncio.to_thread(detector.detect, image)
                ready.set()
                cv2.imshow("Go2 YOLO person tracking", annotated)
                if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                    stop_event.set()
        finally:
            video_done.set()

    connected = False
    obstacle_controlled = False
    try:
        print(f"Connecting to Go2 at {ROBOT_IP}...")
        await connection.connect()
        connected = True
        connection.video.add_track_callback(receive)
        await connection.datachannel.disableTrafficSaving(True)
        connection.video.switchVideoChannel(True)
        await asyncio.wait_for(ready.wait(), timeout=20)
        response = await sport_request(connection, SPORT_CMD_MCF["BalanceStand"])
        require_success(response, "BalanceStand")
        await asyncio.sleep(2)
        await enable_obstacle_control(connection)
        obstacle_controlled = True
        controller = asyncio.create_task(
            _motion_controller(connection, detector, stop_event)
        )
        controller.add_done_callback(lambda _task: stop_event.set())
        print("Tracking enabled. Press q, Esc, or Ctrl+C to stop.")
        await stop_event.wait()
        await controller
    finally:
        stop_event.set()
        if controller is not None and not controller.done():
            controller.cancel()
            await asyncio.gather(controller, return_exceptions=True)
        if connected:
            try:
                try:
                    if obstacle_controlled:
                        await disable_obstacle_control(connection)
                finally:
                    await sport_request(connection, SPORT_CMD_MCF["StopMove"])
                    connection.video.switchVideoChannel(False)
            finally:
                await connection.disconnect()
        if not video_done.is_set():
            try:
                await asyncio.wait_for(video_done.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        cv2.destroyAllWindows()


def run_tracking():
    """Require TRACK confirmation and start autonomous person tracking."""
    confirmation = input(
        "Clear the area in front of and behind the Go2 and keep the e-stop ready. "
        "Type TRACK to continue: "
    )
    if confirmation != "TRACK":
        raise SystemExit("Cancelled; no robot command was sent.")
    try:
        asyncio.run(_run_tracking())
    except KeyboardInterrupt:
        print("\nTracking interrupted")


if __name__ == "__main__":
    run_tracking()
