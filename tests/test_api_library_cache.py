import json
import os
import stat
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import api


def _configure_cache_paths(monkeypatch, tmp_path):
    package_api = tmp_path / "package" / "backend" / "api.py"
    shipped_cache = package_api.parent / "cookbook" / "data" / "library_cache.json"
    user_cache = tmp_path / "home" / ".model-hub" / "cache" / "library_cache.json"
    shipped_cache.parent.mkdir(parents=True)

    monkeypatch.setattr(api, "__file__", str(package_api))
    monkeypatch.setattr(api, "USER_LIBRARY_CACHE_PATH", user_cache, raising=False)
    monkeypatch.setattr(api, "SHIPPED_LIBRARY_CACHE_PATH", shipped_cache, raising=False)
    monkeypatch.setattr(api, "LIBRARY_CACHE", None)
    monkeypatch.setattr(api, "LIBRARY_CACHE_TIME", 0)
    monkeypatch.setattr(api, "LIBRARY_CACHE_REFRESHING", False)
    return user_cache, shipped_cache


def _write_cache(path: Path, model_name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched": time.time(), "models": [{"name": model_name}]}),
        encoding="utf-8",
    )


def _write_models(path: Path, models) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched": time.time(), "models": models}),
        encoding="utf-8",
    )


def test_scrape_writes_user_cache_atomically_without_mutating_shipped_cache(
    monkeypatch, tmp_path
):
    user_cache, shipped_cache = _configure_cache_paths(monkeypatch, tmp_path)
    shipped_payload = b'{"fetched": 1, "models": [{"name": "shipped"}]}'
    shipped_cache.write_bytes(shipped_payload)
    shipped_cache.chmod(stat.S_IREAD)

    html = b"""
    <a href="/library/refreshed" class="group w-full space-y-5">
      <p class="max-w-lg break-words text-neutral-800 text-md">Fresh model</p>
      <span x-test-capability>tools</span>
      <span x-test-size>7b</span>
      <span x-test-pull-count>42</span>
      <span x-test-tag-count>1</span>
    </a>
    """

    class FakeResponse:
        def read(self):
            return html

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_args, **_kwargs: FakeResponse())
    real_replace = os.replace
    replacements = []

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        return real_replace(source, destination)

    monkeypatch.setattr(api.os, "replace", recording_replace)
    try:
        models = api._scrape_library()
    finally:
        shipped_cache.chmod(stat.S_IREAD | stat.S_IWRITE)

    assert models == [
        {
            "name": "refreshed",
            "description": "Fresh model",
            "capabilities": ["tools"],
            "sizes": ["7b"],
            "pulls": "42",
            "tag_count": "1",
        }
    ]
    assert shipped_cache.read_bytes() == shipped_payload
    assert user_cache.is_file()
    assert json.loads(user_cache.read_text(encoding="utf-8"))["models"] == models
    assert replacements and replacements[-1][1] == user_cache
    assert set(user_cache.parent.iterdir()) == {user_cache}


def test_fetch_library_prefers_user_cache(monkeypatch, tmp_path):
    user_cache, shipped_cache = _configure_cache_paths(monkeypatch, tmp_path)
    _write_cache(user_cache, "user")
    _write_cache(shipped_cache, "shipped")
    monkeypatch.setattr(
        api,
        "_scrape_library",
        lambda: pytest.fail("fresh user cache must avoid a live scrape"),
    )

    assert api._fetch_library() == [{"name": "user"}]


def test_fetch_library_falls_back_to_shipped_cache(monkeypatch, tmp_path):
    user_cache, shipped_cache = _configure_cache_paths(monkeypatch, tmp_path)
    user_cache.parent.mkdir(parents=True)
    user_cache.write_text("not valid json", encoding="utf-8")
    _write_cache(shipped_cache, "shipped")
    shipped_payload = shipped_cache.read_bytes()
    monkeypatch.setattr(
        api,
        "_scrape_library",
        lambda: pytest.fail("shipped cold-start cache must avoid a live scrape"),
    )

    assert api._fetch_library() == [{"name": "shipped"}]
    assert shipped_cache.read_bytes() == shipped_payload


@pytest.mark.parametrize(
    "models",
    [
        [1],
        [{}],
        [{"name": 42}],
        [{"name": "   "}],
        [{"name": "valid"}, {"name": None}],
        [{"name": "valid", "description": []}],
        [{"name": "valid", "pulls": 42}],
        [{"name": "valid", "tag_count": 1}],
        [{"name": "valid", "capabilities": "tools"}],
        [{"name": "valid", "capabilities": ["tools", 1]}],
        [{"name": "valid", "sizes": "7b"}],
        [{"name": "valid", "sizes": ["7b", 13]}],
    ],
    ids=[
        "non-dict",
        "missing-name",
        "non-string-name",
        "blank-name",
        "mixed-invalid-row",
        "non-string-description",
        "non-string-pulls",
        "non-string-tag-count",
        "capabilities-not-list",
        "capabilities-non-string-member",
        "sizes-not-list",
        "sizes-non-string-member",
    ],
)
def test_semantically_malformed_user_cache_falls_back_to_shipped(
    monkeypatch, tmp_path, models
):
    user_cache, shipped_cache = _configure_cache_paths(monkeypatch, tmp_path)
    _write_models(user_cache, models)
    _write_cache(shipped_cache, "shipped")
    monkeypatch.setattr(
        api,
        "_scrape_library",
        lambda: pytest.fail("valid shipped fallback must avoid a live scrape"),
    )

    assert api._fetch_library() == [{"name": "shipped"}]


def test_user_cache_is_limited_to_scraper_fields_before_browse(monkeypatch, tmp_path):
    user_cache, shipped_cache = _configure_cache_paths(monkeypatch, tmp_path)
    _write_models(
        user_cache,
        [
            {
                "name": "user",
                "description": "User model",
                "capabilities": ["tools"],
                "sizes": ["7b"],
                "pulls": "42",
                "tag_count": "1",
                "display": 42,
                "untrusted": {"nested": "value"},
            }
        ],
    )
    _write_cache(shipped_cache, "shipped")
    monkeypatch.setattr(
        api,
        "detect",
        lambda: SimpleNamespace(total_vram_gb=0, gpus=[]),
    )
    monkeypatch.setattr(
        api,
        "_scrape_library",
        lambda: pytest.fail("fresh user cache must avoid a live scrape"),
    )

    response = api.app.test_client().get(
        "/api/library/browse?q=model&capability=tools&sort=name"
    )

    assert response.status_code == 200
    models = response.get_json()["models"]
    assert len(models) == 1
    assert models[0]["name"] == "user"
    assert "display" not in models[0]
    assert "untrusted" not in models[0]
