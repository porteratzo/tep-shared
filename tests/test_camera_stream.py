"""Unit tests for shared.core.camera_stream (the ``camera_frames`` message owner)."""

import pytest

from shared.core.camera_stream import (
    CameraFrameMessage,
    decode_frame_message,
    encode_frame_message,
    is_inspectable_frame,
)

pytestmark = pytest.mark.unit

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16


def _encode(**overrides: object) -> dict:
    kwargs: dict = dict(
        camera_id="camera_1",
        cam_type="color",
        output_type="preview",
        frame=JPEG,
        shape=(48, 64, 3),
        timestamp="2026-10-12T00:00:00+00:00",
    )
    kwargs.update(overrides)
    return encode_frame_message(**kwargs)


def _as_wire(fields: dict) -> dict:
    """What Redis hands back with decode_responses=False: bytes keys."""
    return {k.encode(): v for k, v in fields.items()}


def test_encode_produces_exactly_the_documented_fields() -> None:
    assert _encode() == {
        "camera_id": b"camera_1",
        "cam_type": b"color",
        "output_type": b"preview",
        "timestamp": b"2026-10-12T00:00:00+00:00",
        "frame": JPEG,
        "shape": b"48,64,3",
        "encoding": b"jpeg",
        "is_fallback": b"0",
    }


def test_round_trip_through_bytes_keys() -> None:
    msg = decode_frame_message(_as_wire(_encode(is_fallback=True)))
    assert msg == CameraFrameMessage(
        camera_id="camera_1",
        cam_type="color",
        output_type="preview",
        timestamp="2026-10-12T00:00:00+00:00",
        frame=JPEG,
        shape=(48, 64, 3),
        encoding="jpeg",
        is_fallback=True,
    )


def test_decode_accepts_str_keys() -> None:
    assert decode_frame_message(_encode())["camera_id"] == "camera_1"


def test_timestamp_defaults_to_utc_now() -> None:
    ts = decode_frame_message(_encode(timestamp=None))["timestamp"]
    assert ts.endswith("+00:00")


def test_missing_is_fallback_means_real_frame() -> None:
    wire = _as_wire(_encode())
    del wire[b"is_fallback"]
    msg = decode_frame_message(wire)
    assert msg["is_fallback"] is False
    assert is_inspectable_frame(msg)


def test_missing_fields_use_legacy_defaults() -> None:
    msg = decode_frame_message({})
    assert msg["camera_id"] == "unknown"
    assert msg["encoding"] == "jpeg"
    assert msg["frame"] == b""
    assert msg["shape"] is None
    assert msg["is_fallback"] is False


@pytest.mark.parametrize("raw", [b"", b"48,64", b"a,b,c", b"1,2,3,4", b"\xff\xfe"])
def test_malformed_shape_decodes_to_none(raw: bytes) -> None:
    wire = _as_wire(_encode())
    wire[b"shape"] = raw
    assert decode_frame_message(wire)["shape"] is None


def test_fallback_frame_is_not_inspectable() -> None:
    assert not is_inspectable_frame(decode_frame_message(_as_wire(_encode(is_fallback=True))))


def test_empty_frame_is_not_inspectable() -> None:
    assert not is_inspectable_frame(decode_frame_message(_as_wire(_encode(frame=b""))))


def test_non_jpeg_frame_is_not_inspectable() -> None:
    assert not is_inspectable_frame(decode_frame_message(_as_wire(_encode(encoding="png"))))


def test_real_frame_is_inspectable() -> None:
    assert is_inspectable_frame(decode_frame_message(_as_wire(_encode())))


# ── Wire-format contract (versioned: see the module docstring) ─────────────────────────────────────────


def test_documented_fields_are_exactly_the_fields_the_encoder_sends() -> None:
    """The docstring table is the contract other projects read; it must not drift from the encoder."""
    import re

    from shared.core import camera_stream

    documented = set(re.findall(r"^``(\w+)``\s{2,}\S", camera_stream.__doc__ or "", flags=re.MULTILINE))
    assert documented == set(_encode())


def test_wire_format_version_is_an_int_and_not_sent_on_the_wire() -> None:
    from shared.core.camera_stream import WIRE_FORMAT_VERSION

    assert isinstance(WIRE_FORMAT_VERSION, int) and WIRE_FORMAT_VERSION >= 1
    assert "wire_version" not in _encode() and "version" not in _encode()


def test_decoder_ignores_fields_it_does_not_know() -> None:
    """Adding a field is a compatible change: an older consumer must still decode the message."""
    fields = _as_wire(_encode())
    fields[b"added_in_a_later_release"] = b"anything"
    message = decode_frame_message(fields)
    assert message["camera_id"] == "camera_1" and message["frame"] == JPEG
    assert "added_in_a_later_release" not in message
