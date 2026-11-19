"""Unit tests for shared.core.image_paths — resolve_frame_image_path()."""

from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import pytest

from shared.core.image_paths import resolve_frame_image_path

pytestmark = pytest.mark.unit


def _frame(
    image_path: Optional[str], cropped_image_path: Optional[str], frame_id: int = 1
) -> SimpleNamespace:
    return SimpleNamespace(id=frame_id, image_path=image_path, cropped_image_path=cropped_image_path)


class TestResolveFrameImagePath:
    def test_prefers_cropped_when_it_exists(self, tmp_path: Path) -> None:
        full = tmp_path / "full.jpg"
        full.write_bytes(b"full")
        cropped = tmp_path / "cropped.jpg"
        cropped.write_bytes(b"cropped")

        path, kind = resolve_frame_image_path(_frame(str(full), str(cropped)))

        assert path == cropped
        assert kind == "roi"

    def test_falls_back_to_full_when_cropped_missing_on_disk(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        full = tmp_path / "full.jpg"
        full.write_bytes(b"full")
        missing_cropped = tmp_path / "does_not_exist.jpg"

        with caplog.at_level("WARNING"):
            path, kind = resolve_frame_image_path(_frame(str(full), str(missing_cropped)))

        assert path == full
        assert kind == "full"
        assert "falling back to full frame" in caplog.text

    def test_prefer_cropped_false_ignores_cropped_even_if_present(self, tmp_path: Path) -> None:
        full = tmp_path / "full.jpg"
        full.write_bytes(b"full")
        cropped = tmp_path / "cropped.jpg"
        cropped.write_bytes(b"cropped")

        path, kind = resolve_frame_image_path(_frame(str(full), str(cropped)), prefer_cropped=False)

        assert path == full
        assert kind == "full"

    def test_no_cropped_path_uses_full(self, tmp_path: Path) -> None:
        full = tmp_path / "full.jpg"
        full.write_bytes(b"full")

        path, kind = resolve_frame_image_path(_frame(str(full), None))

        assert path == full
        assert kind == "full"

    def test_neither_path_exists_on_disk_returns_none(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        missing = tmp_path / "missing.jpg"

        with caplog.at_level("WARNING"):
            path, kind = resolve_frame_image_path(_frame(str(missing), None))

        assert path is None
        assert kind == ""

    def test_no_image_path_at_all_returns_none(self) -> None:
        path, kind = resolve_frame_image_path(_frame(None, None))

        assert path is None
        assert kind == ""

    def test_cropped_missing_and_full_also_missing_returns_none(self, tmp_path: Path) -> None:
        missing_full = tmp_path / "missing_full.jpg"
        missing_cropped = tmp_path / "missing_cropped.jpg"

        path, kind = resolve_frame_image_path(_frame(str(missing_full), str(missing_cropped)))

        assert path is None
        assert kind == ""
