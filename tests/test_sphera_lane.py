"""Sphera stays closed. The status names the gap and never a value from the licence file."""

from __future__ import annotations

from pathlib import Path

from grit.acquisition import sphera_lane


def test_a_missing_file_is_not_configured(tmp_path) -> None:
    assert "missing" in sphera_lane.sphera_status(tmp_path / "sphera.env")


def test_an_empty_placeholder_waits_for_a_licence(tmp_path) -> None:
    path = tmp_path / "sphera.env"
    path.write_text(sphera_lane.sphera_env_example(), encoding="utf-8")
    assert "waiting for a licence" in sphera_lane.sphera_status(path)


def test_a_filled_file_is_not_called_and_its_values_stay_out_of_the_status(tmp_path) -> None:
    path = tmp_path / "sphera.env"
    path.write_text("SPHERA_API_URL=https://example.invalid/api\nSPHERA_API_KEY=super-secret-value\n", encoding="utf-8")
    status = sphera_lane.sphera_status(path)
    assert "no API contract" in status
    assert "super-secret-value" not in status and "example.invalid" not in status
    assert "requests" not in Path(sphera_lane.__file__).read_text(encoding="utf-8")
