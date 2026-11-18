# -*- coding: utf-8 -*-
"""Flask-приложение локального калькулятора «ЛидерПро» (учебная версия).

Маршруты: авторизация, расчёт, история, прайс, админ-панель,
пересчёт сохранённого расчёта по актуальному прайсу (новая версия),
экспорт расчёта в PDF/XLSX из снимка конкретной версии.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime
from decimal import Decimal
from functools import wraps

from flask import (Flask, flash, jsonify, redirect, render_template, request,
                   send_file, session, url_for)

import app_paths
import database as db
import export_service
import price_diff
import price_service
import recalc
import yandex_disk
from calculator import PriceData, calculate_quote, calculate_structure

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Ресурсы только для чтения (шаблоны/статика) и изменяемые данные — это разные
# папки: в собранном приложении ресурсы распаковываются во временную папку, а
# база, прайс, экспорт и журналы лежат в постоянной папке пользователя.
RESOURCE_DIR = app_paths.resource_dir()
EXCEL_PATH = app_paths.EXCEL_PATH
EXPORT_DIR = app_paths.EXPORT_DIR

# Обновление прайса с Яндекс Диска. Адрес источника фиксирован конфигурацией
# и НЕ принимается от пользователя. Обновление выполняется ТОЛЬКО по кнопке.
YANDEX_PRICE_PUBLIC_URL = "https://disk.yandex.ru/d/YOUR_PUBLIC_PRICE_LINK"
YANDEX_MAX_BYTES = 20 * 1024 * 1024   # предел размера скачиваемого файла
YANDEX_DOWNLOAD_TIMEOUT = 60          # тайм-аут скачивания файла, секунды
YANDEX_API_TIMEOUT = 20               # тайм-аут запросов к API, секунды
YANDEX_TMP_DIR = app_paths.YANDEX_TMP_DIR


def _load_secret_key() -> str:
    """Ключ сессий. В собранном приложении — постоянный, чтобы не выходить
    из системы после каждого перезапуска. В разработке — случайный."""
    if app_paths.is_frozen():
        path = os.path.join(app_paths.DATA_DIR, "secret.key")
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as fh:
                    key = fh.read().strip()
                    if key:
                        return key
            key = secrets.token_hex(32)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(key)
            return key
        except OSError:
            pass
    return secrets.token_hex(32)


app = Flask(__name__,
            template_folder=os.path.join(RESOURCE_DIR, "templates"),
            static_folder=os.path.join(RESOURCE_DIR, "static"))
app.config["SECRET_KEY"] = _load_secret_key()
app.config["TEMPLATES_AUTO_RELOAD"] = True


@app.after_request
def _no_store(resp):
    """Запрещаем браузеру кэшировать страницы приложения.

    Иначе после обновления кода в браузере может остаться устаревшая страница
    (например, без новых кнопок). Статику кэшировать разрешено.
    """
    if not request.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp

# Текущая рабочая версия прайса (в памяти процесса) и её id в SQLite.
CURRENT_PRICE: PriceData | None = None
CURRENT_PRICE_VERSION_ID: int | None = None
# Предупреждения последней загрузки прайса (для страницы «Прайс»).
LAST_PRICE_WARNINGS: list[dict] = []

# Скачанный с Яндекс Диска, но ещё НЕ применённый прайс (ожидает подтверждения).
# Рабочие цены до подтверждения не меняются.
YANDEX_STAGED: dict | None = None
# Замок: не даём запустить две операции обновления прайса одновременно.
YANDEX_LOCK = threading.Lock()


# ============================================================ вспомогательное

def _to_jsonable(obj):
    """Decimal/date в строки, остальное — рекурсивно. Для jsonify."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(v) for v in obj]
    return obj


def _price_json(price: PriceData | None) -> dict:
    return price.to_dict() if price else {}


def _bump_version(version: str) -> str:
    """1.2 → 1.3; «без_версии» → «без_версии.1»."""
    match = re.match(r"^(.*?)(\d+)$", str(version))
    if match:
        return f"{match.group(1)}{int(match.group(2)) + 1}"
    return f"{version}.1"


def load_latest_price_into_memory() -> None:
    """Восстанавливает рабочий прайс из последней версии в SQLite."""
    global CURRENT_PRICE, CURRENT_PRICE_VERSION_ID
    latest = db.latest_price_version()
    if not latest:
        return
    try:
        CURRENT_PRICE = PriceData.from_dict(json.loads(latest["data_json"]))
        CURRENT_PRICE_VERSION_ID = latest["id"]
    except Exception:  # noqa: BLE001
        CURRENT_PRICE = None
        CURRENT_PRICE_VERSION_ID = None


def ensure_app_ready() -> None:
    """Инициализация базы (с миграцией) и восстановление прайса из истории."""
    app_paths.ensure_data_dir()
    db.init_db()
    if not os.path.exists(EXCEL_PATH):
        try:
            price_service.create_sample_workbook(EXCEL_PATH)
        except Exception:  # noqa: BLE001
            pass
    load_latest_price_into_memory()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if session.get("role") != "admin":
            flash("Раздел доступен только администратору.", "error")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return wrapper


def _can_access_calc(calc: dict | None) -> bool:
    """Администратор — все расчёты; менеджер — только свои."""
    if not calc:
        return False
    if session.get("role") == "admin":
        return True
    return calc.get("user_login") == session.get("login")


def _access_error_response():
    return jsonify({"ok": False, "errors": [
        {"field": "access", "blocking": True,
         "message": "Нет прав на этот расчёт."}]}), 403


# ============================================================ авторизация

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if db.has_any_user():
        return redirect(url_for("login"))
    if request.method == "POST":
        login = request.form.get("login", "").strip()
        password = request.form.get("password", "")
        password2 = request.form.get("password2", "")
        full_name = request.form.get("full_name", "").strip()
        if not login:
            flash("Укажите логин.", "error")
        elif len(password) < 6:
            flash("Пароль должен содержать минимум 6 символов.", "error")
        elif password != password2:
            flash("Пароли не совпадают.", "error")
        else:
            db.create_user(login, password, "admin", full_name)
            flash("Администратор создан. Теперь войдите в систему.", "success")
            return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = db.verify_user(request.form.get("login", "").strip(),
                              request.form.get("password", ""))
        if not user:
            flash("Неверный логин или пароль.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            session["login"] = user["login"]
            session["role"] = user["role"]
            session["full_name"] = user["full_name"]
            return redirect(url_for("index"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ============================================================ главная страница

@app.route("/")
def index():
    if not db.has_any_user():
        return redirect(url_for("setup"))
    if not session.get("user_id"):
        return redirect(url_for("login"))
    return render_template("index.html",
                           price_loaded=CURRENT_PRICE is not None,
                           price=_price_json(CURRENT_PRICE))


# ============================================================ прайс

@app.route("/price/load", methods=["POST"])
@login_required
def price_load():
    global CURRENT_PRICE, CURRENT_PRICE_VERSION_ID, LAST_PRICE_WARNINGS
    price, errors, _stats = price_service.load_price_from_file(EXCEL_PATH)
    if price is None:
        session["price_load_errors"] = errors
        flash("Прайс не загружен: исправьте ошибки в Excel-файле.", "error")
        return redirect(url_for("index"))
    CURRENT_PRICE = price
    CURRENT_PRICE_VERSION_ID = db.save_price_version(price, comment="Загрузка из Excel")
    LAST_PRICE_WARNINGS = [e for e in errors if not e.get("blocking")]
    if LAST_PRICE_WARNINGS:
        session["price_load_warnings"] = LAST_PRICE_WARNINGS
    flash(f"Прайс версии {price.version} загружен: конструкций "
          f"{len(price.constructions)}, стекла {len(price.glasses)}, "
          f"фурнитуры {len(price.hardware)}, услуг {len(price.services)}.", "success")
    return redirect(url_for("index"))


@app.route("/price")
@login_required
def price_view():
    if CURRENT_PRICE is None:
        flash("Прайс ещё не загружен — нажмите «Загрузить данные».", "error")
        return redirect(url_for("index"))
    return render_template("price.html", price=_price_json(CURRENT_PRICE),
                           warnings=LAST_PRICE_WARNINGS,
                           is_admin=session.get("role") == "admin")


# ============================================================ прайс с Яндекс Диска

def _cleanup_staged() -> None:
    """Удаляет временный скачанный файл и сбрасывает состояние ожидания."""
    global YANDEX_STAGED
    if YANDEX_STAGED:
        path = YANDEX_STAGED.get("path")
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    YANDEX_STAGED = None


def _yandex_download_and_stage() -> dict:
    """Скачивает прайс с Яндекс Диска во временную папку (без записи в БД).

    Возвращает {"ok": True} или {"ok": False, "message": ...}. Рабочие цены
    не меняются: файл только скачивается и ждёт подтверждения пользователя.
    """
    global YANDEX_STAGED
    _cleanup_staged()
    try:
        info = yandex_disk.get_price_file(
            YANDEX_PRICE_PUBLIC_URL, YANDEX_TMP_DIR,
            max_bytes=YANDEX_MAX_BYTES, timeout=YANDEX_DOWNLOAD_TIMEOUT,
            api_timeout=YANDEX_API_TIMEOUT)
    except yandex_disk.YandexDiskError as exc:
        app.logger.warning("Не удалось получить прайс с Яндекс Диска: %s", exc.kind)
        return {"ok": False, "message": exc.message}
    except Exception:  # noqa: BLE001 — не показываем пользователю трассировку
        app.logger.exception("Непредвиденная ошибка получения прайса с Яндекс Диска")
        return {"ok": False, "message": "Не удалось получить файл с Яндекс Диска. "
                                        "Подробности записаны в журнал сервера."}
    YANDEX_STAGED = {**info, "token": secrets.token_hex(16)}
    return {"ok": True}


def _apply_price_from_staged(price: PriceData, staged: dict, diff: dict,
                             login: str) -> tuple[bool, str]:
    """Атомарно применяет проверенный прайс: Excel + новая версия в SQLite.

    При любой ошибке действующий прайс остаётся прежним (Excel восстанавливается
    из резервной копии, в памяти ничего не меняется).
    """
    global CURRENT_PRICE, CURRENT_PRICE_VERSION_ID
    old_version = CURRENT_PRICE.version if CURRENT_PRICE else ""
    try:
        result = price_service.install_price_file(
            staged["path"], EXCEL_PATH, create_backup=True)
    except Exception:  # noqa: BLE001
        app.logger.exception("Не удалось записать Excel-прайс")
        return False, ("Не удалось сохранить Excel-прайс. "
                       "Действующий прайс не изменён.")
    backup = result.get("backup", "")
    try:
        version_id = db.save_price_version(
            price, comment=f"Импорт с Яндекс Диска: {staged.get('filename', '')}")
    except Exception:  # noqa: BLE001
        app.logger.exception("Не удалось сохранить версию прайса")
        if backup and os.path.exists(backup):
            try:
                shutil.copy2(backup, EXCEL_PATH)
            except OSError:
                pass
        return False, ("Не удалось сохранить новую версию прайса. "
                       "Действующий прайс не изменён.")

    CURRENT_PRICE = price
    CURRENT_PRICE_VERSION_ID = version_id
    try:
        db.log_price_change(login, "прайс", staged.get("filename", ""),
                            "версия_прайса", old_version, price.version)
    except Exception:  # noqa: BLE001
        pass
    counts = diff.get("counts", {})
    _cleanup_staged()
    return True, (f"Прайс успешно обновлён: версия {price.version}. "
                  f"Позиций {counts.get('total', 0)}, изменено "
                  f"{counts.get('changed_positions', 0)}, новых "
                  f"{counts.get('new', 0)}, отсутствующих "
                  f"{counts.get('missing', 0)}. Старые расчёты не изменялись.")


@app.route("/price/yandex/download", methods=["POST"])
@login_required
def yandex_price_download():
    """Кнопка «Получить актуальный прайс с Яндекс Диска» (только скачивание)."""
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    if not YANDEX_LOCK.acquire(blocking=False):
        message = "Обновление прайса уже выполняется. Дождитесь завершения."
        if wants_json:
            return jsonify({"ok": False, "message": message})
        flash(message, "error")
        return redirect(url_for("index"))
    try:
        result = _yandex_download_and_stage()
    finally:
        YANDEX_LOCK.release()

    if result["ok"]:
        if wants_json:
            return jsonify({"ok": True,
                            "redirect": url_for("yandex_price_preview")})
        return redirect(url_for("yandex_price_preview"))

    if wants_json:
        return jsonify({"ok": False, "message": result["message"]})
    flash(result["message"], "error")
    return redirect(url_for("index"))


@app.route("/price/yandex/preview")
@login_required
def yandex_price_preview():
    """Предварительный просмотр: сравнение до применения (без записи в БД)."""
    staged = YANDEX_STAGED
    if not staged:
        flash("Сначала получите файл с Яндекс Диска.", "error")
        return redirect(url_for("index"))

    data = None
    price = None
    errors: list[dict] = []
    try:
        data = price_service.read_workbook_rows(staged["path"])
        errors = price_service.validate_rows(data)
        blocking = [e for e in errors if e.get("blocking")]
        if not blocking:
            price = price_service.build_price_data(
                data, loaded_at=staged.get("downloaded_at"))
    except ValueError as exc:
        errors = [{"sheet": "-", "row": None, "message": str(exc),
                   "blocking": True}]

    blocking = [e for e in errors if e.get("blocking")]
    warnings = [e for e in errors if not e.get("blocking")]
    unmapped = price_diff.find_unmapped(data)
    diff = price_diff.compare_prices(CURRENT_PRICE, price) if price else None
    file_version = ""
    for rec in (data or {}).get("Правила", []):
        if str(rec.get("ключ", "")).strip() == "версия_прайса":
            value = rec.get("значение")
            file_version = "" if value is None else str(value).strip()
    return render_template(
        "yandex_preview.html", staged=staged, errors=errors, blocking=blocking,
        warnings=warnings, unmapped=unmapped, diff=diff,
        current_price=_price_json(CURRENT_PRICE),
        current_version=CURRENT_PRICE.version if CURRENT_PRICE else "",
        file_version=file_version,
        is_admin=session.get("role") == "admin")


@app.route("/price/yandex/apply", methods=["POST"])
@login_required
def yandex_price_apply():
    """Подтверждение: применяет проверенный прайс и создаёт новую версию."""
    token = request.form.get("token", "")
    ack_missing = request.form.get("ack_missing") == "on"
    if not YANDEX_LOCK.acquire(blocking=False):
        flash("Обновление прайса уже выполняется. Дождитесь завершения.", "error")
        return redirect(url_for("yandex_price_preview"))
    try:
        staged = YANDEX_STAGED
        if not staged or staged.get("token") != token:
            flash("Сессия обновления устарела. Получите файл заново.", "error")
            return redirect(url_for("index"))

        # Повторная проверка прямо перед записью (файл мог стать недоступен).
        price, errors, _stats = price_service.load_price_from_file(staged["path"])
        if price is None:
            flash("Файл не прошёл проверку — обновление отменено, "
                  "действующий прайс не изменён.", "error")
            return redirect(url_for("yandex_price_preview"))

        diff = price_diff.compare_prices(CURRENT_PRICE, price)
        if diff["counts"]["missing"] and not ack_missing:
            flash("В новом файле есть отсутствующие позиции. Поставьте галочку "
                  "подтверждения, чтобы продолжить (позиции не будут обнулены).",
                  "error")
            return redirect(url_for("yandex_price_preview"))

        ok, message = _apply_price_from_staged(price, staged, diff,
                                               session["login"])
        flash(message, "success" if ok else "error")
        return redirect(url_for("price_view") if ok else
                        url_for("yandex_price_preview"))
    finally:
        YANDEX_LOCK.release()


@app.route("/price/yandex/cancel", methods=["POST"])
@login_required
def yandex_price_cancel():
    """Отмена обновления: временный файл удаляется, прайс не меняется."""
    _cleanup_staged()
    flash("Обновление отменено, действующий прайс не изменён.", "success")
    return redirect(url_for("price_view") if CURRENT_PRICE else url_for("index"))


@app.route("/api/price")
@login_required
def api_price():
    if CURRENT_PRICE is None:
        return jsonify({"ok": False, "error": "Прайс не загружен"}), 404
    return jsonify(_to_jsonable(_price_json(CURRENT_PRICE)))


# ============================================================ расчёт

@app.route("/quote/preview_structure", methods=["POST"])
@login_required
def quote_preview_structure():
    if CURRENT_PRICE is None:
        return jsonify({"ok": False, "errors": [
            {"field": "price", "blocking": True,
             "message": "Прайс не загружен — нажмите «Загрузить данные»."}]})
    data = request.get_json(silent=True) or {}
    res = calculate_structure(CURRENT_PRICE, data)
    return jsonify(_to_jsonable({
        "ok": not any(e.get("blocking") for e in res["errors"]),
        "area_total": res["area_total"],
        "subtotal": res["subtotal"],
        "errors": res["errors"],
    }))


@app.route("/quote/calculate", methods=["POST"])
@login_required
def quote_calculate():
    if CURRENT_PRICE is None:
        return jsonify({"ok": False, "errors": [
            {"field": "price", "blocking": True,
             "message": "Прайс не загружен — нажмите «Загрузить данные»."}]}), 400
    data = request.get_json(silent=True) or {}
    quote = calculate_quote(CURRENT_PRICE, data,
                            is_admin=session.get("role") == "admin")
    return jsonify(_to_jsonable(quote))


@app.route("/quote/save", methods=["POST"])
@login_required
def quote_save():
    if CURRENT_PRICE is None:
        return jsonify({"ok": False, "errors": [
            {"field": "price", "blocking": True,
             "message": "Прайс не загружен — нажмите «Загрузить данные»."}]}), 400
    data = request.get_json(silent=True) or {}
    quote = calculate_quote(CURRENT_PRICE, data,
                            is_admin=session.get("role") == "admin")
    if not quote["ok"]:
        return jsonify(_to_jsonable({"ok": False, "errors": quote["errors"]}))
    client = data.get("client", {}) or {}
    payload = {
        "number": db.next_calculation_number(),
        "created_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "user_id": session["user_id"],
        "user_login": session["login"],
        "client_name": client.get("name", ""),
        "client_phone": client.get("phone", ""),
        "client_email": client.get("email", ""),
        "client_address": client.get("address", ""),
        "client_source": client.get("source", ""),
        "comment": client.get("comment", ""),
        "price_version_id": CURRENT_PRICE_VERSION_ID,
        "price_version": CURRENT_PRICE.version,
        "snapshot": {
            "quote": quote,
            "client": client,
            "input": data,
            "price": _price_json(CURRENT_PRICE),
        },
        "subtotal": str(quote["subtotal"]),
        "discount": str(quote["discount"]),
        "total": str(quote["total"]),
    }
    calc_id = db.save_calculation(payload)
    return jsonify({"ok": True, "calc_id": calc_id, "number": payload["number"]})


# ============================================================ история расчётов

@app.route("/calculations")
@login_required
def calculations():
    search = request.args.get("q", "").strip()
    user_login = None if session.get("role") == "admin" else session.get("login")
    items = db.list_calculations(user_login=user_login, search=search or None)
    return render_template("calculations.html", items=items, search=search)


@app.route("/calculations/<int:calc_id>")
@login_required
def calculation_view(calc_id):
    calc = db.get_calculation(calc_id)
    if not _can_access_calc(calc):
        flash("Расчёт не найден или нет прав доступа.", "error")
        return redirect(url_for("calculations"))
    root_id = db.get_calculation_root_id(calc)
    versions = db.list_calculation_versions(
        root_id, None if session.get("role") == "admin" else session.get("login"))
    # Предупреждения: прайс обновился или позиции отсутствуют в текущем прайсе.
    warnings = []
    latest = db.latest_price_version()
    if latest and calc.get("price_version") and \
            latest["version"] != calc.get("price_version"):
        warnings.append(
            f"Прайс обновился: сейчас версия {latest['version']}, расчёт выполнен "
            f"по версии {calc.get('price_version')}. Итоги расчёта не изменились — "
            "они зафиксированы в снимке.")
    if CURRENT_PRICE is not None:
        for m in recalc.detect_missing(calc, CURRENT_PRICE):
            if m["required"]:
                warnings.append(
                    f"«{m['name']}» — не найдена в текущем прайсе ({m['reason']}). "
                    "При пересчёте потребуется выбрать замену.")
    return render_template("calculation_view.html", calc=calc,
                           versions=versions, warnings=warnings)


@app.route("/calculations/<int:calc_id>/versions")
@login_required
def calc_versions(calc_id):
    calc = db.get_calculation(calc_id)
    if not _can_access_calc(calc):
        flash("Нет прав на этот расчёт.", "error")
        return redirect(url_for("calculations"))
    root_id = db.get_calculation_root_id(calc)
    versions = db.list_calculation_versions(
        root_id, None if session.get("role") == "admin" else session.get("login"))
    return render_template("recalc_history.html", versions=versions,
                           current_id=calc_id, calc=calc)


# ============================================================ пересчёт

def _resolve_recalc_price(source: str):
    """Источник прайса для пересчёта.

    Возвращает (PriceData | None, version_id | None, errors).
    version_id = None означает, что версию нужно сохранить в SQLite
    (только для варианта «проверить Excel»).
    """
    if source == "excel":
        if not os.path.exists(EXCEL_PATH):
            return None, None, [{"field": "price_source", "blocking": True,
                                 "message": "Excel-файл прайса не найден."}]
        price, errors, _stats = price_service.load_price_from_file(EXCEL_PATH)
        if price is None:
            return None, None, [{"field": "price_source", "blocking": True,
                                 "message": "Excel-прайс не прошёл проверку — "
                                            "пересчёт отменён, старый расчёт не изменён.",
                                 "details": errors}]
        return price, None, []
    # последняя успешно загруженная версия прайса
    if CURRENT_PRICE is not None:
        return CURRENT_PRICE, CURRENT_PRICE_VERSION_ID, []
    latest = db.latest_price_version()
    if latest:
        try:
            return PriceData.from_dict(json.loads(latest["data_json"])), latest["id"], []
        except Exception:  # noqa: BLE001
            pass
    return None, None, [{"field": "price_source", "blocking": True,
                         "message": "Нет загруженной версии прайса — "
                                    "нажмите «Загрузить данные»."}]


@app.route("/calculations/<int:calc_id>/recalc")
@login_required
def recalc_calc(calc_id):
    calc = db.get_calculation(calc_id)
    if not _can_access_calc(calc):
        flash("Нет прав пересчитывать этот расчёт.", "error")
        return redirect(url_for("calculations"))
    return render_template("recalc.html", calc=calc, calc_id=calc_id)


@app.route("/calculations/<int:calc_id>/recalc/preview", methods=["POST"])
@login_required
def recalc_preview(calc_id):
    calc = db.get_calculation(calc_id)
    if not _can_access_calc(calc):
        return _access_error_response()
    data = request.get_json(silent=True) or {}
    new_price, _version_id, errors = _resolve_recalc_price(
        data.get("price_source") or "current")
    if new_price is None:
        return jsonify({"ok": False, "errors": errors})
    analysis = recalc.analyze(calc, new_price,
                              replacements=data.get("replacements") or {},
                              discount_type=data.get("discount_type") or None,
                              discount_value=data.get("discount_value") or None,
                              is_admin=session.get("role") == "admin")
    return jsonify(_to_jsonable({
        "ok": analysis["ok"],
        "errors": analysis["errors"],
        "missing": analysis["missing"],
        "unresolved_missing": analysis["unresolved_missing"],
        "warnings": analysis["warnings"],
        "comparison": analysis["comparison"] or [],
        "price_changes": analysis["price_changes"],
        "old_total": (analysis["old_quote"] or {}).get("total"),
        "new_total": (analysis["new_quote"] or {}).get("total")
        if analysis["new_quote"] else None,
        "price_version": analysis["price_version"],
    }))


@app.route("/calculations/<int:calc_id>/recalc/confirm", methods=["POST"])
@login_required
def recalc_confirm(calc_id):
    calc = db.get_calculation(calc_id)
    if not _can_access_calc(calc):
        return _access_error_response()
    data = request.get_json(silent=True) or {}

    # Пересчёт выполняется ТОЛЬКО после явного подтверждения пользователем.
    if data.get("confirm") not in (True, "1", 1, "true"):
        return jsonify({"ok": False, "errors": [
            {"field": "confirm", "blocking": True,
             "message": "Пересчёт не подтверждён: сравните версии и подтвердите "
                        "действие явно."}]})

    new_price, version_id, errors = _resolve_recalc_price(
        data.get("price_source") or "current")
    if new_price is None:
        return jsonify({"ok": False, "errors": errors})

    analysis = recalc.analyze(calc, new_price,
                              replacements=data.get("replacements") or {},
                              discount_type=data.get("discount_type") or None,
                              discount_value=data.get("discount_value") or None,
                              is_admin=session.get("role") == "admin")
    if not analysis["ok"]:
        return jsonify({"ok": False, "errors": analysis["errors"],
                        "warnings": analysis["warnings"]})

    # Изменения правил/единиц требуют отдельного явного подтверждения.
    if analysis["warnings"] and data.get("ack_warnings") not in (True, "1", 1, "true"):
        return jsonify({"ok": False, "warnings": analysis["warnings"], "errors": [
            {"field": "ack_warnings", "blocking": True,
             "message": "Есть предупреждения об изменениях в прайсе — отметьте "
                        "«Я ознакомлен с предупреждениями»."}]})

    quote = analysis["new_quote"]
    old_quote = analysis["old_quote"] or {}
    old_total = str(old_quote.get("total", "0") or "0")
    new_total = str(quote["total"])
    try:
        difference = str(Decimal(new_total) - Decimal(old_total))
    except Exception:  # noqa: BLE001
        difference = ""

    if version_id is None:
        # Проверенный Excel-прайс фиксируется как новая неизменяемая версия.
        version_id = db.save_price_version(
            new_price, comment=f"Использован при пересчёте расчёта № {calc['number']}")

    root_id = db.get_calculation_root_id(calc)
    versions = db.list_calculation_versions(root_id)
    version_no = (max((v.get("version_no") or 1) for v in versions)
                  if versions else 1) + 1
    root = db.get_calculation(root_id) or calc
    now = datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    payload = {
        "number": f"{root['number']} (v{version_no})",
        "created_at": now,
        "user_id": session["user_id"],
        "user_login": session["login"],
        "client_name": calc.get("client_name", ""),
        "client_phone": calc.get("client_phone", ""),
        "client_email": calc.get("client_email", ""),
        "client_address": calc.get("client_address", ""),
        "client_source": calc.get("client_source", ""),
        "comment": calc.get("comment", ""),
        "price_version_id": version_id,
        "price_version": new_price.version,
        "snapshot": {
            "quote": quote,
            "client": (calc.get("snapshot") or {}).get("client", {}) or {},
            "input": analysis["payload"],
            "price": _price_json(new_price),
        },
        "subtotal": str(quote["subtotal"]),
        "discount": str(quote["discount"]),
        "total": new_total,
        "parent_id": root_id,
        "version_no": version_no,
        "recalculated_at": now,
        "recalc_user": session["login"],
        "old_total": old_total,
        "new_total": new_total,
        "difference": difference,
        "recalc_confirmed": True,
    }
    new_id = db.save_calculation(payload)
    return jsonify({"ok": True, "calc_id": new_id, "number": root["number"],
                    "version_no": version_no})


# ============================================================ экспорт расчёта

@app.route("/calculations/<int:calc_id>/export/<fmt>")
@login_required
def export_calc(calc_id, fmt):
    calc = db.get_calculation(calc_id)
    if not _can_access_calc(calc):
        flash("Нет прав на этот расчёт.", "error")
        return redirect(url_for("calculations"))
    fmt = fmt.lower()
    try:
        if fmt == "pdf":
            path = export_service.export_pdf(calc, EXPORT_DIR)
            mime = "application/pdf"
        elif fmt == "xlsx":
            path = export_service.export_xlsx(calc, EXPORT_DIR)
            mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            flash("Неизвестный формат экспорта.", "error")
            return redirect(url_for("calculation_view", calc_id=calc_id))
    except Exception as exc:  # noqa: BLE001
        flash(f"Ошибка экспорта: {exc}", "error")
        return redirect(url_for("calculation_view", calc_id=calc_id))
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path), mimetype=mime)


# ============================================================ клиенты

@app.route("/clients")
@login_required
def clients():
    return render_template("clients.html", items=db.list_clients())


# ============================================================ доп. услуги

@app.route("/services")
@login_required
def services_view():
    if CURRENT_PRICE is None:
        flash("Прайс ещё не загружен — нажмите «Загрузить данные».", "error")
        return redirect(url_for("index"))
    return render_template("services.html", price=_price_json(CURRENT_PRICE),
                           is_admin=session.get("role") == "admin")


# ============================================================ настройки

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_view():
    return render_template("settings.html")


# ============================================================ админ-панель

@app.route("/admin")
@login_required
@admin_required
def admin():
    return render_template("admin.html",
                           versions=db.list_price_versions(),
                           changes=db.list_price_changes())


@app.route("/admin/users", methods=["GET", "POST"])
@login_required
@admin_required
def admin_users():
    if request.method == "POST":
        action = request.form.get("action")
        try:
            user_id = int(request.form.get("user_id", "0"))
        except ValueError:
            user_id = 0
        if action == "create":
            login = request.form.get("login", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "manager")
            full_name = request.form.get("full_name", "").strip()
            if not login or len(password) < 6:
                flash("Укажите логин и пароль (минимум 6 символов).", "error")
            elif db.get_user_by_login(login):
                flash("Такой логин уже существует.", "error")
            else:
                db.create_user(login, password, role, full_name)
                flash(f"Сотрудник {login} создан.", "success")
        elif action == "password":
            password = request.form.get("password", "")
            if len(password) < 6:
                flash("Пароль слишком короткий (минимум 6 символов).", "error")
            else:
                db.set_user_password(user_id, password)
                flash("Пароль обновлён.", "success")
        elif action == "role":
            role = request.form.get("role", "manager")
            try:
                db.set_user_role(user_id, role)
                flash("Роль обновлена.", "success")
            except ValueError:
                flash("Недопустимая роль.", "error")
        elif action == "active":
            active = request.form.get("active") == "1"
            db.set_user_active(user_id, active)
            flash("Статус сотрудника обновлён.", "success")
        return redirect(url_for("admin_users"))
    return render_template("admin_users.html", users=db.list_users())


@app.route("/admin/price/update", methods=["POST"])
@login_required
@admin_required
def admin_price_update():
    global CURRENT_PRICE, CURRENT_PRICE_VERSION_ID
    if CURRENT_PRICE is None:
        flash("Прайс не загружен.", "error")
        return redirect(url_for("price_view"))
    entity = request.form.get("entity", "")
    entity_id = request.form.get("entity_id", "-")
    field = request.form.get("field", "")
    raw = request.form.get("new_value", "").strip()
    value = price_service._num_or_none(raw) if price_service._looks_numeric(raw) else raw

    old_value = None
    if entity == "rules":
        old_value = CURRENT_PRICE.rules.get(field)
        CURRENT_PRICE.rules[field] = value if raw != "" else None
    else:
        collections = {
            "constructions": CURRENT_PRICE.constructions,
            "profiles": CURRENT_PRICE.profiles,
            "glasses": CURRENT_PRICE.glasses,
            "hardware": CURRENT_PRICE.hardware,
            "services": CURRENT_PRICE.services,
        }
        if entity not in collections or entity_id not in collections[entity]:
            flash("Позиция не найдена в текущем прайсе.", "error")
            return redirect(url_for("price_view"))
        old_value = collections[entity][entity_id].get(field)
        collections[entity][entity_id][field] = value

    CURRENT_PRICE.version = _bump_version(CURRENT_PRICE.version)
    CURRENT_PRICE_VERSION_ID = db.save_price_version(
        CURRENT_PRICE, comment=f"Изменение: {entity} {entity_id} {field}")
    db.log_price_change(session["login"], entity, entity_id, field,
                        "" if old_value is None else str(old_value),
                        "" if value is None else str(value))
    try:
        price_service.write_workbook_from_price(CURRENT_PRICE, EXCEL_PATH,
                                                create_backup=True)
    except Exception as exc:  # noqa: BLE001
        flash(f"Значение сохранено в базе, но Excel не перезаписан: {exc}", "error")
        return redirect(url_for("price_view"))
    flash(f"Сохранено: {entity} {entity_id} {field} = {value}. "
          f"Создана версия прайса {CURRENT_PRICE.version}.", "success")
    return redirect(url_for("price_view"))


@app.route("/admin/price/export")
@login_required
@admin_required
def admin_price_export():
    if CURRENT_PRICE is None:
        flash("Прайс не загружен.", "error")
        return redirect(url_for("price_view"))
    os.makedirs(EXPORT_DIR, exist_ok=True)
    safe_version = re.sub(r"[^\w\-.]+", "_", str(CURRENT_PRICE.version)).strip("_")
    path = os.path.join(EXPORT_DIR, f"прайс_{safe_version or 'без_версии'}.xlsx")
    price_service.write_workbook_from_price(CURRENT_PRICE, path, create_backup=False)
    return send_file(path, as_attachment=True, download_name=os.path.basename(path))


@app.route("/admin/price/create_sample")
@login_required
@admin_required
def admin_price_create_sample():
    result = price_service.create_sample_workbook(EXCEL_PATH, force=False)
    if os.path.exists(EXCEL_PATH):
        flash(str(result), "success")
    else:
        flash(f"Не удалось создать файл: {result}", "error")
    return redirect(url_for("settings_view"))


# ============================================================ запуск

@app.context_processor
def _inject_app_flags():
    """Флаги приложения, доступные во всех шаблонах."""
    return {
        "shutdown_available": callable(app.config.get("SHUTDOWN_SERVER")),
        "is_frozen_app": app_paths.is_frozen(),
        "data_dir": app_paths.DATA_DIR,
    }


@app.route("/app/shutdown", methods=["POST"])
@login_required
@admin_required
def app_shutdown():
    """Остановка локального сервера из интерфейса (для собранного приложения)."""
    shutdown = app.config.get("SHUTDOWN_SERVER")
    if not callable(shutdown):
        flash("Остановка из интерфейса доступна только в собранном приложении "
              "(при запуске через запуск.bat просто закройте окно консоли).", "info")
        return redirect(url_for("settings_view"))
    threading.Timer(0.6, shutdown).start()
    return render_template("shutdown.html")


def _open_browser() -> None:
    try:
        webbrowser.open("http://127.0.0.1:5000/")
    except Exception:  # noqa: BLE001
        pass


def _port_busy(host: str = "127.0.0.1", port: int = 5000) -> bool:
    """Занят ли порт (запущена ли уже копия приложения)."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def _stop_running_instance() -> bool:
    """Останавливает ранее запущенную копию приложения на порту 5000.

    Нужно, чтобы после обновления кода в браузере никогда не оставалось окно
    со старой версией (иначе страницы падают с ошибкой и «нет кнопок»).
    Возвращает True, если порт свободен и можно запускать сервер.
    """
    import subprocess
    import time

    if not _port_busy():
        return True
    print("Найден уже запущенный калькулятор — останавливаю его, "
          "чтобы запустить актуальную версию…")

    pids: set[int] = set()
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=20).stdout
        for line in out.splitlines():
            parts = line.split()
            if (len(parts) >= 5 and parts[0].upper() == "TCP"
                    and parts[3].upper() == "LISTENING"
                    and parts[1].endswith(":5000")):
                pids.add(int(parts[4]))
    except Exception:  # noqa: BLE001
        pids = set()

    pids.discard(os.getpid())
    for pid in pids:
        subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"],
                       capture_output=True)
    for _ in range(24):  # ждём освобождения порта до 6 секунд
        if not _port_busy():
            return True
        time.sleep(0.25)
    return not _port_busy()


try:
    ensure_app_ready()
except Exception as exc:  # noqa: BLE001
    print(f"[warn] Инициализация при старте не выполнена: {exc}", file=sys.stderr)


if __name__ == "__main__":
    ensure_app_ready()
    if not _stop_running_instance():
        print("Порт 5000 занят другим приложением. Закройте его и запустите снова.",
              file=sys.stderr)
        raise SystemExit(1)
    threading.Timer(1.0, _open_browser).start()
    app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
