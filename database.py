"""Работа с SQLite: пользователи, версии прайса, расчёты, история изменений.

База создаётся в instance/calculator.db и хранится между перезапусками.
Существующая база никогда не удаляется при старте приложения.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime

from werkzeug.security import check_password_hash, generate_password_hash

import app_paths

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Путь к базе задаётся в app_paths (в собранном приложении — постоянная папка данных).
DB_PATH = app_paths.DB_PATH
INSTANCE_DIR = os.path.dirname(DB_PATH)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    login TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin', 'manager')),
    full_name TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS price_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version TEXT NOT NULL,
    loaded_at TEXT NOT NULL,
    filename TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS calculations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    number TEXT UNIQUE NOT NULL,
    created_at TEXT NOT NULL,
    user_id INTEGER REFERENCES users(id),
    user_login TEXT DEFAULT '',
    client_name TEXT DEFAULT '',
    client_phone TEXT DEFAULT '',
    client_email TEXT DEFAULT '',
    client_address TEXT DEFAULT '',
    client_source TEXT DEFAULT '',
    comment TEXT DEFAULT '',
    price_version_id INTEGER REFERENCES price_versions(id),
    price_version TEXT DEFAULT '',
    snapshot_json TEXT NOT NULL,
    subtotal TEXT DEFAULT '0',
    discount TEXT DEFAULT '0',
    total TEXT DEFAULT '0'
);

CREATE TABLE IF NOT EXISTS price_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    changed_at TEXT NOT NULL,
    user_login TEXT DEFAULT '',
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT DEFAULT '',
    new_value TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Идемпотентная миграция существующих таблиц (не удаляет данные)."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(calculations)")}
    new_columns = {
        # связь версии пересчёта с исходным расчётом (0 = исходный/обычный расчёт)
        "parent_id": "INTEGER DEFAULT 0",
        "version_no": "INTEGER DEFAULT 1",
        "recalculated_at": "TEXT DEFAULT ''",
        "recalc_user": "TEXT DEFAULT ''",
        "old_total": "TEXT DEFAULT ''",
        "new_total": "TEXT DEFAULT ''",
        "difference": "TEXT DEFAULT ''",
        "recalc_confirmed": "INTEGER DEFAULT 0",
    }
    for name, ddl in new_columns.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE calculations ADD COLUMN {name} {ddl}")


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


# ---------------------------------------------------------------- пользователи

def has_any_user() -> bool:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()
        return row["c"] > 0


def create_user(login: str, password: str, role: str, full_name: str = "") -> int:
    if role not in ("admin", "manager"):
        raise ValueError("Роль может быть только admin или manager")
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO users (login, password_hash, role, full_name, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (login, generate_password_hash(password), role, full_name,
             datetime.now().strftime("%d.%m.%Y %H:%M:%S")))
        return cur.lastrowid


def get_user_by_login(login: str):
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM users WHERE login = ?", (login,)).fetchone()
        return dict(row) if row else None


def verify_user(login: str, password: str):
    user = get_user_by_login(login)
    if not user or not user.get("active"):
        return None
    if check_password_hash(user["password_hash"], password):
        return user
    return None


def list_users() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, login, role, full_name, active, created_at FROM users ORDER BY id")
        return [dict(r) for r in rows]


def set_user_password(user_id: int, password: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                     (generate_password_hash(password), user_id))


def set_user_active(user_id: int, active: bool) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE users SET active = ? WHERE id = ?",
                     (1 if active else 0, user_id))


def set_user_role(user_id: int, role: str) -> None:
    if role not in ("admin", "manager"):
        raise ValueError("Недопустимая роль")
    with get_connection() as conn:
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))


# --------------------------------------------------------------- версии прайса

def save_price_version(price, comment: str = "") -> int:
    """Сохраняет неизменяемую копию прайса в SQLite. Возвращает id версии."""
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO price_versions (version, loaded_at, filename, comment, data_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (price.version, price.loaded_at, "", comment,
             json.dumps(price.to_dict(), ensure_ascii=False, default=str)))
        return cur.lastrowid


def latest_price_version() -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM price_versions ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None


def list_price_versions() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, version, loaded_at, filename, comment FROM price_versions "
            "ORDER BY id DESC")
        return [dict(r) for r in rows]


def get_price_version(version_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM price_versions WHERE id = ?",
                           (version_id,)).fetchone()
        return dict(row) if row else None


# ------------------------------------------------------------------- расчёты

def next_calculation_number() -> str:
    today = datetime.now().strftime("%Y%m%d")
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM calculations WHERE number LIKE ?",
            (f"КА-{today}-%",)).fetchone()
        return f"КА-{today}-{row['c'] + 1:04d}"


def save_calculation(payload: dict) -> int:
    """Сохраняет расчёт вместе с копией цен использованной версии прайса.

    Для обычного расчёта поля пересчёта не задаются (parent_id = 0).
    Для новой версии пересчёта передаются parent_id/version_no/...
    """
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO calculations (number, created_at, user_id, user_login, "
            "client_name, client_phone, client_email, client_address, client_source, "
            "comment, price_version_id, price_version, snapshot_json, subtotal, "
            "discount, total, parent_id, version_no, recalculated_at, recalc_user, "
            "old_total, new_total, difference, recalc_confirmed) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (payload["number"], payload["created_at"], payload.get("user_id"),
             payload.get("user_login", ""), payload.get("client_name", ""),
             payload.get("client_phone", ""), payload.get("client_email", ""),
             payload.get("client_address", ""), payload.get("client_source", ""),
             payload.get("comment", ""), payload.get("price_version_id"),
             payload.get("price_version", ""),
             json.dumps(payload["snapshot"], ensure_ascii=False, default=str),
             str(payload["subtotal"]), str(payload["discount"]), str(payload["total"]),
             int(payload.get("parent_id", 0) or 0),
             int(payload.get("version_no", 1) or 1),
             payload.get("recalculated_at", ""),
             payload.get("recalc_user", ""),
             str(payload.get("old_total", "")),
             str(payload.get("new_total", "")),
             str(payload.get("difference", "")),
             1 if payload.get("recalc_confirmed") else 0))
        return cur.lastrowid


def get_calculation_root_id(calc: dict) -> int:
    """Ид исходного расчёта цепочки версий (для обычного расчёта — его собственный id)."""
    root = calc
    guard = 0
    while root.get("parent_id") and guard < 100:
        parent = get_calculation(root["parent_id"])
        if not parent:
            break
        root = parent
        guard += 1
    return int(root["id"])


def list_calculation_versions(root_id: int, user_login: str | None = None) -> list[dict]:
    """Все версии одной цепочки: исходный расчёт + все пересчёты."""
    query = ("SELECT id, number, parent_id, version_no, created_at, recalculated_at, "
             "user_login, recalc_user, price_version, subtotal, discount, total, "
             "old_total, new_total, difference FROM calculations "
             "WHERE id = ? OR parent_id = ?")
    params: list = [root_id, root_id]
    if user_login:
        query += " AND user_login = ?"
        params.append(user_login)
    query += " ORDER BY version_no ASC, id ASC"
    with get_connection() as conn:
        rows = conn.execute(query, params)
        return [dict(r) for r in rows]


def get_calculation(calc_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM calculations WHERE id = ?",
                           (calc_id,)).fetchone()
        if not row:
            return None
        calc = dict(row)
        calc["snapshot"] = json.loads(calc["snapshot_json"])
        return calc


def list_calculations(user_login: str | None = None,
                      search: str | None = None) -> list[dict]:
    query = ("SELECT id, number, parent_id, version_no, created_at, user_login, "
             "client_name, client_phone, price_version, subtotal, discount, total "
             "FROM calculations")
    conditions, params = [], []
    if user_login:
        conditions.append("user_login = ?")
        params.append(user_login)
    if search:
        conditions.append("(number LIKE ? OR client_name LIKE ? OR created_at LIKE ?)")
        like = f"%{search}%"
        params.extend([like, like, like])
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY id DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params)
        return [dict(r) for r in rows]


def count_calculations() -> int:
    with get_connection() as conn:
        return conn.execute("SELECT COUNT(*) AS c FROM calculations").fetchone()["c"]


# --------------------------------------------------------- история изменений цен

def log_price_change(user_login: str, entity: str, entity_id: str,
                     field: str, old_value, new_value) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO price_changes (changed_at, user_login, entity, entity_id, "
            "field, old_value, new_value) VALUES (?,?,?,?,?,?,?)",
            (datetime.now().strftime("%d.%m.%Y %H:%M:%S"), user_login,
             entity, str(entity_id), field, str(old_value), str(new_value)))


def list_price_changes(limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM price_changes ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]


# --------------------------------------------------------------------- клиенты

def list_clients() -> list[dict]:
    """Клиенты выводятся из сохранённых расчётов (источник истины — история)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT client_name, client_phone, client_email, client_address, "
            "MAX(number) AS last_number, COUNT(*) AS orders "
            "FROM calculations WHERE client_name != '' "
            "GROUP BY client_name, client_phone ORDER BY MAX(id) DESC")
        return [dict(r) for r in rows]


# ----------------------------------------------------------------- настройки

def get_setting(key: str, default=None) -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))
