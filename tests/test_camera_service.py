"""Unit tests for CameraServiceClient — Redis is fully mocked."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_client(ping_ok: bool = True) -> MagicMock:
    """Return a mock redis.Redis instance."""
    client = MagicMock()
    if ping_ok:
        client.ping.return_value = True
    else:
        import redis

        client.ping.side_effect = redis.ConnectionError("refused")
    return client


def _make_stream_message(
    camera_id: bytes = b"cam0",
    cam_type: bytes = b"color",
    output_type: bytes = b"video",
    timestamp: bytes = b"2024-01-01T00:00:00",
    frame: bytes = b"\xff\xd8\xff\xe0" + b"\x00" * 100,
    encoding: bytes = b"jpeg",
    is_fallback: bytes | None = None,
) -> list[Any]:
    """Synthetic Redis xrevrange entry."""
    data = {
        b"camera_id": camera_id,
        b"cam_type": cam_type,
        b"output_type": output_type,
        b"timestamp": timestamp,
        b"frame": frame,
        b"encoding": encoding,
    }
    if is_fallback is not None:
        data[b"is_fallback"] = is_fallback
    return [(b"1234-0", data)]


def _build_client(**kwargs: Any) -> tuple[Any, MagicMock]:
    """Build a CameraServiceClient with a mocked Redis."""
    mock_redis_cls = MagicMock()
    mock_instance = _make_redis_client(ping_ok=True)
    mock_redis_cls.return_value = mock_instance

    with patch("shared.camera_service.redis.Redis", mock_redis_cls):
        from shared.camera_service import CameraServiceClient

        client = CameraServiceClient(
            redis_host="localhost",
            redis_port=6379,
            **kwargs,
        )
        client.redis_client = mock_instance
        return client, mock_instance


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestCameraServiceClientInit:
    def test_is_connected_true_after_successful_ping(self) -> None:
        client, _ = _build_client()
        assert client.is_connected is True

    def test_raises_on_redis_connection_error(self) -> None:
        import redis

        mock_instance = MagicMock()
        mock_instance.ping.side_effect = redis.ConnectionError("refused")

        with patch("shared.camera_service.redis.Redis", return_value=mock_instance):
            from shared.camera_service import CameraServiceClient

            with pytest.raises(redis.ConnectionError):
                CameraServiceClient(redis_host="bad-host", redis_port=6379)

    def test_stores_stream_name(self) -> None:
        client, _ = _build_client(stream_name="my_stream")
        assert client.stream_name == "my_stream"

    def test_filter_attributes(self) -> None:
        client, _ = _build_client(filter_cam_type="color", filter_output_type="preview")
        assert client.filter_cam_type == "color"
        assert client.filter_output_type == "preview"


# ---------------------------------------------------------------------------
# _decode_message
# ---------------------------------------------------------------------------


class TestDecodeMessage:
    def test_decodes_all_fields(self) -> None:
        client, _ = _build_client()
        raw = {
            b"camera_id": b"cam1",
            b"cam_type": b"depth",
            b"output_type": b"preview",
            b"timestamp": b"2024-01-01T12:00:00",
            b"frame": b"\xff\xd8",
            b"encoding": b"jpeg",
        }
        msg = client._decode_message(raw)
        assert msg["camera_id"] == "cam1"
        assert msg["cam_type"] == "depth"
        assert msg["output_type"] == "preview"
        assert msg["frame_bytes"] == b"\xff\xd8"
        assert msg["encoding"] == "jpeg"
        assert msg["is_fallback"] is False

    def test_decodes_is_fallback_flag(self) -> None:
        client, _ = _build_client()
        assert client._decode_message({b"is_fallback": b"1"})["is_fallback"] is True

    def test_defaults_for_missing_keys(self) -> None:
        client, _ = _build_client()
        msg = client._decode_message({})
        assert msg["camera_id"] == "unknown"
        assert msg["cam_type"] == ""
        assert msg["encoding"] == "jpeg"


# ---------------------------------------------------------------------------
# _frame_passes_filters
# ---------------------------------------------------------------------------


class TestFramePassesFilters:
    def _msg(
        self,
        camera_id: str = "cam0",
        cam_type: str = "color",
        output_type: str = "video",
    ) -> dict[str, str]:
        return {"camera_id": camera_id, "cam_type": cam_type, "output_type": output_type}

    def test_no_filters_always_passes(self) -> None:
        client, _ = _build_client()
        assert client._frame_passes_filters(self._msg(), camera_id=None) is True

    def test_camera_id_filter_pass(self) -> None:
        client, _ = _build_client()
        assert client._frame_passes_filters(self._msg(camera_id="cam0"), camera_id="cam0") is True

    def test_camera_id_filter_fail(self) -> None:
        client, _ = _build_client()
        assert client._frame_passes_filters(self._msg(camera_id="cam1"), camera_id="cam0") is False

    def test_cam_type_filter_pass(self) -> None:
        client, _ = _build_client(filter_cam_type="color")
        assert client._frame_passes_filters(self._msg(cam_type="color"), camera_id=None) is True

    def test_cam_type_filter_fail(self) -> None:
        client, _ = _build_client(filter_cam_type="depth")
        assert client._frame_passes_filters(self._msg(cam_type="color"), camera_id=None) is False

    def test_output_type_filter_fail(self) -> None:
        client, _ = _build_client(filter_output_type="preview")
        assert client._frame_passes_filters(self._msg(output_type="video"), camera_id=None) is False


# ---------------------------------------------------------------------------
# get_frame
# ---------------------------------------------------------------------------


class TestGetFrame:
    def test_returns_camera_id_and_bytes_on_success(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(
            camera_id=b"cam0", frame=b"\xff\xd8\xff\xe0" + b"\x00" * 10
        )
        cam_id, frame_bytes = client.get_frame()
        assert cam_id == "cam0"
        assert isinstance(frame_bytes, bytes)
        assert len(frame_bytes) > 0

    def test_returns_none_none_when_stream_empty(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = []
        cam_id, frame_bytes = client.get_frame()
        assert cam_id is None
        assert frame_bytes is None

    def test_skips_messages_with_no_frame_data(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(frame=b"")
        cam_id, frame_bytes = client.get_frame()
        assert cam_id is None
        assert frame_bytes is None

    def test_skips_messages_with_unsupported_encoding(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(encoding=b"png", frame=b"\x89PNG")
        cam_id, frame_bytes = client.get_frame()
        assert cam_id is None
        assert frame_bytes is None

    def test_returns_none_none_on_connection_error(self) -> None:
        import redis

        client, mock_redis = _build_client()
        mock_redis.xrevrange.side_effect = redis.ConnectionError("lost")
        cam_id, frame_bytes = client.get_frame()
        assert cam_id is None
        assert frame_bytes is None
        assert client.is_connected is False

    def test_returns_none_none_on_generic_exception(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.side_effect = RuntimeError("unexpected")
        cam_id, frame_bytes = client.get_frame()
        assert cam_id is None
        assert frame_bytes is None

    def test_skips_fallback_frames(self) -> None:
        """MD-B1: the broker's "CAMERA OFFLINE" placeholder must never be returned."""
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(is_fallback=b"1")
        assert client.get_frame() == (None, None)

    def test_fallback_only_marks_client_disconnected(self) -> None:
        client, mock_redis = _build_client()
        assert client.is_connected is True
        mock_redis.xrevrange.return_value = _make_stream_message(is_fallback=b"1")
        client.get_frame()
        assert client.is_connected is False

    def test_returns_older_real_frame_behind_fallback(self) -> None:
        client, mock_redis = _build_client()
        newest = _make_stream_message(camera_id=b"cam0", is_fallback=b"1")
        older = _make_stream_message(camera_id=b"cam0", is_fallback=b"0")
        mock_redis.xrevrange.return_value = newest + older
        cam_id, frame_bytes = client.get_frame()
        assert cam_id == "cam0"
        assert frame_bytes

    def test_include_fallback_returns_placeholder(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(is_fallback=b"1")
        cam_id, frame_bytes = client.get_frame(include_fallback=True)
        assert cam_id == "cam0"
        assert frame_bytes

    def test_missing_is_fallback_field_treated_as_real(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message()
        assert client.get_frame()[0] == "cam0"

    def test_camera_id_filter_applied(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(camera_id=b"cam1")
        cam_id, _ = client.get_frame(camera_id="cam0")
        assert cam_id is None


# ---------------------------------------------------------------------------
# check_connection
# ---------------------------------------------------------------------------


class TestCheckConnection:
    def test_returns_true_when_ping_succeeds(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.ping.return_value = True
        assert client.check_connection() is True
        assert client.is_connected is True

    def test_returns_false_when_ping_raises(self) -> None:
        import redis

        client, mock_redis = _build_client()
        mock_redis.ping.side_effect = redis.ConnectionError("no redis")
        assert client.check_connection() is False
        assert client.is_connected is False


# ---------------------------------------------------------------------------
# close
# ---------------------------------------------------------------------------


class TestClose:
    def test_close_calls_redis_close(self) -> None:
        client, mock_redis = _build_client()
        client.close()
        mock_redis.close.assert_called_once()


# ---------------------------------------------------------------------------
# list_camera_ids (AD-B4)
# ---------------------------------------------------------------------------


def _messages(*camera_ids: bytes) -> list[Any]:
    """Several synthetic xrevrange entries, one per camera_id, newest first."""
    return [
        (f"{i}-0".encode(), _make_stream_message(camera_id=cid)[0][1]) for i, cid in enumerate(camera_ids)
    ]


class TestListCameraIds:
    def test_returns_distinct_camera_ids(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _messages(b"cam0", b"cam1", b"cam0", b"cam2")

        ids = client.list_camera_ids()

        assert ids == ["cam0", "cam1", "cam2"]

    def test_excludes_cameras_publishing_only_fallback(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(
            camera_id=b"down", is_fallback=b"1"
        ) + _make_stream_message(camera_id=b"up")
        assert client.list_camera_ids() == ["up"]

    def test_empty_stream_returns_empty_list(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = []
        assert client.list_camera_ids() == []

    def test_uses_count_argument(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = []
        client.list_camera_ids(count=50)
        mock_redis.xrevrange.assert_called_once_with(client.stream_name, count=50)

    def test_applies_cam_type_filter(self) -> None:
        client, mock_redis = _build_client(filter_cam_type="depth")
        mock_redis.xrevrange.return_value = _messages(b"cam0")  # default cam_type="color"
        assert client.list_camera_ids() == []

    def test_redis_error_returns_empty_list(self) -> None:
        import redis

        client, mock_redis = _build_client()
        mock_redis.xrevrange.side_effect = redis.ConnectionError("lost")
        assert client.list_camera_ids() == []


class TestGetFrameWithTimestamp:
    """The broker's capture time is returned so consumers can reject stale frames (review §1.2)."""

    def test_returns_the_message_timestamp_as_aware_utc(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(timestamp=b"2024-01-01T00:00:05+00:00")
        cam_id, frame_bytes, captured_at = client.get_frame_with_timestamp()
        assert cam_id == "cam0" and frame_bytes
        assert captured_at is not None and captured_at.isoformat() == "2024-01-01T00:00:05+00:00"

    def test_naive_timestamp_is_read_as_utc(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message(timestamp=b"2024-01-01T00:00:00")
        _, _, captured_at = client.get_frame_with_timestamp()
        assert captured_at is not None and captured_at.utcoffset().total_seconds() == 0

    def test_unparsable_or_missing_timestamp_is_none(self) -> None:
        client, mock_redis = _build_client()
        for raw in (b"not-a-date", b""):
            mock_redis.xrevrange.return_value = _make_stream_message(timestamp=raw)
            assert client.get_frame_with_timestamp()[2] is None

    def test_nothing_available_returns_three_nones(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = []
        assert client.get_frame_with_timestamp() == (None, None, None)

    def test_get_frame_still_returns_the_two_tuple(self) -> None:
        client, mock_redis = _build_client()
        mock_redis.xrevrange.return_value = _make_stream_message()
        assert len(client.get_frame()) == 2
