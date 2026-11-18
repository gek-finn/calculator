# -*- coding: utf-8 -*-
"""Явный (ручной) перенос существующих данных в постоянную папку приложения.

Скрипт запускается ТОЛЬКО вручную и никогда не выполняется приложением
автоматически. Он переносит базу SQLite и рабочий Excel-прайс из старого
расположения (например, из папки исходного проекта) в постоянную папку данных
пользователя ``%LOCALAPPDATA%\\PartitionCalculator``.

Безопасность:
* по умолчанию существующая база в целевой папке НЕ перезаписывается;
* при явном ``--force`` перед перезаписью создаётся резервная копия с меткой
  времени (``*.backup-ГГГГММДД-ЧЧММСС``);
* исходные файлы НИКОГДА не удаляются и не изменяются;
* источник проверяется на признаки базы SQLite.

Примеры:
  # посмотреть, что будет сделано (без изменений)
  python tools/migrate_data.py --from "E:\\vibecoding\\calculator" --dry-run

  # перенести в %LOCALAPPDATA%\\PartitionCalculator (существующую базу не трогать)
  python tools/migrate_data.py --from "E:\\vibecoding\\calculator"

  # перенести в свою папку данных, при необходимости перезаписав (с бэкапом)
  python tools/migrate_data.py --from D:\\old --data-dir D:\\data --force
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime

SQLITE_MAGIC = b"SQLite format 3\x00"
SEED_EXCEL_NAME = "данные_калькулятора.xlsx"
APP_DIR_NAME = "PartitionCalculator"
ENV_DATA_DIR = "CALCULATOR_DATA_DIR"


def sqlite_looks_valid(path: str) -> bool:
    """Проверяет, что файл похож на базу SQLite (по заголовку)."""
    try:
        with open(path, "rb") as fh:
            return fh.read(16) == SQLITE_MAGIC
    except OSError:
        return False


def default_target_dir() -> str:
    """Постоянная папка данных, которую использует собранное приложение."""
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return os.path.abspath(env)
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_DIR_NAME)


def resolve_source_db(source: str) -> str | None:
    """Находит существующую базу в источнике (папка или прямой путь к .db)."""
    if os.path.isfile(source):
        return os.path.abspath(source)
    for rel in (os.path.join("instance", "calculator.db"), "calculator.db"):
        candidate = os.path.join(source, rel)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return None


def resolve_source_excel(source: str) -> str | None:
    """Находит рабочий Excel-прайс в источнике."""
    if os.path.isfile(source):
        base = os.path.dirname(source)
    else:
        base = source
    candidate = os.path.join(base, SEED_EXCEL_NAME)
    return os.path.abspath(candidate) if os.path.isfile(candidate) else None


def _timestamp_backup(path: str, dry_run: bool) -> str | None:
    if not os.path.exists(path):
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"{path}.backup-{stamp}"
    if not dry_run:
        shutil.copy2(path, backup)
    return backup


def _copy_db(src: str, dst: str, dry_run: bool) -> None:
    if dry_run:
        return
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copy2(src, dst)
    # Переносим и сопутствующие журналы SQLite, если они есть.
    for suffix in ("-wal", "-shm"):
        side = src + suffix
        if os.path.exists(side):
            shutil.copy2(side, dst + suffix)


def migrate(source: str, target_dir: str, *, force: bool = False,
            with_excel: bool = True, dry_run: bool = False) -> dict:
    """Выполняет перенос. Возвращает отчёт о действиях (dict)."""
    source = os.path.abspath(source)
    target_dir = os.path.abspath(target_dir)
    report: dict = {
        "source": source, "target_dir": target_dir,
        "dry_run": dry_run, "actions": [], "errors": [], "ok": True,
    }

    def act(msg: str) -> None:
        report["actions"].append(msg)

    def err(msg: str) -> None:
        report["errors"].append(msg)
        report["ok"] = False

    if not os.path.exists(source):
        err(f"Источник не найден: {source}")
        return report

    # Проверяем, не совпадают ли источник и цель (перенос «в себя»).
    same_tree = os.path.abspath(os.path.dirname(target_dir)) == os.path.abspath(source)

    db_src = resolve_source_db(source)
    db_dst = os.path.join(target_dir, "calculator.db")
    if db_src:
        if os.path.abspath(db_src) == os.path.abspath(db_dst):
            act("База уже находится в целевой папке — ничего не делаем.")
        elif not sqlite_looks_valid(db_src):
            err(f"Файл не похож на базу SQLite: {db_src}")
        elif os.path.exists(db_dst) and not force:
            err(f"В целевой папке уже есть база: {db_dst}. "
                "Перезапись не выполнена (используйте --force, будет создан бэкап).")
        else:
            backup = _timestamp_backup(db_dst, dry_run) if os.path.exists(db_dst) else None
            if backup:
                act(f"Резервная копия существующей базы: {backup}")
            _copy_db(db_src, db_dst, dry_run)
            act(f"База: {db_src} -> {db_dst}")
    else:
        if not same_tree:
            act("База в источнике не найдена — перенос базы не требуется.")
        else:
            act("Источник совпадает с целью — база остаётся на месте.")

    if with_excel:
        xls_src = resolve_source_excel(source)
        xls_dst = os.path.join(target_dir, SEED_EXCEL_NAME)
        if xls_src:
            if os.path.abspath(xls_src) == os.path.abspath(xls_dst):
                act("Прайс уже находится в целевой папке — ничего не делаем.")
            elif os.path.exists(xls_dst) and not force:
                act(f"Прайс в целевой папке уже есть ({xls_dst}) — не перезаписываем.")
            else:
                backup = _timestamp_backup(xls_dst, dry_run) if os.path.exists(xls_dst) else None
                if backup:
                    act(f"Резервная копия существующего прайса: {backup}")
                if not dry_run:
                    os.makedirs(target_dir, exist_ok=True)
                    shutil.copy2(xls_src, xls_dst)
                act(f"Прайс: {xls_src} -> {xls_dst}")
        else:
            act("Excel-прайс в источнике не найден — пропускаем.")

    return report


def _print_report(report: dict) -> None:
    prefix = "[ТЕСТ] " if report["dry_run"] else ""
    print(f"{prefix}Источник: {report['source']}")
    print(f"{prefix}Папка данных: {report['target_dir']}")
    for line in report["actions"]:
        print(f"{prefix}  - {line}")
    for line in report["errors"]:
        print(f"{prefix}  [!] {line}")
    print(f"{prefix}Итог: {'успешно' if report['ok'] else 'есть проблемы'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Перенос базы и Excel-прайса в постоянную папку данных.")
    parser.add_argument("--from", dest="source", required=True,
                        help="Папка исходного проекта или путь к файлу calculator.db")
    parser.add_argument("--data-dir", dest="target", default=None,
                        help="Целевая папка данных (по умолчанию "
                             "%LOCALAPPDATA%\\PartitionCalculator или CALCULATOR_DATA_DIR)")
    parser.add_argument("--force", action="store_true",
                        help="Перезаписать существующую базу/прайс (с резервной копией)")
    parser.add_argument("--no-excel", dest="with_excel", action="store_false",
                        help="Не переносить Excel-прайс")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только показать действия, ничего не менять")
    args = parser.parse_args(argv)

    target = args.target or default_target_dir()
    report = migrate(args.source, target, force=args.force,
                     with_excel=args.with_excel, dry_run=args.dry_run)
    _print_report(report)
    if not report["ok"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
