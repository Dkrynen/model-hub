import runpy
from pathlib import Path, PurePosixPath

import PyInstaller.utils.hooks


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "build.spec"
BACKEND = ROOT / "backend"


def _spec_datas(monkeypatch) -> list[tuple[str, str]]:
    captured: dict[str, list[tuple[str, str]]] = {}

    class FakeAnalysis:
        def __init__(self, *args, **kwargs):
            self.pure = []
            self.zipped_data = []
            self.scripts = []
            self.binaries = []
            self.zipfiles = []
            self.datas = kwargs["datas"]
            captured["datas"] = self.datas

    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(
        PyInstaller.utils.hooks,
        "collect_all",
        lambda _package: ([], [], []),
    )
    runpy.run_path(
        str(SPEC),
        init_globals={
            "Analysis": FakeAnalysis,
            "PYZ": lambda *args, **kwargs: object(),
            "EXE": lambda *args, **kwargs: object(),
            "COLLECT": lambda *args, **kwargs: object(),
        },
    )
    return captured["datas"]


def test_backend_data_files_preserve_relative_paths_without_collisions(monkeypatch):
    datas = _spec_datas(monkeypatch)
    backend_rows = [
        (Path(source).resolve(), PurePosixPath(destination.replace("\\", "/")))
        for source, destination in datas
        if Path(source).resolve().is_relative_to(BACKEND.resolve())
    ]

    expected = {
        source.resolve(): PurePosixPath(source.parent.relative_to(ROOT).as_posix())
        for source in BACKEND.rglob("*")
        if source.suffix in {".py", ".json", ".txt"}
        and "__pycache__" not in source.parts
    }
    actual = {source: destination for source, destination in backend_rows}

    assert len(backend_rows) == len(expected)
    assert len({source for source, _destination in backend_rows}) == len(backend_rows)
    assert actual == expected

    packaged_paths = [destination / source.name for source, destination in backend_rows]
    assert len(packaged_paths) == len(set(packaged_paths))

    same_basename_sources = {
        source for source, _destination in backend_rows if source.name == "base.py"
    }
    same_basename_outputs = {
        destination / source.name
        for source, destination in backend_rows
        if source.name == "base.py"
    }
    assert len(same_basename_outputs) == len(same_basename_sources)
