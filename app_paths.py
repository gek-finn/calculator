# -*- coding: utf-8 -*-
"""Единое определение путей к ресурсам и к пользовательским данным.

Зачем это нужно для портативной сборки (PyInstaller):

* РЕСУРСЫ (шаблоны, статика, иконка, исходный Excel-шаблон) поставляются вместе с
  приложением и доступны только для чтения. В собранном виде они лежат во
  временной папке распаковки ``sys._MEIPASS``.
* ДАННЫЕ (база SQLite, рабочий Excel-прайс, экспорт, журналы) должны быть
  изменяемыми и НЕ теряться при обновлении приложения, поэтому в собранном виде
  они хранятся в постоянной папке ``%LOCALAPPDATA%\\PartitionCalculator``.

При запуске из исходников (разработка/тесты) раскладка остаётся прежней:
база в ``instance/calculator.db``, прайс в корне проекта — чтобы ничего не
ломать в уже существующем окружении.

Папку данных можно переопределить переменной окружения ``CALCULATOR_DATA_DIR``.
"""

from __future__ import annotations

import os
import shutil
import sys

APP_DIR_NAME = "PartitionCalculator"
APP_DISPLAY_NAME = "Калькулятор «ЛидерПро»"
ENV_DATA_DIR = "CALCULATOR_DATA_DIR"
SEED_EXCEL_NAME = "данные_калькулятора.xlsx"
DEFAULT_PORT = 5000


def is_frozen() -> bool:
    """True, если приложение запущено как собранный PyInstaller-EXE."""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """Папка только для чтения с ресурсами приложения (шаблоны, статика, Excel-шаблон)."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def executable_dir() -> str:
    """Папка, в которой лежит EXE (в собранном виде) или сам проект (в разработке)."""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def default_data_dir() -> str:
    """Постоянная папка пользовательских данных."""
    env = os.environ.get(ENV_DATA_DIR)
    if env:
        return os.path.abspath(env)
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP_DIR_NAME)
    return os.path.dirname(os.path.abspath(__file__))


DATA_DIR = default_data_dir()


def _p(*parts: str) -> str:
    return os.path.join(DATA_DIR, *parts)


if is_frozen():
    DB_PATH = _p("calculator.db")
    EXCEL_PATH = _p(SEED_EXCEL_NAME)
    EXPORT_DIR = _p("exports")
    YANDEX_TMP_DIR = _p("yandex_imports")
    LOG_DIR = _p("logs")
else:
    DB_PATH = _p("instance", "calculator.db")
    EXCEL_PATH = _p(SEED_EXCEL_NAME)
    EXPORT_DIR = _p("exports")
    YANDEX_TMP_DIR = _p("instance", "yandex_imports")
    LOG_DIR = _p("logs")


def legacy_db_candidates() -> list[str]:
    """Возможные места старой базы РЯДОМ С ПРИЛОЖЕНИЕМ (для переноса данных).

    Текущая рабочая папка (cwd) намеренно НЕ просматривается: при двойном
    щелчке она совпадает с папкой EXE, а в остальных случаях могла бы
    незаметно подхватить постороннюю базу из папки запуска. Явный перенос
    данных выполняет tools/migrate_data.py.
    """
    exe = executable_dir()
    return [
        os.path.join(exe, "instance", "calculator.db"),
        os.path.join(exe, "calculator.db"),
    ]


def migrate_legacy_db() -> str | None:
    """Если базы в DATA_DIR ещё нет, но есть старая рядом — копирует её.

    Существующая база НИКОГДА не перезаписывается. Возвращает путь к
    скопированной базе или None.
    """
    if os.path.exists(DB_PATH):
        return None
    target = os.path.abspath(DB_PATH)
    for candidate in legacy_db_candidates():
        if not candidate:
            continue
        src = os.path.abspath(candidate)
        if src == target or not os.path.exists(src):
            continue
        try:
            os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
            shutil.copy2(src, DB_PATH)
            return DB_PATH
        except OSError:
            continue
    return None


def seed_price_template() -> str | None:
    """Кладёт исходный Excel-шаблон в папку данных при первом запуске.

    Исходный (поставляемый) шаблон при обычной работе не изменяется — правки
    идут только в рабочую копию внутри папки данных.
    """
    if os.path.exists(EXCEL_PATH):
        return None
    seed = os.path.join(resource_dir(), SEED_EXCEL_NAME)
    if os.path.exists(seed) and os.path.abspath(seed) != os.path.abspath(EXCEL_PATH):
        try:
            os.makedirs(os.path.dirname(EXCEL_PATH), exist_ok=True)
            shutil.copy2(seed, EXCEL_PATH)
            return EXCEL_PATH
        except OSError:
            return None
    return None


def ensure_data_dir() -> str:
    """Создаёт папку данных (идемпотентно) и переносит старые данные, если есть."""
    os.makedirs(DATA_DIR, exist_ok=True)
    for folder in (os.path.dirname(DB_PATH), EXPORT_DIR, YANDEX_TMP_DIR, LOG_DIR):
        os.makedirs(folder, exist_ok=True)
    migrate_legacy_db()
    seed_price_template()
    return DATA_DIR
