# -*- coding: utf-8 -*-
"""Тесты путей к данным, миграции старых данных и ресурсов (упаковка)."""

from __future__ import annotations

import os
import sys

import app_paths


def test_dev_data_dir_is_project_dir(monkeypatch):
    monkeypatch.delenv(app_paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setattr(app_paths, "is_frozen", lambda: False)
    assert app_paths.default_data_dir() == os.path.dirname(
        os.path.abspath(app_paths.__file__))


def test_env_override_wins(monkeypatch, tmp_path):
    monkeypatch.setenv(app_paths.ENV_DATA_DIR, str(tmp_path / "custom"))
    assert app_paths.default_data_dir() == os.path.abspath(str(tmp_path / "custom"))


def test_frozen_data_dir_uses_localappdata(monkeypatch, tmp_path):
    monkeypatch.delenv(app_paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_paths, "is_frozen", lambda: True)
    expected = os.path.join(str(tmp_path), app_paths.APP_DIR_NAME)
    assert app_paths.default_data_dir() == expected


def test_frozen_paths_place_mutable_data_in_data_dir(monkeypatch, tmp_path):
    monkeypatch.delenv(app_paths.ENV_DATA_DIR, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app_paths, "is_frozen", lambda: True)
    data_dir = app_paths.default_data_dir()
    # В собранном приложении база/прайс/экспорт — внутри папки данных,
    # а НЕ во временной папке распаковки.
    assert data_dir.startswith(str(tmp_path))
    assert app_paths.APP_DIR_NAME in data_dir


def test_migrate_legacy_db_copies_existing_db(monkeypatch, tmp_path):
    legacy = tmp_path / "old" / "instance"
    legacy.mkdir(parents=True)
    src = legacy / "calculator.db"
    src.write_bytes(b"SQLite format 3\x00OLD-DATA")

    target = tmp_path / "newdata" / "calculator.db"
    monkeypatch.setattr(app_paths, "DB_PATH", str(target))
    monkeypatch.setattr(app_paths, "legacy_db_candidates", lambda: [str(src)])

    copied = app_paths.migrate_legacy_db()
    assert copied == str(target)
    assert target.exists()
    assert target.read_bytes() == src.read_bytes()
    # Старая база НЕ удаляется.
    assert src.exists()


def test_migrate_legacy_db_never_overwrites(monkeypatch, tmp_path):
    target = tmp_path / "newdata" / "calculator.db"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"NEW-DATA")

    src = tmp_path / "old.db"
    src.write_bytes(b"OLD-DATA")

    monkeypatch.setattr(app_paths, "DB_PATH", str(target))
    monkeypatch.setattr(app_paths, "legacy_db_candidates", lambda: [str(src)])

    copied = app_paths.migrate_legacy_db()
    assert copied is None
    assert target.read_bytes() == b"NEW-DATA"


def test_seed_price_template_copies_from_resources(monkeypatch, tmp_path):
    res = tmp_path / "resources"
    res.mkdir()
    (res / app_paths.SEED_EXCEL_NAME).write_bytes(b"XLSX-SEED")

    target = tmp_path / "data" / app_paths.SEED_EXCEL_NAME
    monkeypatch.setattr(app_paths, "EXCEL_PATH", str(target))
    monkeypatch.setattr(app_paths, "resource_dir", lambda: str(res))

    seeded = app_paths.seed_price_template()
    assert seeded == str(target)
    assert target.read_bytes() == b"XLSX-SEED"
    # Исходный шаблон в ресурсах не изменяется.
    assert (res / app_paths.SEED_EXCEL_NAME).read_bytes() == b"XLSX-SEED"


def test_seed_price_template_keeps_existing_working_copy(monkeypatch, tmp_path):
    res = tmp_path / "resources"
    res.mkdir()
    (res / app_paths.SEED_EXCEL_NAME).write_bytes(b"SEED")

    target = tmp_path / "data" / app_paths.SEED_EXCEL_NAME
    target.parent.mkdir(parents=True)
    target.write_bytes(b"USER-EDITED")

    monkeypatch.setattr(app_paths, "EXCEL_PATH", str(target))
    monkeypatch.setattr(app_paths, "resource_dir", lambda: str(res))

    assert app_paths.seed_price_template() is None
    assert target.read_bytes() == b"USER-EDITED"


def test_ensure_data_dir_creates_all_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(app_paths, "DB_PATH", str(tmp_path / "data" / "calculator.db"))
    monkeypatch.setattr(app_paths, "EXCEL_PATH", str(tmp_path / "data" / "p.xlsx"))
    monkeypatch.setattr(app_paths, "EXPORT_DIR", str(tmp_path / "data" / "exports"))
    monkeypatch.setattr(app_paths, "YANDEX_TMP_DIR", str(tmp_path / "data" / "ya"))
    monkeypatch.setattr(app_paths, "LOG_DIR", str(tmp_path / "data" / "logs"))
    monkeypatch.setattr(app_paths, "legacy_db_candidates", lambda: [])

    result = app_paths.ensure_data_dir()
    assert result == str(tmp_path / "data")
    for folder in ("data", "data/exports", "data/ya", "data/logs"):
        assert (tmp_path / folder).is_dir()


def test_resource_dir_frozen_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path / "meipass"), raising=False)
    assert app_paths.resource_dir() == str(tmp_path / "meipass")


def test_legacy_db_candidates_do_not_use_cwd(monkeypatch, tmp_path):
    cwd = tmp_path / "cwd"
    appdir = tmp_path / "elsewhere" / "app"
    cwd.mkdir(parents=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setattr(app_paths, "executable_dir", lambda: str(appdir))
    candidates = [os.path.abspath(c) for c in app_paths.legacy_db_candidates()]
    assert candidates  # по-прежнему ищем рядом с приложением
    for c in candidates:
        assert not c.startswith(os.path.abspath(str(cwd)) + os.sep)
    assert os.path.join(os.path.abspath(str(cwd)), "instance", "calculator.db") not in candidates


def test_migrate_legacy_db_ignores_cwd(monkeypatch, tmp_path):
    """Посторонняя база в текущей рабочей папке НЕ должна подхватываться."""
    workdir = tmp_path / "workdir"
    (workdir / "instance").mkdir(parents=True)
    (workdir / "instance" / "calculator.db").write_bytes(b"SQLite format 3\x00CWD")
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(app_paths, "executable_dir", lambda: str(tmp_path / "appdir"))

    target = tmp_path / "data" / "calculator.db"
    monkeypatch.setattr(app_paths, "DB_PATH", str(target))

    assert app_paths.migrate_legacy_db() is None
    assert not target.exists()


def test_migrate_legacy_db_from_exe_dir_still_works(monkeypatch, tmp_path):
    appdir = tmp_path / "appdir"
    (appdir / "instance").mkdir(parents=True)
    (appdir / "instance" / "calculator.db").write_bytes(b"SQLite format 3\x00EXE")
    monkeypatch.setattr(app_paths, "executable_dir", lambda: str(appdir))

    target = tmp_path / "data" / "calculator.db"
    monkeypatch.setattr(app_paths, "DB_PATH", str(target))

    copied = app_paths.migrate_legacy_db()
    assert copied == str(target)
    assert target.read_bytes() == b"SQLite format 3\x00EXE"
