# -*- coding: utf-8 -*-
"""Тесты безопасного переноса данных (tools/migrate_data.py)."""

from __future__ import annotations

import importlib.util
import os

_TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools")
_spec = importlib.util.spec_from_file_location(
    "migrate_data", os.path.join(_TOOLS, "migrate_data.py"))
migrate_data = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(migrate_data)

SQLITE = b"SQLite format 3\x00" + b"payload"


def _make_source(tmp_path, name="project"):
    src = tmp_path / name
    (src / "instance").mkdir(parents=True)
    (src / "instance" / "calculator.db").write_bytes(SQLITE)
    (src / migrate_data.SEED_EXCEL_NAME).write_bytes(b"XLSX")
    return src


def test_resolve_source_db_from_project_layout(tmp_path):
    src = _make_source(tmp_path)
    assert migrate_data.resolve_source_db(str(src)) == str(src / "instance" / "calculator.db")


def test_migrate_copies_db_into_empty_target(tmp_path):
    src = _make_source(tmp_path)
    target = tmp_path / "data"
    report = migrate_data.migrate(str(src), str(target))
    assert report["ok"]
    assert (target / "calculator.db").read_bytes() == SQLITE
    # Источник не удалён и не изменён.
    assert (src / "instance" / "calculator.db").read_bytes() == SQLITE


def test_migrate_copies_excel_when_target_missing(tmp_path):
    src = _make_source(tmp_path)
    target = tmp_path / "data"
    migrate_data.migrate(str(src), str(target))
    assert (target / migrate_data.SEED_EXCEL_NAME).read_bytes() == b"XLSX"


def test_migrate_refuses_overwrite_without_force(tmp_path):
    src = _make_source(tmp_path)
    target = tmp_path / "data"
    target.mkdir()
    (target / "calculator.db").write_bytes(b"EXISTING")
    report = migrate_data.migrate(str(src), str(target))
    assert not report["ok"]
    assert (target / "calculator.db").read_bytes() == b"EXISTING"  # не перезаписано


def test_migrate_force_creates_backup_and_overwrites(tmp_path):
    src = _make_source(tmp_path)
    target = tmp_path / "data"
    target.mkdir()
    (target / "calculator.db").write_bytes(b"EXISTING")
    report = migrate_data.migrate(str(src), str(target), force=True)
    assert report["ok"]
    assert (target / "calculator.db").read_bytes() == SQLITE
    backups = [p for p in os.listdir(target) if ".backup-" in p]
    assert backups, "резервная копия не создана"
    assert (target / backups[0]).read_bytes() == b"EXISTING"


def test_migrate_rejects_non_sqlite_file(tmp_path):
    src = tmp_path / "project"
    (src / "instance").mkdir(parents=True)
    (src / "instance" / "calculator.db").write_bytes(b"not a database")
    report = migrate_data.migrate(str(src), str(tmp_path / "data"))
    assert not report["ok"]
    assert not (tmp_path / "data" / "calculator.db").exists()


def test_migrate_dry_run_changes_nothing(tmp_path):
    src = _make_source(tmp_path)
    target = tmp_path / "data"
    report = migrate_data.migrate(str(src), str(target), dry_run=True)
    assert report["ok"]
    assert not (target / "calculator.db").exists()
    assert not (target / migrate_data.SEED_EXCEL_NAME).exists()


def test_migrate_keeps_existing_excel_without_force(tmp_path):
    src = _make_source(tmp_path)
    target = tmp_path / "data"
    target.mkdir()
    (target / migrate_data.SEED_EXCEL_NAME).write_bytes(b"USER-EDITED")
    migrate_data.migrate(str(src), str(target))
    assert (target / migrate_data.SEED_EXCEL_NAME).read_bytes() == b"USER-EDITED"


def test_default_target_dir_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv(migrate_data.ENV_DATA_DIR, str(tmp_path / "custom"))
    assert migrate_data.default_target_dir() == str(tmp_path / "custom")


def test_default_target_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv(migrate_data.ENV_DATA_DIR, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    expected = os.path.join(str(tmp_path), migrate_data.APP_DIR_NAME)
    assert migrate_data.default_target_dir() == expected
