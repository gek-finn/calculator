"""Логика пересчёта сохранённого расчёта по обновлённому прайсу.

Здесь (а не в Flask-обработчиках и не в JavaScript) выполняются:
- определение отсутствующих в новом прайсе позиций;
- выявление изменений единиц измерления, типов начисления и правил;
- применение явно выбранных замен;
- пересчёт через существующий модуль calculator;
- построение таблицы сравнения старой и новой стоимости.

Принципы:
- отсутствующая позиция НИКОГДА не заменяется нулём — расчёт блокируется,
  пока пользователь не выберет замену или не отменит пересчёт;
- исторический расчёт не изменяется: результат сохраняется отдельной версией;
- бизнес-логика расчёта не дублируется — вызывается calculator.calculate_quote.
"""

from __future__ import annotations

import copy
from decimal import Decimal

from calculator import PriceData, calculate_quote

# типы позиций в исходном вводе расчёта
KIND_CONSTRUCTION = "constructions"
KIND_GLASS = "glasses"
KIND_PROFILE = "profiles"
KIND_HARDWARE = "hardware"
KIND_SERVICES = "services"

# правила, изменение которых требует подтверждения пользователя
COMPARED_RULES = ("профиль_правило_расчёта", "проверка_мин_стоимости")

# канонический порядок строк услуг в таблице сравнения
SERVICE_ORDER = ("Дополнительные материалы", "Замер", "Доставка по Уфе",
                 "Монтаж", "Проживание")

# поля позиций, изменения которых показываются пользователю при пересчёте
COMPARED_FIELDS: dict[str, tuple[str, ...]] = {
    KIND_CONSTRUCTION: ("название", "единица_расчёта", "базовая_цена",
                        "минимальная_стоимость"),
    KIND_PROFILE: ("название", "цена_за_погонный_метр"),
    KIND_GLASS: ("название", "надбавка_процент"),
    KIND_HARDWARE: ("название", "единица", "цена"),
    KIND_SERVICES: ("название", "тип_расчёта", "цена", "единица"),
}

# человекочитаемые названия полей и правил для интерфейса
FIELD_LABELS: dict[str, str] = {
    "название": "название",
    "единица_расчёта": "единица расчёта",
    "базовая_цена": "базовая цена",
    "минимальная_стоимость": "минимальная стоимость",
    "цена_за_погонный_метр": "цена за погонный метр",
    "надбавка_процент": "надбавка, %",
    "цена": "цена",
    "единица": "единица",
    "тип_расчёта": "тип расчёта",
}

RULE_LABELS: dict[str, str] = {
    "базовая_цена_стекла_руб_м2": "Базовая цена стекла, ₽/м²",
    "профиль_правило_расчёта": "Правило расчёта профиля",
    "проверка_мин_стоимости": "Проверка минимальной стоимости",
    "профиль_погонных_метров_на_м2": "Погонных метров на м²",
}



def _D(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal(0)
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001
        return Decimal(0)


def decompose_quote(quote: dict) -> dict:
    """Разложение итога на составляющие для таблицы сравнения.

    Возвращает: constructions, glass, hardware, services {имя: сумма},
    subtotal, discount, total. Суммы — Decimal.
    """
    structures_total = _D(quote.get("structures_total"))
    glass_total = Decimal(0)
    for structure in quote.get("structures", []):
        for line in structure.get("breakdown", []):
            if str(line.get("name", "")).startswith("Стекло"):
                glass_total += _D(line.get("amount"))
    services: dict[str, Decimal] = {}
    for line in quote.get("services", {}).get("lines", []):
        name = str(line.get("name", ""))
        services[name] = services.get(name, Decimal(0)) + _D(line.get("amount"))
    return {
        "constructions": structures_total - glass_total,
        "glass": glass_total,
        "hardware": _D(quote.get("hardware", {}).get("total")),
        "services": services,
        "subtotal": _D(quote.get("subtotal")),
        "discount": _D(quote.get("discount")),
        "total": _D(quote.get("total")),
    }


def build_comparison(old_quote: dict, new_quote: dict) -> list[dict]:
    """Таблица сравнения: Показатель | Исходный | Новый | Разница.

    Показываются только применимые строки (хотя бы одна из сумм != 0),
    но все составляющие сохраняются в decompose_quote.
    """
    old = decompose_quote(old_quote)
    new = decompose_quote(new_quote)
    rows: list[dict] = []

    def add(label: str, old_v: Decimal, new_v: Decimal) -> None:
        if old_v == 0 and new_v == 0:
            return
        rows.append({"label": label, "old": old_v, "new": new_v,
                     "diff": new_v - old_v})

    add("Конструкции", old["constructions"], new["constructions"])
    add("Стекло", old["glass"], new["glass"])
    add("Фурнитура", old["hardware"], new["hardware"])

    service_names = list(SERVICE_ORDER)
    for name in list(old["services"]) + list(new["services"]):
        if name not in service_names:
            service_names.append(name)
    for name in service_names:
        add(name, old["services"].get(name, Decimal(0)),
            new["services"].get(name, Decimal(0)))

    add("Сумма до скидки", old["subtotal"], new["subtotal"])
    add("Скидка", old["discount"], new["discount"])
    add("Итог", old["total"], new["total"])
    return rows


def _entity_name(price_snapshot: dict | None, kind: str, entity_id: str) -> str:
    entity = (price_snapshot or {}).get(kind, {}).get(entity_id) or {}
    return str(entity.get("название") or entity_id)


def _input_structures(payload: dict) -> list[dict]:
    return list(payload.get("structures", []))


def detect_missing(old_calc: dict, new_price: PriceData) -> list[dict]:
    """Позиции исходного расчёта, отсутствующие в новом прайсе.

    required=True — блокирует пересчёт (нужна замена).
    required=False — позиция не влияет на стоимость (отключена/правило выключено),
    но всё равно показывается пользователю.
    """
    payload = old_calc.get("snapshot", {}).get("input", {}) or {}
    old_price = old_calc.get("snapshot", {}).get("price") or {}
    missing: list[dict] = []

    profile_rule = str(new_price.rules.get("профиль_правило_расчёта", "отключён"))

    def candidates(kind: str, exclude_id: str) -> list[dict]:
        collections = {
            KIND_CONSTRUCTION: new_price.constructions,
            KIND_GLASS: new_price.glasses,
            KIND_PROFILE: new_price.profiles,
            KIND_HARDWARE: new_price.hardware,
            KIND_SERVICES: new_price.services,
        }[kind]
        out = []
        for eid, entity in collections.items():
            active = entity.get("активна", entity.get("активен", entity.get("активно", True)))
            if eid == exclude_id or not active:
                continue
            out.append({"id": eid, "name": str(entity.get("название", eid))})
        return sorted(out, key=lambda x: x["name"])

    def add(kind: str, entity_id: str, required: bool, reason: str) -> None:
        collections = {
            KIND_CONSTRUCTION: new_price.constructions,
            KIND_GLASS: new_price.glasses,
            KIND_PROFILE: new_price.profiles,
            KIND_HARDWARE: new_price.hardware,
            KIND_SERVICES: new_price.services,
        }[kind]
        if entity_id in collections:
            return
        missing.append({
            "kind": kind,
            "id": str(entity_id),
            "name": _entity_name(old_price, kind, str(entity_id)),
            "required": required,
            "reason": reason,
            "candidates": candidates(kind, str(entity_id)),
        })

    for structure in _input_structures(payload):
        add(KIND_CONSTRUCTION, str(structure.get("construction_id", "")), True,
            "тип конструкции")
        add(KIND_GLASS, str(structure.get("glass_id", "")), True, "стекло")
        profile_id = str(structure.get("profile_id", ""))
        if profile_id:
            if profile_rule == "отключён":
                add(KIND_PROFILE, profile_id, False,
                    "профиль не влияет на стоимость: расчёт профиля отключён в правилах")
            else:
                add(KIND_PROFILE, profile_id, True,
                    f"профиль (правило расчёта: {profile_rule})")

    for item in payload.get("hardware", []) or []:
        add(KIND_HARDWARE, str(item.get("hardware_id", "")), True, "фурнитура")

    for item in payload.get("services", []) or []:
        enabled = bool(item.get("enabled", True))
        add(KIND_SERVICES, str(item.get("service_id", "")), enabled,
            "услуга включена в расчёт" if enabled else
            "услуга была выключена и не влияет на стоимость")
    return missing


def detect_warnings(old_calc: dict, new_price: PriceData) -> list[str]:
    """Изменения единиц измерения, типов начисления и правил — требуют подтверждения."""
    payload = old_calc.get("snapshot", {}).get("input", {}) or {}
    old_price = old_calc.get("snapshot", {}).get("price") or {}
    warnings: list[str] = []

    for structure in _input_structures(payload):
        cid = str(structure.get("construction_id", ""))
        old_entity = (old_price.get("constructions", {}) or {}).get(cid) or {}
        new_entity = new_price.constructions.get(cid)
        if old_entity and new_entity:
            old_unit = str(old_entity.get("единица_расчёта", ""))
            new_unit = str(new_entity.get("единица_расчёта", ""))
            if old_unit and old_unit != new_unit:
                warnings.append(
                    f"«{new_entity.get('название', cid)}»: изменилась единица "
                    f"расчёта конструкции ({old_unit} → {new_unit}).")

    for item in payload.get("hardware", []) or []:
        hid = str(item.get("hardware_id", ""))
        old_entity = (old_price.get("hardware", {}) or {}).get(hid) or {}
        new_entity = new_price.hardware.get(hid)
        if old_entity and new_entity:
            old_unit = str(old_entity.get("единица", ""))
            new_unit = str(new_entity.get("единица", ""))
            if old_unit and old_unit != new_unit:
                warnings.append(
                    f"«{new_entity.get('название', hid)}»: изменилась единица "
                    f"измерения фурнитуры ({old_unit} → {new_unit}).")

    for item in payload.get("services", []) or []:
        sid = str(item.get("service_id", ""))
        old_entity = (old_price.get("services", {}) or {}).get(sid) or {}
        new_entity = new_price.services.get(sid)
        if old_entity and new_entity:
            old_type = str(old_entity.get("тип_расчёта", ""))
            new_type = str(new_entity.get("тип_расчёта", ""))
            if old_type and old_type != new_type:
                warnings.append(
                    f"«{new_entity.get('название', sid)}»: изменился тип "
                    f"начисления услуги ({old_type} → {new_type}).")

    for rule in COMPARED_RULES:
        old_value = (old_price.get("rules", {}) or {}).get(rule)
        new_value = new_price.rules.get(rule)
        if old_value is not None and new_value is not None and str(old_value) != str(new_value):
            warnings.append(f"Изменилось правило расчёта «{rule}»: "
                            f"{old_value} → {new_value}.")
    return warnings


def apply_replacements(payload: dict, replacements: dict) -> dict:
    """Применяет выбранные пользователем замены к исходному вводу расчёта.

    replacements: {"<kind>::<old_id>": "<new_id>", ...}
    """
    data = copy.deepcopy(payload)
    mapping: dict[str, dict[str, str]] = {}
    for key, value in replacements.items():
        if "::" not in key or not value:
            continue
        kind, old_id = key.split("::", 1)
        mapping.setdefault(kind, {})[old_id] = str(value)

    def map_id(kind: str, entity_id):
        entity_id = str(entity_id)
        return mapping.get(kind, {}).get(entity_id, entity_id)

    for structure in data.get("structures", []):
        structure["construction_id"] = map_id(KIND_CONSTRUCTION, structure.get("construction_id"))
        structure["glass_id"] = map_id(KIND_GLASS, structure.get("glass_id"))
        if structure.get("profile_id"):
            structure["profile_id"] = map_id(KIND_PROFILE, structure.get("profile_id"))
        for item in structure.get("hardware", []) or []:
            item["hardware_id"] = map_id(KIND_HARDWARE, item.get("hardware_id"))
    for item in data.get("hardware", []) or []:
        item["hardware_id"] = map_id(KIND_HARDWARE, item.get("hardware_id"))
    for item in data.get("services", []) or []:
        item["service_id"] = map_id(KIND_SERVICES, item.get("service_id"))
    return data


def used_entities(payload: dict) -> dict[str, set[str]]:
    """Идентификаторы позиций прайса, участвующих в расчёте."""
    used: dict[str, set[str]] = {
        KIND_CONSTRUCTION: set(), KIND_PROFILE: set(), KIND_GLASS: set(),
        KIND_HARDWARE: set(), KIND_SERVICES: set(),
    }
    for structure in payload.get("structures", []) or []:
        used[KIND_CONSTRUCTION].add(str(structure.get("construction_id", "")))
        used[KIND_GLASS].add(str(structure.get("glass_id", "")))
        if structure.get("profile_id"):
            used[KIND_PROFILE].add(str(structure.get("profile_id")))
        for item in structure.get("hardware", []) or []:
            used[KIND_HARDWARE].add(str(item.get("hardware_id", "")))
    for item in payload.get("hardware", []) or []:
        used[KIND_HARDWARE].add(str(item.get("hardware_id", "")))
    for item in payload.get("services", []) or []:
        used[KIND_SERVICES].add(str(item.get("service_id", "")))
    return used


def price_changes(old_calc: dict, new_price: PriceData,
                  replacements: dict | None = None) -> list[dict]:
    """Что изменилось в прайсе для позиций, участвующих в расчёте.

    Показывает пользователю, какие именно значения прайса затронул новый прайс,
    чтобы было видно: повлияло ли изменение (например, минимальная стоимость
    не влияет, если она ниже расчётной стоимости) или изменилась другая позиция.
    """
    payload = apply_replacements(
        old_calc.get("snapshot", {}).get("input", {}) or {}, replacements or {})
    old_price = old_calc.get("snapshot", {}).get("price") or {}
    collections = {
        KIND_CONSTRUCTION: new_price.constructions,
        KIND_PROFILE: new_price.profiles,
        KIND_GLASS: new_price.glasses,
        KIND_HARDWARE: new_price.hardware,
        KIND_SERVICES: new_price.services,
    }

    changes: list[dict] = []
    used = used_entities(payload)
    for kind, ids in used.items():
        for entity_id in sorted(ids):
            old_entity = (old_price.get(kind) or {}).get(entity_id)
            new_entity = collections[kind].get(entity_id)
            if old_entity is None or new_entity is None:
                continue
            name = str((new_entity or old_entity).get("название") or entity_id)
            for field in COMPARED_FIELDS[kind]:
                old_value = old_entity.get(field)
                new_value = new_entity.get(field)
                if str(old_value) != str(new_value):
                    changes.append({
                        "kind": kind, "id": entity_id, "name": name,
                        "field": field,
                        "field_label": FIELD_LABELS.get(field, field),
                        "old": old_value, "new": new_value,
                    })

    old_rules = old_price.get("rules") or {}
    for key, label in RULE_LABELS.items():
        old_value = old_rules.get(key)
        new_value = new_price.rules.get(key)
        if str(old_value) != str(new_value):
            changes.append({
                "kind": "rules", "id": key, "name": label,
                "field": key, "field_label": label,
                "old": old_value, "new": new_value,
            })
    return changes


def analyze(old_calc: dict, new_price: PriceData,
            replacements: dict | None = None,
            discount_type: str | None = None,
            discount_value: str | None = None,
            is_admin: bool = False) -> dict:
    """Полный анализ пересчёта: замены → позиции → правила → предварительный итог.

    Не изменяет old_calc. Возвращает словарь:
        missing, unresolved_missing, warnings, old_quote, new_quote,
        comparison, errors, ok, price_version, price ( PriceData )
    """
    payload = old_calc.get("snapshot", {}).get("input", {}) or {}
    old_quote = old_calc.get("snapshot", {}).get("quote", {}) or {}
    replacements = replacements or {}

    new_payload = apply_replacements(payload, replacements)
    # Скидка сохраняет исходный тип и значение, если пользователь не изменил их
    if discount_type is not None:
        new_payload["discount_type"] = discount_type
    if discount_value is not None:
        new_payload["discount_value"] = discount_value
    else:
        new_payload.setdefault("discount_type", old_quote.get("discount_type", ""))
        new_payload.setdefault("discount_value", old_quote.get("discount_value", ""))

    missing = detect_missing(old_calc, new_price)
    unresolved = [m for m in missing if m["required"]]
    warnings = detect_warnings(old_calc, new_price)

    result = {
        "missing": missing,
        "unresolved_missing": unresolved,
        "warnings": warnings,
        "old_quote": old_quote,
        "new_quote": None,
        "comparison": None,
        "price_changes": price_changes(old_calc, new_price, replacements),
        "errors": [],
        "ok": False,
        "price_version": new_price.version,
        "price": new_price,
        "payload": new_payload,
    }

    if unresolved:
        result["errors"] = [
            {"field": m["kind"], "blocking": True,
             "message": f"«{m['name']}» — не найдена в новом прайсе ({m['reason']}). "
                        "Выберите замену или отмените пересчёт."}
            for m in unresolved
        ]
        return result

    quote = calculate_quote(new_price, new_payload, is_admin=is_admin)
    if not quote["ok"]:
        result["errors"] = quote["errors"]
        return result

    result["new_quote"] = quote
    result["comparison"] = build_comparison(old_quote, quote)
    result["ok"] = True
    return result
