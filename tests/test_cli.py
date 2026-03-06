from __future__ import annotations

from dynaforge.cli import main


def test_cli_help_returns_zero(capsys) -> None:
    exit_code = main(["--help"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Usage:" in captured.out


def test_cli_returns_nonzero_on_failed_run() -> None:
    exit_code = main(["executor.allow_offline_fallback=false"])

    assert exit_code == 1
