from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check_import_cycles.py"


def test_current_sources_have_no_import_cycles() -> None:
    result = subprocess.run([sys.executable, str(SCRIPT)], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Import cycles: 0" in result.stdout


def test_cycle_detector_reports_a_self_edge() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import check_import_cycles

    cycles = check_import_cycles.find_cycles({"a": {"a"}})
    assert cycles == [["a"]]
