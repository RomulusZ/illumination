"""CLI tests; the stub path never calls the network."""

import json
from pathlib import Path

from illumination.ui.cli import main


def test_run_with_stub_writes_result(tmp_path: Path) -> None:
    exit_code = main(
        [
            "run",
            "--objective",
            "验证骨架链路",
            "--out",
            str(tmp_path),
            "--stub",
        ]
    )

    assert exit_code == 0
    result_payload = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert result_payload["status"] == "partial"
    assert len(result_payload["artifacts"]) == 1


def test_run_without_stub_and_key_fails(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    exit_code = main(["run", "--objective", "demo", "--out", str(tmp_path)])
    assert exit_code == 1
