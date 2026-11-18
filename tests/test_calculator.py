"""Тесты модуля расчёта calculator.py."""

from decimal import Decimal

import pytest

from calculator import (
    BY_AREA, BY_PIECE, FIXED, PROFILE_RULE_OFF,
    PriceData, apply_discount, area_m2, calculate_quote, calculate_structure,
)


def make_price() -> PriceData:
    """Прайс для учебного теста: воспроизводит суммы с референсного скриншота."""
    return PriceData(
        version="тест-1",
        loaded_at="01.01.2026 00:00:00",
        constructions={
            "1": {"id": "1", "название": "Межкомнатная перегородка", "активна": True,
                  "единица_расчёта": BY_AREA, "минимальная_стоимость": Decimal("120000"),
                  "базовая_цена": None, "описание": ""},
        },
        profiles={
            "1": {"id": "1", "название": "Профиль 38×38 мм", "ширина_мм": Decimal("38"),
                  "высота_мм": Decimal("38"), "цена_за_погонный_метр": None, "активен": True},
        },
        glasses={
            "1": {"id": "1", "название": "Прозрачное закалённое", "надбавка_процент": Decimal("0"), "активно": True},
            "2": {"id": "2", "название": "Матовое", "надбавка_процент": Decimal("10"), "активно": True},
            "3": {"id": "3", "название": "Серое / бронза", "надбавка_процент": Decimal("22"), "активно": True},
        },
        hardware={
            "1": {"id": "1", "название": "Доводчик", "единица": BY_PIECE, "цена": Decimal("8000"), "активна": True},
            "2": {"id": "2", "название": "Замок", "единица": BY_PIECE, "цена": Decimal("3500"), "активна": True},
            "3": {"id": "3", "название": "Ручка", "единица": BY_PIECE, "цена": Decimal("2000"), "активна": True},
            "4": {"id": "4", "название": "Закладная", "единица": BY_PIECE, "цена": Decimal("2500"), "активна": True},
        },
        services={
            "1": {"id": "1", "название": "Дополнительные материалы", "тип_расчёта": FIXED,
                  "цена": Decimal("8500"), "единица": "заказ", "активна": True},
            "2": {"id": "2", "название": "Замер", "тип_расчёта": FIXED,
                  "цена": Decimal("2500"), "единица": "заказ", "активна": True},
            "3": {"id": "3", "название": "Доставка по Уфе", "тип_расчёта": FIXED,
                  "цена": Decimal("6000"), "единица": "заказ", "активна": True},
            "4": {"id": "4", "название": "Монтаж", "тип_расчёта": FIXED,
                  "цена": Decimal("35000"), "единица": "заказ", "активна": True},
            "5": {"id": "5", "название": "Проживание", "тип_расчёта": FIXED,
                  "цена": Decimal("0"), "единица": "заказ", "активна": False},
        },
        rules={
            "базовая_цена_стекла_руб_м2": Decimal("3000"),
            "профиль_правило_расчёта": PROFILE_RULE_OFF,
            "проверка_мин_стоимости": "да",
            "макс_скидка_менеджера_процент": Decimal("10"),
            "базовый_процент_скидки": 0,
        },
    )


# 1. Площадь 4000 × 2500 мм = 10 м²
def test_area_4000x2500():
    assert area_m2(4000, 2500) == Decimal("10")


def test_area_rejects_zero():
    with pytest.raises(ValueError):
        area_m2(0, 2500)


def reference_quote(price, glass_id="1", hw_items=None, services=None,
                    discount_type="", discount_value="", width=4000, height=2500, qty=1):
    """Сборка учебного расчёта по референсу: 4000×2500, 1 шт."""
    if hw_items is None:
        # ТЕСТОВЫЙ СЦЕНАРИЙ фурнитуры на 12000 ₽.
        # ВНИМАНИЕ: это не подтверждённое бизнес-правило. На скриншоте сумма
        # итоговой строки «фурнитура» равна 12000 ₽, однако сумма отдельно
        # отмеченных позиций (доводчик 8000 + замок 3500 + ручка 2000 +
        # закладная 2500 = 16000 ₽) с ней НЕ совпадает. Тест воспроизводит
        # итоговую сумму референса подбором позиций, а не утверждает её
        # как истинное правило прайса.
        hw_items = [
            {"hardware_id": "1", "quantity": 1},  # доводчик 8000
            {"hardware_id": "3", "quantity": 2},  # 2 ручки по 2000 = 4000
        ]
    if services is None:
        services = [
            {"service_id": "1", "enabled": True},   # доп. материалы 8500
            {"service_id": "2", "enabled": True},   # замер 2500
            {"service_id": "3", "enabled": True},   # доставка 6000
            {"service_id": "4", "enabled": True},   # монтаж 35000
            {"service_id": "5", "enabled": False},  # проживание 0 ₽, отключено
        ]
    return calculate_quote(price, {
        "structures": [{
            "construction_id": "1", "profile_id": "1", "glass_id": glass_id,
            "width_mm": width, "height_mm": height, "quantity": qty,
            "hardware": [],
        }],
        "hardware": hw_items,
        "services": services,
        "discount_type": discount_type,
        "discount_value": discount_value,
    })


# 2, 3, 4, 5. Референсная сумма
def test_reference_totals():
    price = make_price()
    quote = reference_quote(price)
    assert quote["ok"], quote["errors"]
    # базовая конструкция (применён минимум 120000 ₽)
    assert quote["structures_total"] == Decimal("120000.00")
    # фурнитура — тестовый сценарий 12000 ₽ (см. пояснение в reference_quote)
    assert quote["hardware"]["total"] == Decimal("12000.00")
    # услуги: 8500 + 2500 + 6000 + 35000
    assert quote["services"]["total"] == Decimal("52000.00")
    # сумма до скидки — 184000 ₽
    assert quote["subtotal"] == Decimal("184000.00")


def test_discount_5_percent():
    quote = reference_quote(make_price(), discount_type="процент", discount_value="5")
    assert quote["discount"] == Decimal("9200.00")
    assert quote["total"] == Decimal("174800.00")


# 6. Изменение размеров пересчитывает площадь
def test_size_change_recalculates_area():
    price = make_price()
    res = calculate_structure(price, {
        "construction_id": "1", "glass_id": "1", "profile_id": "1",
        "width_mm": 3000, "height_mm": 2000, "quantity": 1})
    assert res["area_one"] == Decimal("6")
    assert res["area_total"] == Decimal("6")
    # 6 м² × 3000 ₽/м² = 18000 ₽ < минимума 120000 ₽ → применён минимум
    assert res["subtotal"] == Decimal("120000.00")


# 7. Изменение количества пересчитывает итог
def test_quantity_change_recalculates_total():
    price = make_price()
    res1 = calculate_structure(price, {
        "construction_id": "1", "glass_id": "1", "profile_id": "1",
        "width_mm": 4000, "height_mm": 2500, "quantity": 1})
    res2 = calculate_structure(price, {
        "construction_id": "1", "glass_id": "1", "profile_id": "1",
        "width_mm": 4000, "height_mm": 2500, "quantity": 3})
    assert res2["area_total"] == Decimal("30")
    # 30 м² × 3000 ₽ = 90000 ₽ — всё ещё меньше минимума 120000 ₽
    assert res2["subtotal"] == Decimal("120000.00")
    # а 5 шт → 50 м² × 3000 = 150000 ₽ > минимума
    res3 = calculate_structure(price, {
        "construction_id": "1", "glass_id": "1", "profile_id": "1",
        "width_mm": 4000, "height_mm": 2500, "quantity": 5})
    assert res3["subtotal"] == Decimal("150000.00")


# 8. Изменение стекла пересчитывает надбавку
def test_glass_surcharge_recalculation():
    price = make_price()
    base = calculate_structure(price, {
        "construction_id": "1", "glass_id": "1", "profile_id": "1",
        "width_mm": 4000, "height_mm": 2500, "quantity": 1})
    matte = calculate_structure(price, {
        "construction_id": "1", "glass_id": "2", "profile_id": "1",
        "width_mm": 4000, "height_mm": 2500, "quantity": 1})
    # прозрачное: 3000 ₽/м² × 10 м² = 30000; матовое: 3300 ₽/м² × 10 = 33000
    assert base["subtotal"] == Decimal("120000.00")   # минимум перекрывает
    # проверим именно стоимость стекла в детализации
    def glass_amount(res):
        return [line for line in res["breakdown"] if line["name"].startswith("Стекло")][0]["amount"]
    assert glass_amount(base) == Decimal("30000.00")
    assert glass_amount(matte) == Decimal("33000.00")


# 9. Выключение услуги исключает её стоимость
def test_disabled_service_excluded():
    price = make_price()
    with_montage = reference_quote(price)
    without_montage = reference_quote(price, services=[
        {"service_id": "1", "enabled": True},
        {"service_id": "2", "enabled": True},
        {"service_id": "3", "enabled": True},
        {"service_id": "4", "enabled": False},   # монтаж выключен
        {"service_id": "5", "enabled": False},
    ])
    assert with_montage["services"]["total"] - without_montage["services"]["total"] == Decimal("35000.00")


# Незаполненная базовая цена стекла блокирует расчёт (не превращается в ноль)
def test_missing_glass_base_price_blocks_quote():
    price = make_price()
    price.rules["базовая_цена_стекла_руб_м2"] = None
    quote = reference_quote(price)
    assert not quote["ok"]
    assert any("базовая цена стекла" in e["message"].lower() for e in quote["errors"])


# Незаполненная цена профиля при включённом правиле блокирует расчёт
def test_missing_profile_price_blocks_when_rule_enabled():
    price = make_price()
    price.rules["профиль_правило_расчёта"] = "периметр"
    quote = reference_quote(price)
    assert not quote["ok"]


# Правило погонных метров применяется только явно
def test_profile_rule_disabled_by_default():
    price = make_price()
    res = calculate_structure(price, {
        "construction_id": "1", "glass_id": "1", "profile_id": "1",
        "width_mm": 4000, "height_mm": 2500, "quantity": 1})
    assert not any(line["name"].startswith("Профиль") for line in res["breakdown"])


# Скидка: ограничение менеджера и неотрицательный итог
def test_manager_discount_limit():
    res = apply_discount(Decimal("1000"), "процент", 50, 10, is_admin=False)
    assert res["discount"] == Decimal("100.00")
    assert any("больше 10%" in e["message"] for e in res["errors"])


def test_admin_can_give_larger_discount():
    res = apply_discount(Decimal("1000"), "процент", 50, 10, is_admin=True)
    assert res["discount"] == Decimal("500.00")
    assert not res["errors"]


def test_discount_cannot_be_negative():
    res = apply_discount(Decimal("1000"), "процент", -5, 10, is_admin=True)
    assert res["errors"]


def test_fixed_discount_capped_at_total():
    res = apply_discount(Decimal("500"), "фикс", 99999, 100, is_admin=True)
    assert res["total"] == Decimal("0.00")


# Минимум применяется один раз к конструкции и показан в детализации
def test_minimum_cost_shown_in_breakdown():
    price = make_price()
    res = calculate_structure(price, {
        "construction_id": "1", "glass_id": "1", "profile_id": "1",
        "width_mm": 1000, "height_mm": 1000, "quantity": 1})
    names = [line["name"] for line in res["breakdown"]]
    assert any("Минимальная стоимость" in n for n in names)
    assert res["min_cost_applied"] == Decimal("120000.00")
