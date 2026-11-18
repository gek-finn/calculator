"""Сравнение действующего прайса и файла, полученного с Яндекс Диска.

Сопоставление позиций идёт по устойчивому идентификатору `id` — как в
остальном проекте. Цель — показать пользователю, что изменится ДО применения,
и никогда не подставлять позиции без подтверждения.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from calculator import PriceData

# (атрибут PriceData, человекочитаемое имя раздела, [(поле, подпись поля), ...])
KINDS: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("constructions", "Конструкции",
     (("базовая_цена", "Базовая цена"),
      ("минимальная_стоимость", "Минимальная стоимость"))),
    ("profiles", "Профили", (("цена_за_погонный_метр", "Цена за пог. метр"),)),
    ("glasses", "Стекло", (("надбавка_процент", "Надбавка, %"),)),
    ("hardware", "Фурнитура", (("цена", "Цена"),)),
    ("services", "Услуги", (("цена", "Цена"),)),
)

# Разделы, позиции которых сопоставляются с прайсом (для поиска неподходящих id).
MAPPED_SHEETS = ("Конструкции", "Профили", "Стекло", "Фурнитура", "Услуги")


def _num(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _text(value) -> str:
    return "" if value is None else str(value)


def _money(value: Decimal | None) -> str:
    return "" if value is None else f"{value:.2f}"


def _percent(old: Decimal | None, new: Decimal | None) -> str:
    if old is None or new is None or old == 0:
        return ""
    return f"{((new - old) / old * 100):.2f}"


def _price_of(item: dict) -> str:
    """Основная цена позиции для компактного списка новых/отсутствующих."""
    for field in ("базовая_цена", "цена", "цена_за_погонный_метр", "надбавка_процент"):
        if item.get(field) not in (None, ""):
            return _money(_num(item.get(field)))
    if item.get("минимальная_стоимость") not in (None, ""):
        return _money(_num(item.get("минимальная_стоимость")))
    return ""


def find_unmapped(data: dict | None) -> list[dict]:
    """Строки, которые нельзя надёжно сопоставить (пустой или дублирующийся id)."""
    if not data:
        return []
    out: list[dict] = []
    for sheet in MAPPED_SHEETS:
        seen: set[str] = set()
        for rec in data.get(sheet, []):
            rid = _text(rec.get("id")).strip()
            if not rid:
                out.append({"sheet": sheet, "row": rec.get("_строка"),
                            "reason": "Пустой id — сопоставить позицию невозможно"})
            elif rid in seen:
                out.append({"sheet": sheet, "row": rec.get("_строка"),
                            "reason": f"Дубликат id «{rid}»"})
            seen.add(rid)
    return out


def compare_prices(current: PriceData | None,
                   candidate: PriceData | None) -> dict:
    """Сравнивает действующий прайс и проверенный прайс из файла.

    Возвращает структуру с итоговыми счётчиками и списками изменений,
    новых, отсутствующих позиций и переименований.
    """
    changed: list[dict] = []
    new_positions: list[dict] = []
    missing_positions: list[dict] = []
    name_changes: list[dict] = []
    total = 0

    for attr, label, fields in KINDS:
        current_items = getattr(current, attr, {}) if current else {}
        candidate_items = getattr(candidate, attr, {}) if candidate else {}

        for rid, item in candidate_items.items():
            total += 1
            if rid not in current_items:
                new_positions.append({
                    "kind": attr, "kind_label": label, "id": rid,
                    "name": _text(item.get("название")), "price": _price_of(item),
                })
                continue
            old_item = current_items[rid]
            if _text(item.get("название")) != _text(old_item.get("название")):
                name_changes.append({
                    "kind": attr, "kind_label": label, "id": rid,
                    "old_name": _text(old_item.get("название")),
                    "new_name": _text(item.get("название")),
                })
            for field, field_label in fields:
                old_val = _num(old_item.get(field))
                new_val = _num(item.get(field))
                if old_val == new_val:
                    continue
                delta = (new_val - old_val) if (old_val is not None and new_val is not None) else None
                direction = "same"
                if delta is not None:
                    direction = "up" if delta > 0 else "down" if delta < 0 else "same"
                changed.append({
                    "kind": attr, "kind_label": label, "id": rid,
                    "name": _text(item.get("название")), "field": field,
                    "field_label": field_label,
                    "old": _money(old_val), "new": _money(new_val),
                    "delta": _money(delta) if delta is not None else "",
                    "percent": _percent(old_val, new_val),
                    "direction": direction,
                })

        for rid, item in current_items.items():
            if rid not in candidate_items:
                missing_positions.append({
                    "kind": attr, "kind_label": label, "id": rid,
                    "name": _text(item.get("название")), "price": _price_of(item),
                })

    # число позиций с изменёнными ценами (переименования считаются отдельно)
    touched = {c["kind"] + ":" + str(c["id"]) for c in changed}
    counts = {
        "total": total,
        "changed": len(changed),
        "changed_positions": len(touched),
        "new": len(new_positions),
        "missing": len(missing_positions),
        "name_changed": len(name_changes),
    }
    return {
        "counts": counts,
        "changed": changed,
        "new_positions": new_positions,
        "missing_positions": missing_positions,
        "name_changes": name_changes,
    }
