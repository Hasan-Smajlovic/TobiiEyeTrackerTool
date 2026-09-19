from __future__ import annotations

import pytest
from PySide6.QtGui import QImage

from scripts.capture_ui import capture_ui


@pytest.mark.e2e
def test_all_ui_preview_surfaces_render(qapp, tmp_path):
    snapshots = capture_ui(tmp_path, width=1280, height=720)

    assert len(snapshots) == 19
    assert (tmp_path / "index.html").is_file()
    for snapshot in snapshots:
        image = QImage(str(snapshot))
        assert not image.isNull(), snapshot.name
        assert image.width() >= 380, snapshot.name
        assert image.height() >= 70, snapshot.name
