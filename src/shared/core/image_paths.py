"""Helper for resolving a frame record's on-disk image path.

A frame record carries ``image_path`` and ``cropped_image_path``, written as absolute paths (built from
``ConfigLoader.get_storage_path()``), so resolving one is a ``Path()`` wrap plus an existence check and a
cropped-vs-full preference. Keeping that logic in one place avoids small behavioral differences between
callers.
"""

import logging
from pathlib import Path
from typing import Optional, Protocol, Tuple

logger = logging.getLogger(__name__)


class _FrameLike(Protocol):
    """Structural type: anything with an id and the two image-path columns.

    Deliberately not an ORM class — callers may pass a real
    ORM row, a lightweight stand-in in a test, or (as some call sites already
    do) a plain object built from a raw SQL row.
    """

    id: object
    image_path: Optional[str]
    cropped_image_path: Optional[str]


def resolve_frame_image_path(frame: _FrameLike, *, prefer_cropped: bool = True) -> Tuple[Optional[Path], str]:
    """Return the image path to use for *frame*, and which kind it is.

    With ``prefer_cropped=True`` (the default), returns the cropped/ROI image
    when ``cropped_image_path`` is set and exists on disk, logging a warning
    and falling back to the full frame if it's set but missing. Returns
    ``(None, "")`` if no usable image exists on disk.

    The returned kind string (``"roi"`` or ``"full"``) matches
    a dataset manifest's ``source_image_kind`` vocabulary.
    """
    if prefer_cropped and frame.cropped_image_path:
        roi_path = Path(frame.cropped_image_path)
        if roi_path.exists():
            return roi_path, "roi"
        logger.warning(
            "Cropped image path set but missing on disk for frame %s, falling back to full frame: %s",
            getattr(frame, "id", "?"),
            roi_path,
        )

    if not frame.image_path:
        return None, ""

    full_path = Path(frame.image_path)
    if not full_path.exists():
        logger.warning(
            "Image path set but missing on disk for frame %s: %s", getattr(frame, "id", "?"), full_path
        )
        return None, ""

    return full_path, "full"
