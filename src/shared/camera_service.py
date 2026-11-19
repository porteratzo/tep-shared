"""Camera frame service client - Redis Streams based"""

import logging
import time
from datetime import datetime
from typing import Any, List, Optional, cast

from shared.core.camera_stream import decode_frame_message, parse_frame_timestamp

try:
    import redis
except ImportError:
    raise ImportError("redis package required. Install with: pip install redis")

logger = logging.getLogger(__name__)

FRAME_LOG_INTERVAL_SECONDS = 20.0


class CameraServiceClient:
    """
    Client for fetching frames from camera_broker via Redis Streams.

    Consumes frames published by ``mydepthai.broker.camera_broker`` to Redis Streams.
    Supports multiple cameras with cam_type and output_type filtering.
    """

    def __init__(
        self,
        redis_host: str,
        redis_port: int,
        redis_db: int = 0,
        redis_password: Optional[str] = None,
        stream_name: str = "camera_frames",
        block_ms: int = 1000,
        filter_cam_type: Optional[str] = None,
        filter_output_type: Optional[str] = None,
        max_connections: int = 50,
        socket_timeout: int = 5,
        socket_connect_timeout: int = 5,
    ):
        """
        Initialize camera service client.

        Args:
            redis_host: Redis server host
            redis_port: Redis server port
            redis_db: Redis database number
            redis_password: Redis password (if required)
            stream_name: Redis stream name to consume from
            block_ms: Block timeout for reading (milliseconds)
            filter_cam_type: Optional filter for camera type (e.g., 'color', 'depth')
            filter_output_type: Optional filter for output type (e.g., 'video', 'preview')
        """
        self.stream_name = stream_name
        self.filter_cam_type = filter_cam_type
        self.filter_output_type = filter_output_type
        self.is_connected = False
        self._last_frame_log_time: float = 0.0

        # Initialize Redis connection
        self.redis_client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            decode_responses=False,  # Need bytes for image data
            max_connections=max_connections,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_connect_timeout,
        )

        # Test connection
        try:
            self.redis_client.ping()
            self.is_connected = True
            logger.info(f"Connected to Redis at {redis_host}:{redis_port}")
        except redis.ConnectionError as e:
            self.is_connected = False
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def _decode_message(self, data: dict[bytes, bytes]) -> dict[str, Any]:
        """Decode raw Redis message byte values via the shared ``camera_frames`` codec."""
        msg = decode_frame_message(data)
        return {
            "camera_id": msg["camera_id"],
            "cam_type": msg["cam_type"],
            "output_type": msg["output_type"],
            "timestamp": msg["timestamp"],
            "frame_bytes": msg["frame"],
            "encoding": msg["encoding"],
            # Producer-side placeholder ("CAMERA OFFLINE"); a missing field means a real frame.
            "is_fallback": msg["is_fallback"],
        }

    def _frame_passes_filters(self, msg: dict, camera_id: Optional[str]) -> bool:
        """Return True if a decoded message passes all active filters."""
        if camera_id and msg["camera_id"] != camera_id:
            return False
        if self.filter_cam_type and msg["cam_type"] != self.filter_cam_type:
            return False
        if self.filter_output_type and msg["output_type"] != self.filter_output_type:
            return False
        return True

    def get_frame(
        self, camera_id: Optional[str] = None, include_fallback: bool = False
    ) -> tuple[Optional[str], Optional[bytes]]:
        """:meth:`get_frame_with_timestamp` without the capture time (live-view and debug callers)."""
        cam_id, frame_bytes, _captured_at = self.get_frame_with_timestamp(camera_id, include_fallback)
        return cam_id, frame_bytes

    def get_frame_with_timestamp(
        self, camera_id: Optional[str] = None, include_fallback: bool = False
    ) -> tuple[Optional[str], Optional[bytes], Optional[datetime]]:
        """
        Fetch the most recent frame from the camera broker via Redis Streams.

        Also returns when the broker captured it (the message's ``timestamp``, UTC; ``None`` if the
        field is missing or unparsable), so callers can reject frames the stream merely still holds
        after the camera or broker died (review 2026-10-21 §1.2).

        Always returns the latest available frame, skipping any backlog. Fallback
        frames (the broker's synthetic "CAMERA OFFLINE" placeholder, ``is_fallback=1``)
        are skipped unless ``include_fallback`` is set: they must never reach anomaly
        detection. Only live-view callers should opt in.

        Args:
            camera_id: Optional camera identifier to filter by
            include_fallback: Also return fallback placeholder frames (display only)

        Returns:
            tuple: (camera_id, frame_bytes, captured_at) or (None, None, None) on error
        """
        try:
            # Read recent messages in reverse order to get the latest first.
            # Using xrevrange avoids accumulating a backlog when the consumer
            # is slower than the producer.
            messages = cast(List[Any], self.redis_client.xrevrange(self.stream_name, count=20))

            if not messages:
                logger.debug("No frames available in stream")
                return None, None, None

            saw_fallback = False
            for _message_id, data in messages:
                msg = self._decode_message(data)
                if not self._frame_passes_filters(msg, camera_id):
                    continue
                if msg["is_fallback"] and not include_fallback:
                    saw_fallback = True
                    continue
                if not msg["frame_bytes"]:
                    logger.warning(f"Received message with no frame data from {msg['camera_id']}")
                    continue
                if msg["encoding"] != "jpeg":
                    logger.warning(f"Unsupported encoding: {msg['encoding']}")
                    continue

                self.is_connected = True
                now = time.time()
                if now - self._last_frame_log_time >= FRAME_LOG_INTERVAL_SECONDS:
                    logger.info(
                        f"Fetched frame from camera {msg['camera_id']} "
                        f"(type={msg['cam_type']}, output={msg['output_type']}) at {msg['timestamp']}"
                    )
                    self._last_frame_log_time = now
                return msg["camera_id"], msg["frame_bytes"], parse_frame_timestamp(msg["timestamp"])

            if saw_fallback:
                # Only placeholders are available: the camera is down, not "connected".
                self.is_connected = False
                logger.warning("Only fallback (camera offline) frames available; skipping")
            else:
                logger.debug("No frames matching filter criteria")
            return None, None, None

        except redis.ConnectionError as e:
            self.is_connected = False
            logger.error(f"Redis connection error: {e}")
            return None, None, None
        except Exception as e:
            self.is_connected = False
            logger.error(f"Error fetching frame from Redis: {e}")
            return None, None, None

    def list_camera_ids(self, count: int = 100) -> List[str]:
        """Return the distinct camera ids currently publishing to the stream.

        Scans the most recent `count` messages (default 100, vs. get_frame's 20 —
        wide enough that several interleaved cameras each still show up) and
        returns their camera_id values, most-recently-seen first, deduplicated.
        Used to fan scheduled capture/inspection out per camera (AD-B4) instead of
        picking whichever camera happens to publish the single newest message.
        """
        try:
            messages = cast(List[Any], self.redis_client.xrevrange(self.stream_name, count=count))
        except Exception as e:
            logger.error(f"Error listing camera ids from Redis: {e}")
            return []

        ids: List[str] = []
        seen: set = set()
        for _message_id, data in messages:
            msg = self._decode_message(data)
            if not self._frame_passes_filters(msg, None) or msg["is_fallback"]:
                continue
            cam_id = msg["camera_id"]
            if cam_id and cam_id not in seen:
                seen.add(cam_id)
                ids.append(cam_id)
        return ids

    def check_connection(self) -> bool:
        """
        Check if Redis connection is healthy.

        Returns:
            True if connection is active
        """
        try:
            self.redis_client.ping()
            self.is_connected = True
            return True
        except Exception as e:
            logger.warning(f"Connection check failed: {e}")
            self.is_connected = False
            return False

    def close(self) -> None:
        """Close Redis connection."""
        if self.redis_client is not None:
            try:
                self.redis_client.close()
                logger.info("Redis connection closed")
            except Exception as e:
                logger.error(f"Error closing Redis connection: {e}")
