"""Shared hand detection and gesture-controlled movement for C1-C3.

There are three layers in this module:

1. ``GestureDetector`` converts each camera frame into STOP, WAVING, or no
   gesture. MediaPipe supplies hand landmarks; the code here applies the
   open-palm and motion rules from ``config.yaml``.
2. ``_receive_video`` continuously reads frames and updates the detector while
   one controller task sends movement commands.
3. ``run_gesture_trigger`` and ``run_gesture_stop`` are the public entry points
   called by scripts/c1.py, scripts/c2.py, and scripts/c3.py.

Public call paths::

    run_gesture_trigger(...)
        -> _confirm_and_run(...)
            -> _run(..., _trigger_controller)

    run_gesture_stop(...)
        -> _confirm_and_run(...)
            -> _run(..., _stop_controller)

``_run`` owns the complete WebRTC/video/controller lifecycle and guarantees a
StopMove command during cleanup. Names beginning with ``_`` are internal
helpers; scenario scripts should normally call only the two public runners.
"""

import asyncio
import logging
import math
import os
import sys
import time
import types
from collections import deque

os.environ.setdefault("QT_QPA_FONTDIR", "/usr/share/fonts/truetype/dejavu")
os.environ.setdefault("MPLCONFIGDIR", "/tmp/go2_mediapipe_matplotlib")

import cv2
from aiortc.mediastreams import MediaStreamError

# The Go2 can send a few inter-frame H.264 packets before the first keyframe
# arrives after enabling its camera channel.  aiortc rejects those packets,
# then resumes normally at the keyframe; they are not actionable errors.
# Keep genuine errors from the rest of the application visible.
logging.getLogger("aiortc.codecs.h264").setLevel(logging.ERROR)

# MediaPipe probes its optional audio dependency even though this module only
# uses vision. Avoid a PortAudio probe on the robot workstation.
sys.modules.setdefault("sounddevice", types.ModuleType("sounddevice"))

import mediapipe as mp
from unitree_webrtc_connect import (
    SPORT_CMD_MCF,
    UnitreeWebRTCConnection,
    WebRTCConnectionMethod,
)

from src.common_api import (
    ROBOT_IP,
    require_success,
    sport_request,
    sport_request_no_reply,
)
from src.config import section

COMMAND_HZ = 10.0
MOVEMENT_CONFIG = section("gesture_movement")
HAND_CONFIG = section("hand_detection")
CONNECTION_CONFIG = section("connection")

FORWARD_SPEED_MPS = float(MOVEMENT_CONFIG["forward_speed"])
MOVE_SECONDS = float(MOVEMENT_CONFIG["move_seconds"])
TRIGGER_COOLDOWN_SECONDS = float(MOVEMENT_CONFIG["cooldown_seconds"])

MIN_DETECTION_CONFIDENCE = float(HAND_CONFIG["detection_confidence"])
MIN_TRACKING_CONFIDENCE = float(HAND_CONFIG["tracking_confidence"])
STOP_HOLD_SECONDS = float(HAND_CONFIG["stop_hold_seconds"])
STOP_MAX_TRAVEL = float(HAND_CONFIG["stop_max_travel"])
WAVE_WINDOW_SECONDS = float(HAND_CONFIG["wave_window_seconds"])
WAVE_MIN_STEP = float(HAND_CONFIG["wave_min_step"])
WAVE_MIN_TRAVEL = float(HAND_CONFIG["wave_min_travel"])
WAVE_MIN_SPEED = float(HAND_CONFIG["wave_min_speed"])
WAVE_MIN_DURATION = float(HAND_CONFIG["wave_min_duration"])
WAVE_MIN_SAMPLES = int(HAND_CONFIG["wave_min_samples"])
WAVE_ACTIVITY_SECONDS = float(HAND_CONFIG["wave_activity_seconds"])
WAVE_OPEN_GRACE_SECONDS = float(HAND_CONFIG["wave_open_grace_seconds"])
GESTURE_LATCH_SECONDS = float(HAND_CONFIG["gesture_latch_seconds"])


def angle_degrees(a, b, c):
    """Return the 2D angle ABC for three normalized MediaPipe landmarks.

    Finger straightness is estimated from this joint angle. A degenerate angle
    returns zero instead of dividing by a nearly zero vector length.
    """
    ba = (a.x - b.x, a.y - b.y)
    bc = (c.x - b.x, c.y - b.y)
    denominator = math.hypot(*ba) * math.hypot(*bc)
    if denominator < 1e-8:
        return 0.0
    cosine = max(-1.0, min(1.0, (ba[0] * bc[0] + ba[1] * bc[1]) / denominator))
    return math.degrees(math.acos(cosine))


def is_open_palm(landmarks):
    """Return whether at least three non-thumb fingers appear extended.

    Each finger must be both reasonably straight at its middle joint and have
    its tip farther from the wrist than its middle joint. The thumb is omitted
    because its geometry varies strongly with hand orientation.
    """
    finger_joints = ((5, 6, 8), (9, 10, 12), (13, 14, 16), (17, 18, 20))
    straight = sum(
        angle_degrees(landmarks[mcp], landmarks[pip], landmarks[tip]) >= 155.0
        for mcp, pip, tip in finger_joints
    )
    wrist = landmarks[0]
    extended = sum(
        math.hypot(landmarks[tip].x - wrist.x, landmarks[tip].y - wrist.y)
        > 1.35 * math.hypot(landmarks[pip].x - wrist.x, landmarks[pip].y - wrist.y)
        for _, pip, tip in finger_joints
    )
    return straight >= 3 and extended >= 3


class GestureDetector:
    """Stateful MediaPipe classifier for open-palm STOP and WAVING gestures.

    ``process`` must be called for consecutive frames from the same stream. The
    detector keeps a time-limited history because STOP depends on holding still
    and WAVING depends on sustained motion.

    Important consumer fields:

    - ``raw_gesture`` is the current frame-level STOP/WAVING result. Movement
      controllers use it when immediate detection matters.
    - ``gesture_event_id`` increments on each new STOP/WAVING transition.
    - ``last_gesture_event`` and ``last_gesture_event_at`` describe that event.
    - ``latched_gesture`` is display-oriented and remains visible briefly so
      the label does not flicker between frames.
    """

    def __init__(self):
        """Create MediaPipe Hands and initialize empty gesture history."""
        self.hands = mp.solutions.hands.Hands(
            static_image_mode=False,
            max_num_hands=2,
            model_complexity=1,
            min_detection_confidence=MIN_DETECTION_CONFIDENCE,
            min_tracking_confidence=MIN_TRACKING_CONFIDENCE,
        )
        self.drawer = mp.solutions.drawing_utils
        self.connections = mp.solutions.hands.HAND_CONNECTIONS
        self.open_history = deque()
        self.open_since = None
        self.last_open_at = None
        self.latched_gesture = None
        self.latched_until = 0.0
        self.last_reported = None
        self.raw_gesture = None
        self.gesture_event_id = 0
        self.last_gesture_event = None
        self.last_gesture_event_at = None

    def close(self):
        """Release MediaPipe's native graph resources after video has stopped."""
        self.hands.close()

    def _motion_metrics(self, samples=None):
        """Measure accumulated palm travel, whole-hand travel, and speed.

        A sample is ``(timestamp, palm_x, hand_pose)``. Palm travel measures
        horizontal translation. Whole-hand travel averages landmark motion, so
        it also captures vertical movement, depth/scale change, and wrist
        rotation. Per-frame movement below ``WAVE_MIN_STEP`` is treated as
        normal landmark jitter and ignored.
        """
        samples = list(self.open_history) if samples is None else list(samples)
        if len(samples) < 2:
            return 0.0, 0.0, 0.0
        palm_travel = 0.0
        hand_travel = 0.0
        for (_, x1, pose1), (_, x2, pose2) in zip(samples, samples[1:]):
            palm_step = abs(x2 - x1)
            if palm_step >= WAVE_MIN_STEP:
                palm_travel += palm_step
            point_steps = [
                math.hypot(x2p - x1p, y2p - y1p)
                for (x1p, y1p), (x2p, y2p) in zip(pose1, pose2)
            ] if pose1 and pose2 else []
            hand_step = sum(point_steps) / len(point_steps) if point_steps else 0.0
            if hand_step >= WAVE_MIN_STEP:
                hand_travel += hand_step
        elapsed = samples[-1][0] - samples[0][0]
        travel = max(palm_travel, hand_travel)
        return palm_travel, hand_travel, travel / elapsed if elapsed > 1e-6 else 0.0

    def _classify(self, now, palm_x, open_palm, hand_pose):
        """Update temporal history and return the current display classification.

        WAVING requires moderate motion sustained in both halves of the recent
        activity window. This prevents one adjustment while raising the hand
        from becoming a wave. STOP uses only the recent hold window and permits
        motion up to ``STOP_MAX_TRAVEL``, since a human hand is never perfectly
        still.

        WAVING is checked first because an open palm is shared by both gestures.
        The returned gesture may be briefly latched for display; ``raw_gesture``
        always contains the unlatched detection used by controllers.
        """
        recently_open = (
            self.last_open_at is not None
            and now - self.last_open_at <= WAVE_OPEN_GRACE_SECONDS
        )
        if open_palm:
            if self.open_since is None or (
                self.last_open_at is not None and now - self.last_open_at > 0.3
            ):
                self.open_since = now
                self.open_history.clear()
            self.last_open_at = now
            self.open_history.append((now, palm_x, hand_pose))
        elif recently_open and hand_pose is not None:
            self.open_history.append((now, palm_x, hand_pose))
        elif self.last_open_at is None or now - self.last_open_at > 0.3:
            self.open_since = None
            self.open_history.clear()

        while self.open_history and now - self.open_history[0][0] > WAVE_WINDOW_SECONDS:
            self.open_history.popleft()

        palm_travel, hand_travel, speed = self._motion_metrics()
        wave_samples = [
            sample for sample in self.open_history
            if now - sample[0] <= WAVE_ACTIVITY_SECONDS
        ]
        wave_palm, wave_hand, wave_speed = self._motion_metrics(wave_samples)
        wave_travel = max(wave_palm, wave_hand)
        wave_elapsed = (
            wave_samples[-1][0] - wave_samples[0][0]
            if len(wave_samples) >= 2 else 0.0
        )
        midpoint = wave_samples[0][0] + wave_elapsed / 2 if wave_samples else now
        early = [sample for sample in wave_samples if sample[0] <= midpoint]
        late = [sample for sample in wave_samples if sample[0] >= midpoint]
        early_motion = max(self._motion_metrics(early)[:2])
        late_motion = max(self._motion_metrics(late)[:2])

        stop_samples = [
            sample for sample in self.open_history
            if now - sample[0] <= STOP_HOLD_SECONDS
        ]
        stop_xs = [sample[1] for sample in stop_samples]
        stop_span = max(stop_xs) - min(stop_xs) if stop_xs else 0.0
        stop_palm, stop_hand, _ = self._motion_metrics(stop_samples)

        detected = None
        if (open_palm or recently_open) and hand_pose is not None and (
            len(wave_samples) >= WAVE_MIN_SAMPLES
            and wave_elapsed >= WAVE_MIN_DURATION
            and wave_travel >= WAVE_MIN_TRAVEL
            and wave_speed >= WAVE_MIN_SPEED
            and early_motion >= WAVE_MIN_TRAVEL * 0.2
            and late_motion >= WAVE_MIN_TRAVEL * 0.2
        ):
            detected = "WAVING"
        elif (
            open_palm
            and self.open_since is not None
            and now - self.open_since >= STOP_HOLD_SECONDS
            and stop_span <= STOP_MAX_TRAVEL
            and max(stop_palm, stop_hand) <= STOP_MAX_TRAVEL
        ):
            detected = "STOP"

        if detected is not None and detected != self.raw_gesture:
            self.gesture_event_id += 1
            self.last_gesture_event = detected
            self.last_gesture_event_at = now
        self.raw_gesture = detected
        if detected:
            self.latched_gesture = detected
            self.latched_until = now + GESTURE_LATCH_SECONDS
        elif now >= self.latched_until:
            self.latched_gesture = "OPEN PALM" if open_palm else None
        return self.latched_gesture, palm_travel, hand_travel, speed

    def process(self, frame):
        """Detect, classify, and annotate one BGR camera frame.

        When multiple hands are visible, the largest image-space hand is used
        for classification while landmarks are drawn for all hands. The method
        mutates and returns ``frame`` with its gesture label and motion metrics.
        It runs in a worker thread so MediaPipe inference does not block the
        asyncio video/controller loop.
        """
        now = time.monotonic()
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        result = self.hands.process(rgb)
        selected = None
        if result.multi_hand_landmarks:
            selected = max(
                result.multi_hand_landmarks,
                key=lambda hand: (
                    max(p.x for p in hand.landmark) - min(p.x for p in hand.landmark)
                ) * (
                    max(p.y for p in hand.landmark) - min(p.y for p in hand.landmark)
                ),
            )
            for hand in result.multi_hand_landmarks:
                self.drawer.draw_landmarks(frame, hand, self.connections)

        open_palm = selected is not None and is_open_palm(selected.landmark)
        palm_x = (
            sum(selected.landmark[i].x for i in (0, 5, 9, 13, 17)) / 5
            if selected is not None else 0.0
        )
        points = (0, 4, 5, 8, 9, 12, 13, 16, 17, 20)
        pose = (
            tuple((selected.landmark[i].x, selected.landmark[i].y) for i in points)
            if selected is not None else None
        )
        gesture, palm_travel, hand_travel, speed = self._classify(
            now, palm_x, open_palm, pose
        )
        labels = {
            "STOP": ("STOP", (0, 0, 255)),
            "WAVING": ("WAVING", (0, 255, 255)),
            "OPEN PALM": ("OPEN PALM - HOLD STILL OR WAVE", (255, 200, 0)),
        }
        label, color = labels.get(
            gesture,
            ("HAND DETECTED", (0, 255, 0)) if selected else ("NO HAND", (180, 180, 180)),
        )
        cv2.rectangle(frame, (0, 0), (frame.shape[1], 76), (0, 0, 0), -1)
        cv2.putText(frame, label, (14, 34), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
        cv2.putText(
            frame,
            f"palm={palm_travel:.3f} hand={hand_travel:.3f} speed={speed:.2f}/s",
            (14, 64), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1,
        )
        if gesture in ("STOP", "WAVING") and gesture != self.last_reported:
            print(f"[{time.strftime('%H:%M:%S')}] Gesture detected: {gesture}", flush=True)
        self.last_reported = gesture if gesture in ("STOP", "WAVING") else None
        return frame


async def _receive_video(track, detector, stop_event, target, ready, done):
    """Read camera frames until shutdown and update ``detector``.

    ``ready`` signals that the first usable frame has arrived, preventing robot
    movement before vision is active. ``done`` is always set in ``finally`` so
    shutdown can wait for frame processing before closing MediaPipe. Pressing
    q or Esc in the preview requests shutdown through ``stop_event``.
    """
    try:
        while not stop_event.is_set():
            try:
                video_frame = await track.recv()
            except MediaStreamError:
                stop_event.set()
                return
            frame = video_frame.to_ndarray(format="bgr24")
            annotated = await asyncio.to_thread(detector.process, frame)
            ready.set()
            cv2.putText(
                annotated, f"Trigger: {target}", (14, 100),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2,
            )
            cv2.imshow(f"Go2 gesture: {target}", annotated)
            if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                stop_event.set()
                return
    finally:
        done.set()


async def _trigger_controller(connection, detector, stop_event, target, delay):
    """Run one short forward burst for each new target-gesture event.

    Used by C1 and C3. Event IDs prevent one gesture from triggering on every
    camera frame, and the cooldown rejects repeated transitions that occur too
    close together. After the optional delay, Move is refreshed at COMMAND_HZ
    for the configured duration and StopMove is sent afterward.
    """
    seen_event = detector.gesture_event_id
    last_trigger = -float("inf")
    period = 1 / COMMAND_HZ
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            event = detector.gesture_event_id
            trigger = (
                event != seen_event
                and detector.last_gesture_event == target
                and detector.last_gesture_event_at is not None
                and now - detector.last_gesture_event_at <= 0.5
                and now - last_trigger >= TRIGGER_COOLDOWN_SECONDS
            )
            seen_event = event
            if not trigger:
                await asyncio.sleep(period)
                continue
            last_trigger = now
            if delay:
                print(f"{target} captured; waiting {delay:.1f} seconds...", flush=True)
                await asyncio.sleep(delay)
            deadline = time.monotonic() + MOVE_SECONDS
            while time.monotonic() < deadline and not stop_event.is_set():
                sport_request_no_reply(
                    connection, SPORT_CMD_MCF["Move"],
                    {"x": FORWARD_SPEED_MPS, "y": 0.0, "z": 0.0},
                )
                await asyncio.sleep(period)
            response = await sport_request(connection, SPORT_CMD_MCF["StopMove"])
            require_success(response, "StopMove")
    finally:
        await sport_request(connection, SPORT_CMD_MCF["StopMove"])


async def _stop_controller(connection, detector, stop_event, target, speed, delay, timeout):
    """Move immediately until a target gesture or fail-safe timeout.

    Used by C2. The first matching raw gesture schedules a stop after ``delay``;
    this schedule cannot be cancelled by later classification changes. The
    independent ``timeout`` bounds movement if vision never recognizes the
    gesture. StopMove is guaranteed in ``finally``.
    """
    started = time.monotonic()
    stop_at = None
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            if stop_at is None and detector.raw_gesture == target:
                stop_at = now + delay
                print(f"{target} detected; stopping in {delay:.1f} seconds.", flush=True)
            if stop_at is not None and now >= stop_at:
                stop_event.set()
                break
            if now - started >= timeout:
                print("Maximum movement timeout reached; stopping.", flush=True)
                stop_event.set()
                break
            sport_request_no_reply(
                connection, SPORT_CMD_MCF["Move"],
                {"x": speed, "y": 0.0, "z": 0.0},
            )
            await asyncio.sleep(1 / COMMAND_HZ)
    finally:
        response = await sport_request(connection, SPORT_CMD_MCF["StopMove"])
        require_success(response, "StopMove")


async def _run(target, controller_factory):
    """Own one gesture program's connection, video, and controller lifecycle.

    Startup order is deliberate: connect, enable video, wait for the first
    processed frame, enter BalanceStand, then start movement control. During
    shutdown it cancels unfinished control, sends StopMove, disables video,
    disconnects, waits for frame processing, closes windows, and finally closes
    MediaPipe. Waiting avoids closing MediaPipe while ``process`` is active.

    ``controller_factory`` selects trigger mode or stop-on-gesture mode while
    keeping this safety-sensitive lifecycle in one place.
    """
    detector = GestureDetector()
    stop_event = asyncio.Event()
    ready = asyncio.Event()
    done = asyncio.Event()
    connection = UnitreeWebRTCConnection(
        WebRTCConnectionMethod.LocalSTA,
        ip=ROBOT_IP,
        aes_128_key=CONNECTION_CONFIG.get("aes_128_key") or None,
    )
    connected = False
    controller = None
    try:
        print(f"Connecting to Go2 at {ROBOT_IP}...")
        await connection.connect()
        connected = True
        connection.video.add_track_callback(
            lambda track: _receive_video(track, detector, stop_event, target, ready, done)
        )
        await connection.datachannel.disableTrafficSaving(True)
        connection.video.switchVideoChannel(True)
        print("Waiting for the Go2 camera to deliver its first frame...", flush=True)
        await asyncio.wait_for(ready.wait(), timeout=20)
        print("Go2 camera connected.", flush=True)
        response = await sport_request(connection, SPORT_CMD_MCF["BalanceStand"])
        require_success(response, "BalanceStand")
        await asyncio.sleep(2)
        controller = asyncio.create_task(controller_factory(connection, detector, stop_event))
        controller.add_done_callback(lambda _task: stop_event.set())
        await stop_event.wait()
        await controller
    finally:
        stop_event.set()
        if controller is not None and not controller.done():
            controller.cancel()
            await asyncio.gather(controller, return_exceptions=True)
        if connected:
            try:
                await sport_request(connection, SPORT_CMD_MCF["StopMove"])
                connection.video.switchVideoChannel(False)
            finally:
                await connection.disconnect()
        if not done.is_set():
            try:
                await asyncio.wait_for(done.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass
        cv2.destroyAllWindows()
        detector.close()


def _confirm_and_run(coroutine):
    """Require the exact MOVE confirmation and run an async gesture program."""
    confirmation = input(
        "Clear the area around the Go2 and keep the remote/e-stop ready. "
        "Type MOVE to continue: "
    )
    if confirmation != "MOVE":
        raise SystemExit("Cancelled; no robot command was sent.")
    try:
        asyncio.run(coroutine)
    except KeyboardInterrupt:
        print("\nInterrupted")


def run_gesture_trigger(target_gesture, *, move_delay_seconds=0.0):
    """Run trigger mode for C1/C3.

    The robot waits in BalanceStand. Each new ``target_gesture`` event causes a
    short forward burst after ``move_delay_seconds``. Movement speed, duration,
    and retrigger cooldown come from ``config.yaml``.
    """
    _confirm_and_run(_run(
        target_gesture,
        lambda connection, detector, stop: _trigger_controller(
            connection, detector, stop, target_gesture, move_delay_seconds
        ),
    ))


def run_gesture_stop(
    target_gesture, *, forward_speed, stop_delay_seconds, max_move_seconds
):
    """Run C2-style mode: move now and stop after seeing a gesture.

    ``forward_speed`` controls continuous forward velocity. Once
    ``target_gesture`` is detected, movement continues only for
    ``stop_delay_seconds``. ``max_move_seconds`` is the fail-safe limit when no
    gesture is recognized.
    """
    _confirm_and_run(_run(
        target_gesture,
        lambda connection, detector, stop: _stop_controller(
            connection, detector, stop, target_gesture, forward_speed,
            stop_delay_seconds, max_move_seconds,
        ),
    ))
