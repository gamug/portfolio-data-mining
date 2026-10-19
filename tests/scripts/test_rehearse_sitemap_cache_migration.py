"""scripts/rehearse_sitemap_cache_migration.py: the rehearsal runs on a copy and never writes the source."""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from pathlib import Path

import pytest
from portfolio_common.db import Database

from data_mining.schema import apply_schema, rollback_sitemap_cache, run_migrations

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "rehearse_sitemap_cache_migration.py"
_spec = importlib.util.spec_from_file_location("rehearse_sitemap_cache_migration", _SCRIPT)
assert _spec is not None
assert _spec.loader is not None
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def _version_3_file(path: Path) -> None:
    db = Database(sqlite3.connect(path))
    apply_schema(db)
    run_migrations(db)  # as both stages do at start-up: `idx_match_class` is added there
    db.execute(
        "INSERT INTO discovered_urls (url, domain, company, ticker, source, discovered_at) "
        "VALUES ('https://www.cnbc.com/2024/05/01/a.html', 'cnbc.com', 'KLA', 'KLAC', "
        "'sitemap', '2026-10-19T00:00:00')"
    )
    db.commit()
    rollback_sitemap_cache(db)
    db.commit()
    db.close()


def test_the_rehearsal_passes_and_leaves_the_source_byte_identical(tmp_path: Path) -> None:
    source, copy = tmp_path / "urls.db", tmp_path / "copy.db"
    _version_3_file(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    lines: list[str] = []

    assert mod.rehearse(source, copy, say=lines.append) is True

    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert copy.exists()
    assert not any(line.startswith("FAIL") for line in lines)
    assert any("rollback: no cache object left" in line for line in lines)


def test_the_rehearsal_refuses_an_existing_copy_and_the_source_itself(tmp_path: Path) -> None:
    source = tmp_path / "urls.db"
    _version_3_file(source)
    existing = tmp_path / "copy.db"
    existing.write_bytes(b"keep me")
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        mod.copy_read_only(source, existing)
    assert existing.read_bytes() == b"keep me"
    with pytest.raises(SystemExit):
        mod.copy_read_only(source, source)
