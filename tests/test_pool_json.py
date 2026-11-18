"""Pool JSON cells: blank recovered values must not crash the UI."""

from __future__ import annotations

from pathlib import Path

from grit.pool.db import Pool, _loads_json, _row


def test_blank_json_cells_become_schema_shaped_empties() -> None:
    assert _loads_json("config_json", "") == {}
    assert _loads_json("config_json", "   ") == {}
    assert _loads_json("themes_json", "") == []
    assert _loads_json("condition_ids_json", "") == []
    assert _loads_json("config_json", '{"a": 1}') == {"a": 1}


def test_pool_reads_a_run_with_blank_config_json(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    pool.start_run("R1", run_date="2026-11-18", pack="ste", kind="READER_SCAN", config={"keep": True})
    pool.conn.execute("UPDATE runs SET config_json = '' WHERE run_id = ?", ("R1",))
    pool.conn.commit()
    row = pool.run("R1")
    assert row is not None
    assert row["config"] == {}
    pool.close()


def test_row_helper_strips_json_suffix_for_blank_list_columns(tmp_path: Path) -> None:
    pool = Pool(tmp_path / "grit.sqlite")
    pool.conn.execute(
        "CREATE TABLE IF NOT EXISTS _json_probe (themes_json TEXT NOT NULL, config_json TEXT NOT NULL)"
    )
    pool.conn.execute("INSERT INTO _json_probe (themes_json, config_json) VALUES ('', '')")
    pool.conn.commit()
    raw = pool.conn.execute("SELECT themes_json, config_json FROM _json_probe").fetchone()
    assert _row(raw) == {"themes": [], "config": {}}
    pool.close()
