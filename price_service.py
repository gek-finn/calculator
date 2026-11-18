"""Работа с прайсом в Excel: создание учебного файла, чтение, проверка, экспорт.

Правила надёжности:
- Файл не считается загруженным, пока он не прочитан И не прошёл проверку.
- Ошибки показываются с указанием листа и строки.
- Перед записью в существующий файл создаётся резервная копия.
- Повреждённый или ошибочный файл не заменяет рабочий прайс.
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime
from decimal import Decimal, InvalidOperation

from openpyxl import Workbook, load_workbook

from calculator import (
    BY_AREA, BY_LINEAR, BY_PIECE, FIXED, PERCENT_ADD,
    PROFILE_RULE_OFF, PriceData,
)

# Обязательные листы и их обязательные колонки
REQUIRED_SHEETS: dict[str, list[str]] = {
    "Конструкции": ["id", "название", "активна", "единица_расчёта",
                    "минимальная_стоимость", "описание"],
    "Профили": ["id", "название", "ширина_мм", "высота_мм",
                "цена_за_погонный_метр", "активен"],
    "Стекло": ["id", "название", "надбавка_процент", "активно"],
    "Фурнитура": ["id", "название", "единица", "цена", "активна"],
    "Услуги": ["id", "название", "тип_расчёта", "цена", "единица", "активна"],
    "Правила": ["ключ", "значение", "тип", "описание"],
    "История_версий": ["версия", "дата_создания", "имя_файла", "комментарий"],
}

VALID_UNITS = {BY_AREA, BY_LINEAR, BY_PIECE, FIXED, PERCENT_ADD}
VALID_SERVICE_TYPES = {FIXED, BY_PIECE, BY_AREA}
VALID_HARDWARE_UNITS = {BY_PIECE, BY_AREA, FIXED}
VALID_PROFILE_RULES = {PROFILE_RULE_OFF, "периметр", "ширина", "высота",
                       "погонных_метров_на_м2"}

# «Магические» байты форматов Excel для надёжного определения формата.
XLSX_MAGIC = b"PK\x03\x04"                       # zip-контейнер (.xlsx/.xlsm)
XLS_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # OLE2-контейнер (.xls)
EXCEL_EXTENSIONS = (".xlsx", ".xlsm", ".xls")


def detect_excel_format(head: bytes) -> str | None:
    """Определяет формат Excel по первым байтам файла.

    Возвращает "xlsx", "xls" или None (не Excel — например, HTML-страница).
    """
    if head[:4] == XLSX_MAGIC:
        return "xlsx"
    if head[:8] == XLS_MAGIC:
        return "xls"
    return None


def detect_format_by_path(path: str) -> str | None:
    """Формат Excel по содержимому файла (None, если файл недоступен/не Excel)."""
    try:
        with open(path, "rb") as fh:
            return detect_excel_format(fh.read(8))
    except OSError:
        return None


def _cell_text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _cell_number(value):
    """Возвращает Decimal или None, если пусто; бросает ValueError при мусоре."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        return Decimal(str(value).replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError(f"не число: {value!r}")


def _as_bool(value) -> bool:
    text = _cell_text(value).lower()
    return text in ("да", "true", "1", "истина", "yes", "y")


def create_sample_workbook(path: str, force: bool = False) -> str:
    """Создаёт учебную таблицу цен и правил. Не перезаписывает существующий файл."""
    if os.path.exists(path) and not force:
        return f"Файл уже существует: {path}. Автоматическая перезапись запрещена."

    wb = Workbook()

    # --- Лист 1: Конструкции -------------------------------------------
    ws = wb.active
    ws.title = "Конструкции"
    ws.append(["id", "название", "активна", "единица_расчёта",
               "минимальная_стоимость", "описание", "базовая_цена"])
    constructions = [
        (1, "Межкомнатная перегородка", "да", BY_AREA, 120000,
         "Минимум 120000 ₽ подтверждён скриншотом «ЛидерПро».", None),
        (2, "Офисная / банная перегородка", "да", BY_AREA, None,
         "УЧЕБНОЕ ДОПУЩЕНИЕ: цена неизвестна, задайте администратором.", None),
        (3, "Ограждение", "да", BY_AREA, None,
         "УЧЕБНОЕ ДОПУЩЕНИЕ: цена неизвестна, задайте администратором.", None),
        (4, "Козырёк", "да", BY_AREA, None,
         "УЧЕБНОЕ ДОПУЩЕНИЕ: цена неизвестна, задайте администратором.", None),
        (5, "Остекление террасы", "да", BY_AREA, None,
         "УЧЕБНОЕ ДОПУЩЕНИЕ: цена неизвестна, задайте администратором.", None),
        (6, "Алюминиевая конструкция", "да", BY_AREA, None,
         "УЧЕБНОЕ ДОПУЩЕНИЕ: цена неизвестна, задайте администратором.", None),
        (7, "Раздвижная система", "да", BY_PIECE, None,
         "УЧЕБНОЕ ДОПУЩЕНИЕ: цена неизвестна, задайте администратором.", None),
        (8, "Портал", "да", BY_PIECE, None,
         "УЧЕБНОЕ ДОПУЩЕНИЕ: цена неизвестна, задайте администратором.", None),
    ]
    for row in constructions:
        ws.append(list(row))

    # --- Лист 2: Профили -------------------------------------------------
    ws = wb.create_sheet("Профили")
    ws.append(["id", "название", "ширина_мм", "высота_мм",
               "цена_за_погонный_метр", "активен", "примечание"])
    ws.append([1, "Профиль 38×38 мм", 38, 38, None, "да",
               "УЧЕБНЫЙ ПАРАМЕТР: цена погонного метра неизвестна, "
               "заполните администратором перед включением расчёта профиля."])

    # --- Лист 3: Стекло ---------------------------------------------------
    ws = wb.create_sheet("Стекло")
    ws.append(["id", "название", "надбавка_процент", "активно", "примечание"])
    glasses = [
        (1, "Прозрачное закалённое", 0, "да", "Надбавка 0% — снимок с исходного скриншота."),
        (2, "Матовое", 10, "да", "Надбавка +10% — со скриншота."),
        (3, "Серое / бронза", 22, "да", "Надбавка +22% — со скриншота."),
        (4, "Серое / бронза матовое", 35, "да", "Надбавка +35% — со скриншота."),
        (5, "Осветлённое", 35, "да", "Надбавка +35% — со скриншота."),
        (6, "Осветлённое матовое", 40, "да", "Надбавка +40% — со скриншота."),
        (7, "Рифлёное", 40, "да", "Надбавка +40% — со скриншота."),
    ]
    for row in glasses:
        ws.append(list(row))

    # --- Лист 4: Фурнитура ------------------------------------------------
    ws = wb.create_sheet("Фурнитура")
    ws.append(["id", "название", "единица", "цена", "активна", "примечание"])
    hardware = [
        (1, "Доводчик", BY_PIECE, 8000, "да", "8000 ₽ — цена позиции со скриншота."),
        (2, "Замок", BY_PIECE, 3500, "да", "3500 ₽ — цена позиции со скриншота."),
        (3, "Ручка", BY_PIECE, 2000, "да", "2000 ₽ — цена позиции со скриншота."),
        (4, "Закладная", BY_PIECE, 2500, "да", "2500 ₽ — цена позиции со скриншота."),
    ]
    for row in hardware:
        ws.append(list(row))

    # --- Лист 5: Услуги ---------------------------------------------------
    ws = wb.create_sheet("Услуги")
    ws.append(["id", "название", "тип_расчёта", "цена", "единица", "активна",
               "примечание"])
    services = [
        (1, "Дополнительные материалы", FIXED, 8500, "заказ", "да",
         "8500 ₽ за заказ — учебное воспроизведение примера со скриншота."),
        (2, "Замер", FIXED, 2500, "заказ", "да",
         "2500 ₽ за заказ — учебное воспроизведение примера со скриншота."),
        (3, "Доставка по Уфе", FIXED, 6000, "заказ", "да",
         "6000 ₽ за заказ — учебное воспроизведение примера со скриншота."),
        (4, "Монтаж", FIXED, 35000, "заказ", "да",
         "35000 ₽ за заказ — учебное воспроизведение примера со скриншота."),
        (5, "Проживание", FIXED, 0, "заказ", "нет",
         "0 ₽ в исходном примере; услуга необязательная, по умолчанию выключена."),
    ]
    for row in services:
        ws.append(list(row))

    # --- Лист 6: Правила ---------------------------------------------------
    ws = wb.create_sheet("Правила")
    ws.append(["ключ", "значение", "тип", "описание"])
    rules = [
        ("версия_прайса", "1.0-учебная", "строка",
         "Версия прайса, увеличивается при сохранении изменений администратором."),
        ("макс_скидка_менеджера_процент", 10, "число",
         "УЧЕБНОЕ ДОПУЩЕНИЕ: предельная скидка менеджера. Уточните у заказчика."),
        ("базовый_процент_скидки", 0, "число",
         "Скидка по умолчанию для нового расчёта (0 = без скидки)."),
        ("единица_площади", "м²", "строка", "Единица измерения площади."),
        ("единица_размеров", "мм", "строка", "Единица ввода размеров конструкций."),
        ("округление_денежных", "0.01", "строка",
         "Шаг округления денежных сумм (до копеек)."),
        ("проверка_мин_стоимости", "да", "строка",
         "Применять ли минимальную стоимость конструкции при расчёте."),
        ("базовая_цена_стекла_руб_м2", None, "число",
         "УЧЕБНЫЙ ПАРАМЕТР: базовая цена стекла за м² НЕИЗВЕСТНА. "
         "Заполните её, иначе расчёт стекла будет заблокирован."),
        ("профиль_правило_расчёта", PROFILE_RULE_OFF, "строка",
         "Как считать погонные метры профиля: отключён | периметр | ширина | "
         "высота | погонных_метров_на_м2. По умолчанию отключён, пока "
         "не подтверждены правила расчёта."),
        ("профиль_погонных_метров_на_м2", None, "число",
         "Коэффициент погонных метров на м² (нужен только для правила "
         "«погонных_метров_на_м2»)."),
    ]
    for row in rules:
        ws.append(list(row))

    # --- Лист 7: История_версий -------------------------------------------
    ws = wb.create_sheet("История_версий")
    ws.append(["версия", "дата_создания", "имя_файла", "комментарий"])
    ws.append(["1.0-учебная", datetime.now().strftime("%d.%m.%Y %H:%M"),
               os.path.basename(path),
               "Первая учебная версия. Проценты стекла, услуги и фурнитура — "
               "со скриншота «ЛидерПро». Цены профилей, базовая цена стекла и "
               "тарифы большинства конструкций неизвестны и требуют заполнения."])

    wb.save(path)
    return f"Создан учебный прайс: {path}"


def read_workbook_rows(path: str) -> dict[str, list[dict]]:
    """Читает все листы прайса в список словарей. Бросает ValueError при повреждении.

    Поддерживаются оба формата Excel: .xlsx/.xlsm (openpyxl) и .xls (xlrd).
    Формат определяется по содержимому файла, а не только по расширению.
    """
    if detect_format_by_path(path) == "xls":
        return _read_workbook_rows_xls(path)
    return _read_workbook_rows_xlsx(path)


def _read_workbook_rows_xlsx(path: str) -> dict[str, list[dict]]:
    if not os.path.exists(path):
        raise ValueError(f"Файл не найден: {path}")
    try:
        wb = load_workbook(path, data_only=True)
    except Exception as exc:  # noqa: BLE001 — любой сбой парсинга = повреждённый файл
        raise ValueError(f"Не удалось открыть Excel-файл (повреждён?): {exc}") from exc

    data: dict[str, list[dict]] = {}
    for ws in wb.worksheets:
        rows_iter = ws.iter_rows(values_only=True)
        try:
            headers_raw = next(rows_iter)
        except StopIteration:
            data[ws.title] = []
            continue
        headers = [_cell_text(h) for h in headers_raw]
        rows = []
        for idx, row in enumerate(rows_iter, start=2):
            if all(cell is None or _cell_text(cell) == "" for cell in row):
                continue
            record = {"_строка": idx}
            for header, value in zip(headers, row):
                if header:
                    record[header] = value
            rows.append(record)
        data[ws.title] = rows
    return data


def _read_workbook_rows_xls(path: str) -> dict[str, list[dict]]:
    """Чтение старого формата .xls через xlrd (необязательная зависимость)."""
    if not os.path.exists(path):
        raise ValueError(f"Файл не найден: {path}")
    try:
        import xlrd  # необязательная зависимость только для формата .xls
    except ImportError as exc:
        raise ValueError(
            "Для чтения формата .xls нужна библиотека xlrd. "
            "Установите её командой: pip install xlrd") from exc
    try:
        book = xlrd.open_workbook(path)
    except Exception as exc:  # noqa: BLE001 — любой сбой парсинга = повреждённый файл
        raise ValueError(f"Не удалось открыть .xls-файл (повреждён?): {exc}") from exc

    data: dict[str, list[dict]] = {}
    for sheet in book.sheets():
        if sheet.nrows == 0:
            data[sheet.name] = []
            continue
        headers = [_cell_text(sheet.cell_value(0, c)) for c in range(sheet.ncols)]
        rows: list[dict] = []
        for r in range(1, sheet.nrows):
            values = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
            if all(v is None or _cell_text(v) == "" for v in values):
                continue
            record: dict = {"_строка": r + 1}
            for header, value in zip(headers, values):
                if header:
                    record[header] = value
            rows.append(record)
        data[sheet.name] = rows
    return data


def validate_rows(data: dict[str, list[dict]]) -> list[dict]:
    """Проверка структуры и данных прайса. Возвращает список ошибок."""
    errors: list[dict] = []

    def err(sheet: str, row, message: str, blocking: bool = True):
        errors.append({"sheet": sheet, "row": row, "message": message,
                       "blocking": blocking})

    for sheet, columns in REQUIRED_SHEETS.items():
        if sheet not in data:
            err(sheet, None, f"Отсутствует обязательный лист «{sheet}»")
            continue
        rows = data[sheet]
        if not rows:
            err(sheet, None, "Лист пуст (нет ни одной строки данных)")
            continue
        headers = set(rows[0].keys())
        for col in columns:
            if col not in headers:
                err(sheet, None, f"Отсутствует обязательная колонка «{col}»")

    # --- Конструкции ------------------------------------------------------
    seen_ids: set[str] = set()
    valid_units = VALID_UNITS
    for rec in data.get("Конструкции", []):
        row = rec.get("_строка")
        rid = _cell_text(rec.get("id"))
        if not rid:
            err("Конструкции", row, "Пустой id")
        elif rid in seen_ids:
            err("Конструкции", row, f"Дубликат id: {rid}")
        seen_ids.add(rid)
        if not _cell_text(rec.get("название")):
            err("Конструкции", row, "Пустое название")
        unit = _cell_text(rec.get("единица_расчёта"))
        if unit not in valid_units:
            err("Конструкции", row,
                f"Недопустимая единица расчёта «{unit}». Допустимы: "
                f"{', '.join(sorted(valid_units))}")
        try:
            _cell_number(rec.get("минимальная_стоимость"))
        except ValueError:
            err("Конструкции", row, "Минимальная стоимость не число")
        try:
            _cell_number(rec.get("базовая_цена"))
        except ValueError:
            err("Конструкции", row, "Базовая цена не число")

    # --- Профили ------------------------------------------------------------
    seen_ids = set()
    for rec in data.get("Профили", []):
        row = rec.get("_строка")
        rid = _cell_text(rec.get("id"))
        if not rid:
            err("Профили", row, "Пустой id")
        elif rid in seen_ids:
            err("Профили", row, f"Дубликат id: {rid}")
        seen_ids.add(rid)
        for col in ("ширина_мм", "высота_мм"):
            try:
                val = _cell_number(rec.get(col))
                if val is not None and val <= 0:
                    err("Профили", row, f"{col} должна быть больше нуля")
            except ValueError:
                err("Профили", row, f"{col} не число")
        try:
            price_val = _cell_number(rec.get("цена_за_погонный_метр"))
            if price_val is not None and price_val < 0:
                err("Профили", row, "Цена погонного метра отрицательная")
        except ValueError:
            err("Профили", row, "Цена погонного метра не число", blocking=True)

    # --- Стекло ---------------------------------------------------------------
    seen_ids = set()
    for rec in data.get("Стекло", []):
        row = rec.get("_строка")
        rid = _cell_text(rec.get("id"))
        if not rid:
            err("Стекло", row, "Пустой id")
        elif rid in seen_ids:
            err("Стекло", row, f"Дубликат id: {rid}")
        seen_ids.add(rid)
        try:
            pct = _cell_number(rec.get("надбавка_процент"))
            if pct is not None and (pct < 0 or pct > 500):
                err("Стекло", row, "Надбавка должна быть в диапазоне 0–500%")
        except ValueError:
            err("Стекло", row, "Надбавка_процент не число")

    # --- Фурнитура -------------------------------------------------------------
    seen_ids = set()
    for rec in data.get("Фурнитура", []):
        row = rec.get("_строка")
        rid = _cell_text(rec.get("id"))
        if not rid:
            err("Фурнитура", row, "Пустой id")
        elif rid in seen_ids:
            err("Фурнитура", row, f"Дубликат id: {rid}")
        seen_ids.add(rid)
        unit = _cell_text(rec.get("единица"))
        if unit and unit not in VALID_HARDWARE_UNITS:
            err("Фурнитура", row, f"Недопустимая единица «{unit}»")
        try:
            price_val = _cell_number(rec.get("цена"))
            if price_val is not None and price_val < 0:
                err("Фурнитура", row, "Цена отрицательная")
        except ValueError:
            err("Фурнитура", row, "Цена не число")

    # --- Услуги -------------------------------------------------------------------
    seen_ids = set()
    for rec in data.get("Услуги", []):
        row = rec.get("_строка")
        rid = _cell_text(rec.get("id"))
        if not rid:
            err("Услуги", row, "Пустой id")
        elif rid in seen_ids:
            err("Услуги", row, f"Дубликат id: {rid}")
        seen_ids.add(rid)
        stype = _cell_text(rec.get("тип_расчёта"))
        if stype and stype not in VALID_SERVICE_TYPES:
            err("Услуги", row, f"Недопустимый тип расчёта «{stype}»")
        try:
            price_val = _cell_number(rec.get("цена"))
            if price_val is not None and price_val < 0:
                err("Услуги", row, "Цена отрицательная")
        except ValueError:
            err("Услуги", row, "Цена не число")

    # --- Правила ---------------------------------------------------------------------
    seen_keys: set[str] = set()
    for rec in data.get("Правила", []):
        row = rec.get("_строка")
        key = _cell_text(rec.get("ключ"))
        if not key:
            err("Правила", row, "Пустой ключ")
            continue
        if key in seen_keys:
            err("Правила", row, f"Дубликат ключа правил: {key}")
        seen_keys.add(key)
    rule_profile = None
    for rec in data.get("Правила", []):
        if _cell_text(rec.get("ключ")) == "профиль_правило_расчёта":
            rule_profile = _cell_text(rec.get("значение"))
    if rule_profile and rule_profile not in VALID_PROFILE_RULES:
        err("Правила", None,
            f"Недопустимое правило расчёта профиля «{rule_profile}». "
            f"Допустимы: {', '.join(sorted(VALID_PROFILE_RULES))}")

    # --- Блокирующее условие: базовая цена стекла --------------------------------------
    glass_base = None
    for rec in data.get("Правила", []):
        if _cell_text(rec.get("ключ")) == "базовая_цена_стекла_руб_м2":
            glass_base = rec.get("значение")
    if glass_base in (None, ""):
        err("Правила", None,
            "Базовая цена стекла за м² не заполнена (учебный параметр). "
            "Пока она пуста, подтверждение расчётов заблокировано. "
            "Заполните её в админ-панели или в Excel.")

    # --- Предупреждения (не блокируют загрузку, но видны пользователю) ------------------
    for rec in data.get("Профили", []):
        if rec.get("цена_за_погонный_метр") in (None, ""):
            err("Профили", rec.get("_строка"),
                f"Цена погонного метра профиля «{_cell_text(rec.get('название'))}» "
                "не заполнена (учебный параметр). Расчёт профиля останется "
                "отключённым, пока цена и правило не заданы.",
                blocking=False)
    for rec in data.get("Конструкции", []):
        if rec.get("базовая_цена") in (None, "") and rec.get("минимальная_стоимость") in (None, ""):
            err("Конструкции", rec.get("_строка"),
                f"Для конструкции «{_cell_text(rec.get('название'))}» не заданы "
                "ни базовая цена, ни минимальная стоимость (учебное допущение).",
                blocking=False)
    return errors


def build_price_data(data: dict, loaded_at: str | None = None) -> PriceData:
    """Превращает проверенные строки Excel в объект PriceData."""
    loaded_at = loaded_at or datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    constructions = {}
    for rec in data.get("Конструкции", []):
        rid = _cell_text(rec.get("id"))
        if not rid:
            continue
        constructions[rid] = {
            "id": rid,
            "название": _cell_text(rec.get("название")),
            "активна": _as_bool(rec.get("активна")),
            "единица_расчёта": _cell_text(rec.get("единица_расчёта")),
            "минимальная_стоимость": _num_or_none(rec.get("минимальная_стоимость")),
            "базовая_цена": _num_or_none(rec.get("базовая_цена")),
            "описание": _cell_text(rec.get("описание")),
        }

    profiles = {}
    for rec in data.get("Профили", []):
        rid = _cell_text(rec.get("id"))
        if not rid:
            continue
        profiles[rid] = {
            "id": rid,
            "название": _cell_text(rec.get("название")),
            "ширина_мм": _num_or_none(rec.get("ширина_мм")),
            "высота_мм": _num_or_none(rec.get("высота_мм")),
            "цена_за_погонный_метр": _num_or_none(rec.get("цена_за_погонный_метр")),
            "активен": _as_bool(rec.get("активен")),
        }

    glasses = {}
    for rec in data.get("Стекло", []):
        rid = _cell_text(rec.get("id"))
        if not rid:
            continue
        glasses[rid] = {
            "id": rid,
            "название": _cell_text(rec.get("название")),
            "надбавка_процент": _num_or_none(rec.get("надбавка_процент")) or Decimal(0),
            "активно": _as_bool(rec.get("активно")),
        }

    hardware = {}
    for rec in data.get("Фурнитура", []):
        rid = _cell_text(rec.get("id"))
        if not rid:
            continue
        hardware[rid] = {
            "id": rid,
            "название": _cell_text(rec.get("название")),
            "единица": _cell_text(rec.get("единица")) or BY_PIECE,
            "цена": _num_or_none(rec.get("цена")),
            "активна": _as_bool(rec.get("активна")),
        }

    services = {}
    for rec in data.get("Услуги", []):
        rid = _cell_text(rec.get("id"))
        if not rid:
            continue
        services[rid] = {
            "id": rid,
            "название": _cell_text(rec.get("название")),
            "тип_расчёта": _cell_text(rec.get("тип_расчёта")) or FIXED,
            "цена": _num_or_none(rec.get("цена")),
            "единица": _cell_text(rec.get("единица")) or "заказ",
            "активна": _as_bool(rec.get("активна")),
        }

    rules: dict[str, object] = {}
    for rec in data.get("Правила", []):
        key = _cell_text(rec.get("ключ"))
        if not key:
            continue
        value = rec.get("значение")
        rules[key] = _num_or_none(value) if _looks_numeric(value) else (value if value is not None else None)

    version = _cell_text(rules.get("версия_прайса")) or "без_версии"
    return PriceData(
        version=version, loaded_at=loaded_at,
        constructions=constructions, profiles=profiles, glasses=glasses,
        hardware=hardware, services=services, rules=rules,
    )


def _num_or_none(value):
    try:
        return _cell_number(value)
    except ValueError:
        return None


def _looks_numeric(value) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    try:
        Decimal(str(value).replace(",", "."))
        return True
    except (InvalidOperation, ValueError):
        return False


def load_price_from_file(path: str) -> tuple[PriceData | None, list[dict], dict]:
    """Читает и проверяет прайс. Возвращает (PriceData | None, ошибки, статистика).

    PriceData возвращается ТОЛЬКО если нет блокирующих ошибок.
    """
    stats = {"filename": os.path.basename(path), "loaded_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S")}
    try:
        data = read_workbook_rows(path)
    except ValueError as exc:
        return None, [{"sheet": "-", "row": None, "message": str(exc), "blocking": True}], stats

    errors = validate_rows(data)
    blocking = [e for e in errors if e["blocking"]]
    stats["counts"] = {
        "Конструкции": len(data.get("Конструкции", [])),
        "Профили": len(data.get("Профили", [])),
        "Стекло": len(data.get("Стекло", [])),
        "Фурнитура": len(data.get("Фурнитура", [])),
        "Услуги": len(data.get("Услуги", [])),
        "Правила": len(data.get("Правила", [])),
    }
    if blocking:
        return None, errors, stats
    price = build_price_data(data, loaded_at=stats["loaded_at"])
    stats["version"] = price.version
    return price, errors, stats


def load_price_for_editing(path: str) -> PriceData | None:
    """Читает прайс БЕЗ проверки для админского редактирования.

    Нужен, когда файл содержит блокирующие ошибки: администратор должен иметь
    возможность исправить их через интерфейс, а не только руками в Excel.
    """
    try:
        data = read_workbook_rows(path)
    except ValueError:
        return None
    return build_price_data(data)


def write_workbook_from_price(price: PriceData, path: str, create_backup: bool = True) -> str:
    """Записывает PriceData в Excel. Перед перезаписью создаёт резервную копию."""
    if os.path.exists(path) and create_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base, ext = os.path.splitext(path)
        backup_path = f"{base}_резервная_копия_{stamp}{ext}"
        shutil.copy2(path, backup_path)

    wb = Workbook()

    ws = wb.active
    ws.title = "Конструкции"
    ws.append(["id", "название", "активна", "единица_расчёта",
               "минимальная_стоимость", "описание", "базовая_цена"])
    for item in price.constructions.values():
        ws.append([item["id"], item["название"], "да" if item["активна"] else "нет",
                   item["единица_расчёта"], _plain(item["минимальная_стоимость"]),
                   item.get("описание", ""), _plain(item.get("базовая_цена"))])

    ws = wb.create_sheet("Профили")
    ws.append(["id", "название", "ширина_мм", "высота_мм",
               "цена_за_погонный_метр", "активен"])
    for item in price.profiles.values():
        ws.append([item["id"], item["название"], _plain(item["ширина_мм"]),
                   _plain(item["высота_мм"]), _plain(item["цена_за_погонный_метр"]),
                   "да" if item["активен"] else "нет"])

    ws = wb.create_sheet("Стекло")
    ws.append(["id", "название", "надбавка_процент", "активно"])
    for item in price.glasses.values():
        ws.append([item["id"], item["название"], _plain(item["надбавка_процент"]),
                   "да" if item["активно"] else "нет"])

    ws = wb.create_sheet("Фурнитура")
    ws.append(["id", "название", "единица", "цена", "активна"])
    for item in price.hardware.values():
        ws.append([item["id"], item["название"], item["единица"],
                   _plain(item["цена"]), "да" if item["активна"] else "нет"])

    ws = wb.create_sheet("Услуги")
    ws.append(["id", "название", "тип_расчёта", "цена", "единица", "активна"])
    for item in price.services.values():
        ws.append([item["id"], item["название"], item["тип_расчёта"],
                   _plain(item["цена"]), item["единица"],
                   "да" if item["активна"] else "нет"])

    ws = wb.create_sheet("Правила")
    ws.append(["ключ", "значение", "тип", "описание"])
    for key, value in price.rules.items():
        ws.append([key, _plain(value), "число" if _looks_numeric(value) else "строка", ""])

    ws = wb.create_sheet("История_версий")
    ws.append(["версия", "дата_создания", "имя_файла", "комментарий"])
    ws.append([price.version, price.loaded_at, os.path.basename(path),
               "Сохранено администратором через интерфейс калькулятора."])

    wb.save(path)
    return f"Прайс сохранён: {path} (версия {price.version})"


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    return value


def install_price_file(src_path: str, dest_path: str,
                       create_backup: bool = True) -> dict:
    """Заменяет рабочий Excel-файл прайса скачанным файлом, делая резервную копию.

    Копирование идёт через временный файл и os.replace — замена атомарна в
    пределах одного диска, поэтому рабочий файл никогда не остаётся
    «полузаписанным». Возвращает путь результата и путь резервной копии.
    """
    if not os.path.exists(src_path):
        raise ValueError(f"Исходный файл не найден: {src_path}")
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
    backup_path = ""
    if os.path.exists(dest_path) and create_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base, ext = os.path.splitext(dest_path)
        backup_path = f"{base}_резервная_копия_{stamp}{ext}"
        shutil.copy2(dest_path, backup_path)
    tmp_path = dest_path + ".tmp"
    shutil.copy2(src_path, tmp_path)
    os.replace(tmp_path, dest_path)
    return {"path": dest_path, "backup": backup_path}
