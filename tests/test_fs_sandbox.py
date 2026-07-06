from __future__ import annotations

import pytest

from backend.cookbook import config


def test_resolve_under_data_root_allows_child_path(isolated_home):
    result = config.resolve_under_data_root("downloads/model.bin")
    assert result == (config.CONFIG_DIR.resolve() / "downloads" / "model.bin")


def test_resolve_under_data_root_rejects_traversal_escape(isolated_home):
    with pytest.raises(ValueError):
        config.resolve_under_data_root("../../../../Temp/evil.bin")


def test_resolve_under_data_root_rejects_absolute_path(isolated_home):
    with pytest.raises(ValueError):
        config.resolve_under_data_root("C:/Windows/evil.dll")
