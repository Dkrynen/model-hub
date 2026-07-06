# tests/test_boundary_no_lac_pro_import.py
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAT = re.compile(r"^\s*(import\s+lac_pro|from\s+lac_pro)\b", re.M)


def test_model_hub_never_imports_lac_pro():
    offenders = []
    for f in list((ROOT / "backend").rglob("*.py")) + [ROOT / "server.py", ROOT / "cli.py"]:
        if "__pycache__" in f.parts:
            continue
        if PAT.search(f.read_text(encoding="utf-8")):
            offenders.append(str(f.relative_to(ROOT)))
    assert offenders == [], f"open-core must never import lac_pro: {offenders}"
