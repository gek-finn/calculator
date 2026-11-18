"""Модуль расчёта стоимости конструкций.

Ключевые принципы:
- Все денежные вычисления выполняются через Decimal (без ошибок двоичной арифметики).
- Каждая позиция прайса имеет ЯВНЫЙ тип расчёта. Одна формула не применяется ко всему.
- Незаполненная цена никогда не превращается молча в ноль: она даёт ошибку или
  явное предупреждение, видимое пользователю.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any

MONEY_STEP = Decimal("0.01")

# Типы начислений
BY_AREA = "за_м2"            # цена за квадратный метр
BY_LINEAR = "за_погонный_метр"  # цена за погонный метр
BY_PIECE = "за_штуку"        # цена за штуку
PERCENT_ADD = "процентная_надбавка"
FIXED = "фиксированная"

# Правила расчёта погонных метров профиля (задаются администратором в Правилах)
PROFILE_RULE_OFF = "отключён"
PROFILE_RULE_PERIMETER = "периметр"
PROFILE_RULE_WIDTH = "ширина"
PROFILE_RULE_HEIGHT = "высота"
PROFILE_RULE_PER_M2 = "погонных_метров_на_м2"


def D(value: Any) -> Decimal:
    """Надёжное превращение значения в Decimal."""
    if isinstance(value, Decimal):
        return value
    if value is None or value == "":
        raise InvalidOperation("Пустое значение")
    return Decimal(str(value))


def money(value: Any) -> Decimal:
    """Округление денежной суммы до копеек по правилам округления (HALF_UP)."""
    return D(value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def area_m2(width_mm: Any, height_mm: Any) -> Decimal:
    """Площадь одной конструкции в м²: ширина × высота / 1_000_000."""
    w = D(width_mm)
    h = D(height_mm)
    if w <= 0 or h <= 0:
        raise ValueError("Ширина и высота должны быть больше нуля")
    return (w * h) / Decimal(1_000_000)


@dataclass
class PriceData:
    """Загруженный прайс: справочники, правила и версия."""

    version: str
    loaded_at: str
    constructions: dict[str, dict] = field(default_factory=dict)
    profiles: dict[str, dict] = field(default_factory=dict)
    glasses: dict[str, dict] = field(default_factory=dict)
    hardware: dict[str, dict] = field(default_factory=dict)
    services: dict[str, dict] = field(default_factory=dict)
    rules: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "loaded_at": self.loaded_at,
            "constructions": self.constructions,
            "profiles": self.profiles,
            "glasses": self.glasses,
            "hardware": self.hardware,
            "services": self.services,
            "rules": self.rules,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PriceData":
        return cls(
            version=data.get("version", "?"),
            loaded_at=data.get("loaded_at", ""),
            constructions=data.get("constructions", {}),
            profiles=data.get("profiles", {}),
            glasses=data.get("glasses", {}),
            hardware=data.get("hardware", {}),
            services=data.get("services", {}),
            rules=data.get("rules", {}),
        )


@dataclass
class CalcError:
    """Ошибка расчёта: блокирующая или предупреждение."""

    field: str
    message: str
    blocking: bool = True

    def to_dict(self) -> dict:
        return {"field": self.field, "message": self.message, "blocking": self.blocking}


def profile_linear_meters(rule: str, width_mm: Any, height_mm: Any,
                          meters_per_m2: Any) -> Decimal:
    """Сколько погонных метров профиля учитываются по заданному правилу.

    Правило выбирается администратором. Без явного правила погонные метры
    НЕ выводятся автоматически из размеров.
    """
    w = D(width_mm)
    h = D(height_mm)
    if rule == PROFILE_RULE_PERIMETER:
        return (Decimal(2) * (w + h)) / Decimal(1000)
    if rule == PROFILE_RULE_WIDTH:
        return w / Decimal(1000)
    if rule == PROFILE_RULE_HEIGHT:
        return h / Decimal(1000)
    if rule == PROFILE_RULE_PER_M2:
        if meters_per_m2 in (None, ""):
            raise ValueError(
                "Включён расчёт профиля «погонных метров на м²», "
                "но коэффициент не задан в правилах")
        return area_m2(w, h) * D(meters_per_m2)
    if rule == PROFILE_RULE_OFF:
        return Decimal(0)
    raise ValueError(f"Неизвестное правило расчёта профиля: {rule!r}")


def calculate_structure(price: PriceData, params: dict) -> dict:
    """Расчёт стоимости одной конструкции.

    params:
        construction_id, profile_id, glass_id, width_mm, height_mm, quantity
        hardware: список {"hardware_id": ..., "quantity": n}
    Возвращает словарь с детализацией и списком ошибок.
    """
    errors: list[CalcError] = []
    notes: list[str] = []
    cid = str(params.get("construction_id", ""))
    construction = price.constructions.get(cid)
    if not construction or not construction.get("активна", True):
        errors.append(CalcError("construction_id",
                                f"Конструкция «{cid}» не найдена или отключена"))
        return {"errors": [e.to_dict() for e in errors], "notes": notes}

    try:
        qty = int(params.get("quantity", 1))
        if qty <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append(CalcError("quantity", "Количество должно быть целым числом больше нуля"))
        qty = 1

    try:
        width = D(params.get("width_mm"))
        height = D(params.get("height_mm"))
        one_area = area_m2(width, height)
    except (InvalidOperation, ValueError):
        errors.append(CalcError("size", "Укажите ширину и высоту в миллиметрах больше нуля"))
        one_area = Decimal(0)
        width = height = Decimal(0)

    total_area = one_area * qty
    unit = str(construction.get("единица_расчёта", BY_AREA))

    breakdown: list[dict] = []

    # --- Стекло: площадь × базовая цена за м² × (1 + надбавка) -------------
    glass_id = str(params.get("glass_id", ""))
    glass = price.glasses.get(glass_id)
    glass_cost = Decimal(0)
    if glass is None:
        errors.append(CalcError("glass_id", f"Стекло «{glass_id}» не найдено"))
    else:
        base_price = price.rules.get("базовая_цена_стекла_руб_м2")
        if base_price in (None, ""):
            # Явная ошибка вместо молчаливого нуля
            errors.append(CalcError(
                "базовая_цена_стекла_руб_м2",
                "Базовая цена стекла за м² не заполнена в правилах прайса. "
                "Заполните её в разделе «Прайс» (администратор) до подтверждения расчёта."))
        else:
            surcharge = D(glass.get("надбавка_процент", 0))
            one_m2_price = money(D(base_price) * (Decimal(1) + surcharge / Decimal(100)))
            glass_cost = money(one_m2_price * total_area)
            breakdown.append({
                "name": f"Стекло: {glass.get('название', glass_id)}",
                "type": BY_AREA,
                "formula": (f"{total_area:.2f} м² × {one_m2_price} ₽/м² "
                            f"(базовая {base_price} + надбавка {surcharge}%)"),
                "amount": glass_cost,
            })
            if surcharge > 0:
                notes.append(f"Надбавка за стекло «{glass.get('название')}»: {surcharge}%")

    # --- Профиль: только по явному правилу --------------------------------
    profile_id = str(params.get("profile_id", ""))
    profile_cost = Decimal(0)
    rule = str(price.rules.get("профиль_правило_расчёта", PROFILE_RULE_OFF))
    if rule != PROFILE_RULE_OFF:
        profile = price.profiles.get(profile_id)
        if profile is None:
            errors.append(CalcError("profile_id", f"Профиль «{profile_id}» не найден"))
        else:
            p_price = profile.get("цена_за_погонный_метр")
            if p_price in (None, ""):
                errors.append(CalcError(
                    "profile_price",
                    f"Цена погонного метра профиля «{profile.get('название')}» "
                    "не заполнена в прайсе. Заполните её перед подтверждением расчёта."))
            else:
                try:
                    lm_one = profile_linear_meters(
                        rule, width, height, price.rules.get("профиль_погонных_метров_на_м2"))
                    lm_total = money(lm_one * qty)
                    profile_cost = money(lm_total * D(p_price))
                    breakdown.append({
                        "name": f"Профиль: {profile.get('название')}",
                        "type": BY_LINEAR,
                        "formula": f"{lm_total} пог. м × {p_price} ₽ (правило: {rule})",
                        "amount": profile_cost,
                    })
                except ValueError as exc:
                    errors.append(CalcError("профиль_правило_расчёта", str(exc)))
    else:
        notes.append("Расчёт профиля отключён в правилах прайса (учебное допущение).")

    # --- Базовая цена конструкции в зависимости от единицы расчёта --------
    base_price = construction.get("базовая_цена")
    base_cost = Decimal(0)
    if unit == BY_AREA:
        if base_price not in (None, ""):
            base_cost = money(D(base_price) * total_area)
            breakdown.append({
                "name": f"{construction.get('название')} (работы, м²)",
                "type": BY_AREA,
                "formula": f"{total_area:.2f} м² × {base_price} ₽/м²",
                "amount": base_cost,
            })
    elif unit == BY_PIECE:
        if base_price not in (None, ""):
            base_cost = money(D(base_price) * qty)
            breakdown.append({
                "name": f"{construction.get('название')} (за штуку)",
                "type": BY_PIECE,
                "formula": f"{qty} шт × {base_price} ₽",
                "amount": base_cost,
            })
    elif unit == FIXED:
        if base_price not in (None, ""):
            base_cost = money(D(base_price))
            breakdown.append({
                "name": f"{construction.get('название')} (фиксированная)",
                "type": FIXED,
                "formula": f"{base_price} ₽",
                "amount": base_cost,
            })
    elif unit == PERCENT_ADD:
        subtotal_so_far = glass_cost + profile_cost + base_cost
        if base_price not in (None, ""):
            base_cost = money(subtotal_so_far * D(base_price) / Decimal(100))
            breakdown.append({
                "name": f"{construction.get('название')} (надбавка {base_price}%)",
                "type": PERCENT_ADD,
                "formula": f"({glass_cost} + {profile_cost}) × {base_price}%",
                "amount": base_cost,
            })
    else:
        errors.append(CalcError("единица_расчёта",
                                f"Неизвестная единица расчёта конструкции: {unit!r}"))

    subtotal = money(glass_cost + profile_cost + base_cost)

    # --- Минимальная стоимость (правило «проверка_мин_стоимости») --------
    min_cost_raw = construction.get("минимальная_стоимость")
    min_applied = Decimal(0)
    check_min = str(price.rules.get("проверка_мин_стоимости", "да")).lower() in ("да", "true", "1")
    if min_cost_raw not in (None, "") and check_min:
        min_cost = money(min_cost_raw)
        if subtotal < min_cost:
            min_applied = min_cost
            breakdown.append({
                "name": f"Минимальная стоимость «{construction.get('название')}»",
                "type": FIXED,
                "formula": (f"Расчётная сумма {subtotal} ₽ меньше минимума "
                            f"{min_cost} ₽ — применён минимум"),
                "amount": min_cost,
            })
            notes.append(f"Применена минимальная стоимость: {min_cost} ₽")
            subtotal = min_cost

    return {
        "construction_id": cid,
        "construction_name": construction.get("название", cid),
        "unit": unit,
        "width_mm": width,
        "height_mm": height,
        "quantity": qty,
        "area_one": one_area,
        "area_total": total_area,
        "breakdown": breakdown,
        "subtotal": subtotal,
        "min_cost_applied": min_applied,
        "notes": notes,
        "errors": [e.to_dict() for e in errors],
    }


def calculate_hardware(hardware_items: list[dict], price: PriceData) -> tuple[Decimal, list[dict], list[CalcError]]:
    """Фурнитура: каждая позиция считается поштучно (или по её типу расчёта)."""
    errors: list[CalcError] = []
    lines: list[dict] = []
    total = Decimal(0)
    for item in hardware_items or []:
        hid = str(item.get("hardware_id", ""))
        h = price.hardware.get(hid)
        if h is None or not h.get("активна", True):
            errors.append(CalcError("hardware", f"Позиция фурнитуры «{hid}» не найдена или отключена"))
            continue
        try:
            qty = int(item.get("quantity", 1))
            if qty <= 0:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(CalcError("hardware", f"Некорректное количество фурнитуры «{h.get('название')}»"))
            continue
        price_raw = h.get("цена")
        if price_raw in (None, ""):
            errors.append(CalcError("hardware", f"Цена фурнитуры «{h.get('название')}» не заполнена"))
            continue
        unit = str(h.get("единица", BY_PIECE))
        if unit == BY_PIECE:
            amount = money(D(price_raw) * qty)
            formula = f"{qty} шт × {price_raw} ₽"
        elif unit == BY_AREA:
            amount = money(D(price_raw) * qty)
            formula = f"{qty} м² × {price_raw} ₽/м²"
        else:
            amount = money(D(price_raw) * qty)
            formula = f"{qty} × {price_raw} ₽ ({unit})"
        total += amount
        lines.append({"name": h.get("название", hid), "unit": unit,
                      "quantity": qty, "unit_price": money(price_raw),
                      "amount": amount, "formula": formula})
    return money(total), lines, errors


def calculate_services(selected: list[dict], price: PriceData) -> tuple[Decimal, list[dict], list[CalcError]]:
    """Дополнительные услуги: каждая включается независимо, может быть отключена."""
    errors: list[CalcError] = []
    lines: list[dict] = []
    total = Decimal(0)
    for item in selected or []:
        if not item.get("enabled", True):
            continue  # отключённая услуга не считается
        sid = str(item.get("service_id", ""))
        s = price.services.get(sid)
        if s is None:
            errors.append(CalcError("services", f"Услуга «{sid}» не найдена в прайсе"))
            continue
        price_raw = s.get("цена")
        if price_raw in (None, ""):
            errors.append(CalcError("services", f"Цена услуги «{s.get('название')}» не заполнена"))
            continue
        try:
            amount = money(D(price_raw))
        except InvalidOperation:
            errors.append(CalcError("services", f"Некорректная цена услуги «{s.get('название')}»"))
            continue
        total += amount
        lines.append({
            "name": s.get("название", sid),
            "type": str(s.get("тип_расчёта", FIXED)),
            "unit": s.get("единица", "заказ"),
            "amount": amount,
            "formula": f"{s.get('цена')} ₽ за {s.get('единица', 'заказ')}",
        })
    return money(total), lines, errors


def apply_discount(amount_before: Decimal, discount_type: str, discount_value: Any,
                   max_percent: Any, is_admin: bool = False) -> dict:
    """Применение скидки: процентом или фиксированной суммой.

    Проверяет: не выходит ли процент за максимум, не отрицателен ли итог.
    """
    errors: list[CalcError] = []
    before = money(amount_before)
    discount = Decimal(0)
    dval_raw = discount_value if discount_value not in (None, "") else 0
    try:
        dval = D(dval_raw)
    except InvalidOperation:
        return {"before": before, "discount": Decimal(0), "total": before,
                "errors": [CalcError("discount", "Некорректное значение скидки").to_dict()]}

    if discount_type == "процент":
        try:
            max_pct = D(max_percent if max_percent not in (None, "") else 100)
        except InvalidOperation:
            max_pct = Decimal(100)
        if dval < 0:
            errors.append(CalcError("discount", "Скидка не может быть отрицательной"))
            dval = Decimal(0)
        if not is_admin and dval > max_pct:
            errors.append(CalcError(
                "discount",
                f"Менеджер не может задать скидку больше {max_pct}% (правила прайса)"))
            dval = max_pct
        if dval > 100:
            errors.append(CalcError("discount", "Скидка не может превышать 100%"))
            dval = Decimal(100)
        discount = money(before * dval / Decimal(100))
    elif discount_type == "фикс":
        if dval < 0:
            errors.append(CalcError("discount", "Скидка не может быть отрицательной"))
            dval = Decimal(0)
        if not is_admin:
            # фиксированная скидка ограничивается процентом от суммы
            limit = money(before * (D(max_percent if max_percent not in (None, "") else 0) / Decimal(100)))
            if dval > limit:
                errors.append(CalcError(
                    "discount",
                    f"Менеджер не может задать фиксированную скидку больше {limit} ₽"))
                dval = limit
        discount = money(min(dval, before))
    else:
        if dval != 0:
            errors.append(CalcError("discount", f"Неизвестный тип скидки: {discount_type!r}"))

    total = money(before - discount)
    if total < 0:
        errors.append(CalcError("discount", "Итог после скидки не может быть отрицательным"))
        total = Decimal(0)
    return {"before": before, "discount": discount, "total": total,
            "errors": [e.to_dict() for e in errors]}


def calculate_quote(price: PriceData, data: dict, is_admin: bool = False) -> dict:
    """Полный расчёт заказа: конструкции + фурнитура + услуги + скидка.

    data:
        structures: список параметров конструкций (см. calculate_structure)
        hardware: общий список фурнитуры
        services: список услуг с флагом enabled
        discount_type: 'процент' | 'фикс' | ''
        discount_value: число
    """
    errors: list[CalcError] = []
    structures_out = []
    structures_total = Decimal(0)
    for params in data.get("structures", []):
        res = calculate_structure(price, params)
        structures_out.append(res)
        structures_total += res["subtotal"]
        for err in res["errors"]:
            errors.append(CalcError(err["field"], err["message"], err["blocking"]))
    structures_total = money(structures_total)

    hw_total, hw_lines, hw_errors = calculate_hardware(data.get("hardware", []), price)
    errors.extend(hw_errors)

    svc_total, svc_lines, svc_errors = calculate_services(data.get("services", []), price)
    errors.extend(svc_errors)

    subtotal = money(structures_total + hw_total + svc_total)

    max_discount = price.rules.get("макс_скидка_менеджера_процент", 0)
    base_discount_pct = price.rules.get("базовый_процент_скидки", 0)
    dtype = data.get("discount_type", "")
    dvalue = data.get("discount_value", "")
    if (dtype in (None, "", "нет")) and base_discount_pct:
        dtype, dvalue = "процент", base_discount_pct

    if dtype in (None, "", "нет"):
        disc = {"before": subtotal, "discount": Decimal(0), "total": subtotal, "errors": []}
    else:
        disc = apply_discount(subtotal, dtype, dvalue, max_discount, is_admin=is_admin)
    for err in disc["errors"]:
        errors.append(CalcError(err["field"], err["message"], err["blocking"]))

    blocking = [e for e in errors if e.blocking]
    return {
        "structures": structures_out,
        "hardware": {"total": hw_total, "lines": hw_lines},
        "services": {"total": svc_total, "lines": svc_lines},
        "structures_total": structures_total,
        "subtotal": subtotal,
        "discount_type": dtype,
        "discount_value": dvalue,
        "discount": disc["discount"],
        "total": disc["total"],
        "errors": [e.to_dict() for e in errors],
        "ok": len(blocking) == 0,
        "price_version": price.version,
    }
