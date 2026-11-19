"""The ``camera_frames`` Redis stream message: one owner for its fields.

``my_depthai``'s broker publishes these messages and consumers (``shared.camera_service`` and any other
reader) decode them. This module owns the wire format so the producer and every consumer agree on it, and
``is_inspectable_frame`` gives consumers a single check for "may this go to inference / decode".

Pure Python (no redis, numpy or cv2), so any module can import it.

Wire format (Redis stream fields, all values bytes):

====================  =======================================================================
``camera_id``         the config *key* (e.g. ``camera_1``), not the device id
``cam_type``          camera component type (``color``, ``left``, ``right``)
``output_type``       ``preview`` / ``video`` / ``fullvideo`` / ``still``
``timestamp``         UTC ISO-8601
``frame``             JPEG bytes
``shape``             ``"h,w,c"`` of the *source* frame before JPEG encoding
``encoding``          ``jpeg``
``is_fallback``       ``1`` for the broker's synthetic "CAMERA OFFLINE" placeholder, else ``0``
====================  =======================================================================

Consumers must treat a missing ``is_fallback`` as ``0`` (older brokers did not send it).

Versioning (``WIRE_FORMAT_VERSION``)
------------------------------------

The producer (``mydepthai``'s broker) and its consumers are separate packages that other projects pin
independently, so this format is a contract with a version. The version is **not** sent in the message
and no consumer needs it to decode a frame: it names the table above, so a project can say "wire format 1"
instead of a commit.

* Compatible, no version bump: adding a field. Consumers ignore fields they don't know
  (``decode_frame_message`` does), and a producer that omits a new field must still be readable by a
  consumer that expects it.
* Incompatible, bumps ``WIRE_FORMAT_VERSION``: removing or renaming a field, changing its type, or changing
  what a value means. State it in the changelog below and in the release notes of both ``tep-shared`` and
  ``mydepthai``, and release the producer and the consumers together.

Changelog:

* **1**: the eight fields above. ``is_fallback`` was added after the first brokers, so it is read as
  optional; every other field is always sent.
"""

from datetime import datetime, timezone
from typing import Dict, Mapping, Optional, Tuple, TypedDict, Union

#: The version of the message contract described in the module docstring. Not sent on the wire.
WIRE_FORMAT_VERSION = 1

SUPPORTED_ENCODING = "jpeg"


class CameraFrameMessage(TypedDict):
    """A decoded ``camera_frames`` stream entry."""

    camera_id: str
    cam_type: str
    output_type: str
    timestamp: str
    frame: bytes
    shape: Optional[Tuple[int, int, int]]
    encoding: str
    is_fallback: bool


def encode_frame_message(
    *,
    camera_id: str,
    cam_type: str,
    output_type: str,
    frame: bytes,
    shape: Tuple[int, int, int],
    is_fallback: bool = False,
    encoding: str = SUPPORTED_ENCODING,
    timestamp: Optional[str] = None,
) -> Dict[str, bytes]:
    """Build the field dict to ``XADD``. ``timestamp`` defaults to now (UTC ISO-8601)."""
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).isoformat()
    height, width, channels = shape
    return {
        "camera_id": camera_id.encode("utf-8"),
        "cam_type": cam_type.encode("utf-8"),
        "output_type": output_type.encode("utf-8"),
        "timestamp": timestamp.encode("utf-8"),
        "frame": frame,
        "shape": f"{height},{width},{channels}".encode("utf-8"),
        "encoding": encoding.encode("utf-8"),
        "is_fallback": b"1" if is_fallback else b"0",
    }


def _parse_shape(raw: bytes) -> Optional[Tuple[int, int, int]]:
    try:
        parts = tuple(int(p) for p in raw.decode("utf-8").split(","))
    except (ValueError, UnicodeDecodeError):
        return None
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def decode_frame_message(data: Union[Mapping[bytes, bytes], Mapping[str, bytes]]) -> CameraFrameMessage:
    """Decode a stream entry's fields. Accepts bytes or str keys; never raises on missing fields.

    Missing fields take the same defaults the hand-written decoders used: ``camera_id``
    ``"unknown"``, ``encoding`` ``"jpeg"``, ``is_fallback`` ``False`` (a missing flag means a
    real frame), everything else empty. Check :func:`is_inspectable_frame` before using
    ``frame``.
    """
    fields: Dict[str, bytes] = {
        (k.decode("utf-8") if isinstance(k, bytes) else k): v for k, v in data.items()
    }
    return CameraFrameMessage(
        camera_id=fields.get("camera_id", b"unknown").decode("utf-8"),
        cam_type=fields.get("cam_type", b"").decode("utf-8"),
        output_type=fields.get("output_type", b"").decode("utf-8"),
        timestamp=fields.get("timestamp", b"").decode("utf-8"),
        frame=fields.get("frame", b""),
        shape=_parse_shape(fields.get("shape", b"")),
        encoding=fields.get("encoding", b"jpeg").decode("utf-8"),
        is_fallback=fields.get("is_fallback", b"0") == b"1",
    )


def is_inspectable_frame(message: CameraFrameMessage) -> bool:
    """True if the frame is a real, decodable capture (not a placeholder, empty or non-JPEG).

    This is the single gate for anomaly detection and barcode decode. Live-view callers that
    want to *show* the placeholder should not use it.
    """
    return bool(message["frame"]) and not message["is_fallback"] and message["encoding"] == SUPPORTED_ENCODING


def parse_frame_timestamp(raw: str) -> Optional[datetime]:
    """Parse a message's ISO-8601 ``timestamp`` to an aware UTC datetime; ``None`` if absent/invalid.

    A naive timestamp is read as UTC, which is what the broker writes.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
