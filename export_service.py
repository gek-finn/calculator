"""Экспорт сохранённого расчёта в PDF (коммерческое предложение) и XLSX.

Формируется ТОЛЬКО из снимка конкретной версии расчёта (snapshot), а не из
текущего прайса: старые расчёты экспортируются по старым ценам.

Безопасность:
- имена файлов очищаются от недопустимых символов;
- пароли, хеши и секреты в экспорт не попадают (берутся только поля расчёта);
- файлы складываются в постоянную папку exports/ (не во временную).
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import Decimal

# ------------------------------------------------------------------ имена файлов

UNSAFE_CHARS = re.compile(r"[^\w\-.]+", re.UNICODE)


def safe_filename(calc: dict, ext: str) -> str:
    """Например: Расчет_КА-20261018-0001_2026-10-19.pdf"""
    number = UNSAFE_CHARS.sub("_", str(calc.get("number", "расчёт"))).strip("_")
    date = _calc_date(calc)
    return f"Расчет_{number}_{date}.{ext}"


def _calc_date(calc: dict) -> str:
    raw = str(calc.get("created_at", ""))
    for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return datetime.now().strftime("%Y-%m-%d")


# ------------------------------------------------------------------ данные из снимка

def _snapshot(calc: dict) -> dict:
    snap = calc.get("snapshot") or {}
    return {
        "quote": snap.get("quote", {}) or {},
        "input": snap.get("input", {}) or {},
        "price": snap.get("price", {}) or {},
        "client": snap.get("client", {}) or {},
    }


def _structure_rows(calc: dict) -> list[dict]:
    """Строки таблицы конструкций: параметры из ввода + суммы из цитаты."""
    snap = _snapshot(calc)
    price = snap["price"]
    rows = []
    structures_input = snap["input"].get("structures", [])
    for idx, quote_s in enumerate(snap["quote"].get("structures", [])):
        params = structures_input[idx] if idx < len(structures_input) else {}
        glass = price.get("glasses", {}).get(str(params.get("glass_id", "")), {})
        profile = price.get("profiles", {}).get(str(params.get("profile_id", "")), {})
        rows.append({
            "no": idx + 1,
            "name": quote_s.get("construction_name", ""),
            "profile": profile.get("название", params.get("profile_id", "") or "—"),
            "size": f"{quote_s.get('width_mm')} × {quote_s.get('height_mm')}",
            "quantity": quote_s.get("quantity", 0),
            "area": quote_s.get("area_total", 0),
            "glass": glass.get("название", params.get("glass_id", "") or "—"),
            "amount": quote_s.get("subtotal", 0),
        })
    return rows


def _hardware_rows(calc: dict) -> list[dict]:
    return [{
        "name": line.get("name", ""),
        "quantity": line.get("quantity", 0),
        "unit_price": line.get("unit_price", 0),
        "amount": line.get("amount", 0),
        "formula": line.get("formula", ""),
    } for line in _snapshot(calc)["quote"].get("hardware", {}).get("lines", [])]


def _service_rows(calc: dict) -> list[dict]:
    return [{
        "name": line.get("name", ""),
        "amount": line.get("amount", 0),
        "formula": line.get("formula", ""),
    } for line in _snapshot(calc)["quote"].get("services", {}).get("lines", [])]


def _detail_rows(calc: dict) -> list[dict]:
    rows = []
    for structure in _snapshot(calc)["quote"].get("structures", []):
        for line in structure.get("breakdown", []):
            rows.append({"name": line.get("name", ""),
                         "formula": line.get("formula", ""),
                         "amount": line.get("amount", 0)})
    for line in _hardware_rows(calc):
        rows.append({"name": f"Фурнитура: {line['name']}",
                     "formula": line["formula"], "amount": line["amount"]})
    for line in _service_rows(calc):
        rows.append({"name": f"Услуга: {line['name']}",
                     "formula": line["formula"], "amount": line["amount"]})
    return rows


# ------------------------------------------------------------------ XLSX

def export_xlsx(calc: dict, out_dir: str) -> str:
    """Создаёт XLSX с листами: Итог, Конструкции, Детализация, Версия прайса."""
    from openpyxl import Workbook

    snap = _snapshot(calc)
    quote = snap["quote"]
    price = snap["price"]
    wb = Workbook()

    # --- Итог ---
    ws = wb.active
    ws.title = "Итог"
    ws.append(["Компания", "ЛидерПро"])
    ws.append(["Номер расчёта", calc.get("number", "")])
    ws.append(["Дата расчёта", calc.get("created_at", "")])
    ws.append(["Сотрудник", calc.get("user_login", "")])
    ws.append(["Клиент", calc.get("client_name", "")])
    ws.append(["Телефон", calc.get("client_phone", "")])
    ws.append(["Email", calc.get("client_email", "")])
    ws.append(["Адрес объекта", calc.get("client_address", "")])
    ws.append(["Источник заявки", calc.get("client_source", "")])
    ws.append(["Комментарий", calc.get("comment", "")])
    ws.append([])
    ws.append(["Версия прайса", calc.get("price_version", "")])
    ws.append(["Версия расчёта", calc.get("version_no", 1)])
    ws.append([])
    ws.append(["Сумма до скидки", float(quote.get("subtotal", 0) or 0)])
    ws.append(["Скидка", float(quote.get("discount", 0) or 0)])
    ws.append(["Итого к оплате", float(quote.get("total", 0) or 0)])

    # --- Конструкции ---
    ws = wb.create_sheet("Конструкции")
    ws.append(["№", "Конструкция", "Профиль", "Размеры, мм", "Количество",
               "Площадь, м²", "Стекло", "Стоимость"])
    for row in _structure_rows(calc):
        ws.append([row["no"], row["name"], row["profile"], row["size"],
                   row["quantity"], float(row["area"]),
                   row["glass"], float(row["amount"])])

    # --- Детализация ---
    ws = wb.create_sheet("Детализация")
    ws.append(["Позиция", "Формула / тариф", "Сумма"])
    for line in _detail_rows(calc):
        ws.append([line["name"], line["formula"], float(line["amount"])])

    # --- Версия прайса ---
    ws = wb.create_sheet("Версия прайса")
    ws.append(["Идентификатор версии", calc.get("price_version", "")])
    ws.append(["Дата загрузки прайса", price.get("loaded_at", "")])
    ws.append([])
    ws.append(["Правила расчёта"])
    ws.append(["Ключ", "Значение"])
    for key, value in (price.get("rules") or {}).items():
        ws.append([key, str(value if value is not None else "")])
    for sheet_name, entities in (
            ("Конструкции", price.get("constructions") or {}),
            ("Профили", price.get("profiles") or {}),
            ("Стекло", price.get("glasses") or {}),
            ("Фурнитура", price.get("hardware") or {}),
            ("Услуги", price.get("services") or {})):
        ws.append([])
        ws.append([f"Прайс: {sheet_name}"])
        headers = ["id", "название"]
        for field in ("единица_расчёта", "минимальная_стоимость", "базовая_цена",
                      "цена_за_погонный_метр", "надбавка_процент", "цена",
                      "единица", "тип_расчёта"):
            if any(field in entity for entity in entities.values()):
                headers.append(field)
        ws.append(headers)
        for entity_id, entity in entities.items():
            row = [entity_id, entity.get("название", "")]
            row.extend(entity.get(field, "") for field in headers[2:])
            ws.append([str(v) if not isinstance(v, (int, float)) else v for v in row])

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, safe_filename(calc, "xlsx"))
    wb.save(path)
    return path


# ------------------------------------------------------------------ PDF

_FONT_REGULAR_CANDIDATES = ("arial.ttf", "segoeui.ttf", "tahoma.ttf",
                            "verdana.ttf", "arialuni.ttf", "DejaVuSans.ttf")
_FONT_BOLD_CANDIDATES = ("arialbd.ttf", "segoeuib.ttf", "tahomabd.ttf",
                         "verdanab.ttf", "DejaVuSans-Bold.ttf")


def _register_fonts() -> tuple[str, str]:
    """Регистрирует кириллический шрифт из системных. Возвращает (regular, bold)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    regular_path = next((os.path.join(fonts_dir, f)
                         for f in _FONT_REGULAR_CANDIDATES
                         if os.path.exists(os.path.join(fonts_dir, f))), None)
    if regular_path is None:
        raise RuntimeError(
            "Не найден кириллический шрифт (arial.ttf и др. в C:\\Windows\\Fonts) "
            "— PDF с русским текстом сформировать невозможно.")
    bold_path = next((os.path.join(fonts_dir, f)
                      for f in _FONT_BOLD_CANDIDATES
                      if os.path.exists(os.path.join(fonts_dir, f))), regular_path)

    pdfmetrics.registerFont(TTFont("LP", regular_path))
    pdfmetrics.registerFont(TTFont("LP-Bold", bold_path))
    pdfmetrics.registerFontFamily("LP", normal="LP", bold="LP-Bold",
                                  italic="LP", boldItalic="LP-Bold")
    return "LP", "LP-Bold"


def export_pdf(calc: dict, out_dir: str) -> str:
    """Формирует коммерческое предложение в PDF с таблицами и кириллицей."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    font, font_bold = _register_fonts()
    snap = _snapshot(calc)
    quote = snap["quote"]

    styles = {
        "title": ParagraphStyle("title", fontName=font_bold, fontSize=16,
                                alignment=TA_CENTER, spaceAfter=4),
        "h2": ParagraphStyle("h2", fontName=font_bold, fontSize=11,
                             spaceBefore=10, spaceAfter=4),
        "normal": ParagraphStyle("normal", fontName=font, fontSize=9,
                                 leading=12),
        "muted": ParagraphStyle("muted", fontName=font, fontSize=8,
                                textColor=colors.HexColor("#666666")),
        "right": ParagraphStyle("right", fontName=font, fontSize=9,
                                alignment=TA_RIGHT),
    }

    def p(text, style="normal"):
        return Paragraph(str(text if text is not None else ""), styles[style])

    def money(value) -> str:
        try:
            return f"{Decimal(str(value)):,.2f}".replace(",", " ").replace(".", ",") + " ₽"
        except Exception:  # noqa: BLE001
            return f"{value} ₽"

    def table(data, widths, header=True) -> Table:
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [
            ("FONT", (0, 0), (-1, -1), font, 8),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("PADDING", (0, 0), (-1, -1), 4),
        ]
        if header:
            style += [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e2530")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONT", (0, 0), (-1, 0), font_bold, 8),
            ]
        t.setStyle(TableStyle(style))
        return t

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, safe_filename(calc, "pdf"))

    doc = SimpleDocTemplate(path, pagesize=A4,
                            leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Расчёт {calc.get('number', '')}",
                            author="ЛидерПро")
    story = [p("«ЛидерПро» — расчёт стоимости конструкций", "title"),
             p(f"Коммерческое предложение № {calc.get('number', '')} "
               f"от {calc.get('created_at', '')}", "normal"),
             p(f"Сотрудник: {calc.get('user_login', '')}", "muted"),
             Spacer(1, 6)]

    # Клиент
    story.append(p("Данные клиента", "h2"))
    story.append(table([
        [p("ФИО / компания"), p(calc.get("client_name") or "—")],
        [p("Телефон"), p(calc.get("client_phone") or "—")],
        [p("Email"), p(calc.get("client_email") or "—")],
        [p("Адрес объекта"), p(calc.get("client_address") or "—")],
        [p("Источник заявки"), p(calc.get("client_source") or "—")],
    ], [45 * mm, 125 * mm], header=False))

    # Конструкции
    struct_rows = _structure_rows(calc)
    if struct_rows:
        story.append(p("Конструкции", "h2"))
        data = [[p("№", "muted"), p("Конструкция", "muted"), p("Профиль", "muted"),
                 p("Размеры, мм", "muted"), p("Кол-во", "muted"),
                 p("Площадь, м²", "muted"), p("Стекло", "muted"),
                 p("Стоимость", "muted")]]
        for row in struct_rows:
            data.append([p(row["no"]), p(row["name"]), p(row["profile"]),
                         p(row["size"]), p(row["quantity"]), p(row["area"]),
                         p(row["glass"]), p(money(row["amount"]), "right")])
        story.append(table(data, [8 * mm, 36 * mm, 24 * mm, 22 * mm,
                                  12 * mm, 16 * mm, 30 * mm, 22 * mm]))

    # Фурнитура
    hw_rows = _hardware_rows(calc)
    if hw_rows:
        story.append(p("Выбранная фурнитура", "h2"))
        data = [[p("Наименование", "muted"), p("Кол-во", "muted"),
                 p("Цена", "muted"), p("Сумма", "muted")]]
        for row in hw_rows:
            data.append([p(row["name"]), p(row["quantity"]),
                         p(money(row["unit_price"])), p(money(row["amount"]), "right")])
        story.append(table(data, [90 * mm, 25 * mm, 30 * mm, 25 * mm]))

    # Услуги
    svc_rows = _service_rows(calc)
    if svc_rows:
        story.append(p("Дополнительные работы и услуги", "h2"))
        data = [[p("Наименование", "muted"), p("Тариф", "muted"),
                 p("Сумма", "muted")]]
        for row in svc_rows:
            data.append([p(row["name"]), p(row["formula"]),
                         p(money(row["amount"]), "right")])
        story.append(table(data, [70 * mm, 60 * mm, 40 * mm]))

    # Детализация
    detail = _detail_rows(calc)
    if detail:
        story.append(p("Детализация стоимости", "h2"))
        data = [[p("Позиция", "muted"), p("Формула", "muted"), p("Сумма", "muted")]]
        for row in detail:
            data.append([p(row["name"]), p(row["formula"]),
                         p(money(row["amount"]), "right")])
        story.append(table(data, [70 * mm, 70 * mm, 30 * mm]))

    # Итоги
    story.append(p("Итог", "h2"))
    story.append(table([
        [p("Сумма до скидки"), p(money(quote.get("subtotal")), "right")],
        [p("Скидка"), p("−" + money(quote.get("discount")), "right")],
        [p("Итого к оплате", "muted"), p(money(quote.get("total")), "right")],
    ], [100 * mm, 70 * mm], header=False))

    comment = calc.get("comment") or snap["client"].get("comment")
    if comment:
        story.append(Spacer(1, 6))
        story.append(p(f"Комментарий: {comment}", "muted"))
    story.append(Spacer(1, 8))
    story.append(p(f"Расчёт выполнен по версии прайса {calc.get('price_version', '')}. "
                   "Предложение действительно на дату формирования.", "muted"))

    doc.build(story)
    return path
