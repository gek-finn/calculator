"""Тесты пересчёта по актуальному прайсу и экспорта PDF/XLSX.

Покрытие обязательных требований:
 1. тот же прайс → тот же итог;
 2. изменившийся прайс → изменённый итог (в новой версии);
 3. исходный расчёт не изменяется при пересчёте;
 4. новая версия хранит новые цены и версию прайса;
 5. отсутствующая позиция блокирует пересчёт и никогда не заменяется нулём;
 6. некорректный Excel-файл не повреждает ничего;
 7. без явного подтверждения пересчёт не выполняется (в т.ч. предупреждения);
 8. менеджер не может пересчитать чужой расчёт, но может свой;
 9. PDF-экспорт содержит кириллицу;
10. XLSX-экспорт содержит листы Итог/Конструкции/Детализация/Версия прайса;
11. экспорт старой версии использует старые цены после обновления прайса;
12. история пересчётов переживает перезапуск;
13. существующая логика калькулятора продолжает работать.
"""

import io
from decimal import Decimal

import pytest

import database as db


# ------------------------------------------------------------- фикстуры

@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Изолированная база и Excel-файл для каждого теста."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    import app as app_module
    monkeypatch.setattr(app_module, "EXCEL_PATH",
                        str(tmp_path / "данные_калькулятора.xlsx"))
    monkeypatch.setattr(app_module, "CURRENT_PRICE", None)
    monkeypatch.setattr(app_module, "CURRENT_PRICE_VERSION_ID", None)
    monkeypatch.setattr(app_module, "EXPORT_DIR", str(tmp_path / "exports"))
    db.init_db()
    db.create_user("admin", "secret123", "admin", "Администратор")
    db.create_user("manager", "pass456", "manager", "Менеджер")
    app_module.app.config["TESTING"] = True
    return app_module


@pytest.fixture()
def admin_client(app_env):
    client = app_env.app.test_client()
    client.post("/login", data={"login": "admin", "password": "secret123"})
    return client


@pytest.fixture()
def manager_client(app_env):
    client = app_env.app.test_client()
    client.post("/login", data={"login": "manager", "password": "pass456"})
    return client


def load_price(admin_client, app_env):
    """Загружает прайс, предварительно заполняя базовую цену стекла в Excel."""
    from openpyxl import load_workbook
    admin_client.get("/admin/price/create_sample")
    wb = load_workbook(app_env.EXCEL_PATH)
    ws = wb["Правила"]
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value).strip() == "базовая_цена_стекла_руб_м2":
            row[1].value = 3000
    wb.save(app_env.EXCEL_PATH)
    resp = admin_client.post("/price/load", follow_redirects=True)
    assert "Прайс версии" in resp.get_data(as_text=True)


def quote_payload():
    return {
        "client": {"name": "Иванов Иван", "phone": "+7 999 000-00-00",
                   "email": "", "address": "Уфа", "source": "звонок", "comment": ""},
        "structures": [{
            "construction_id": "1", "profile_id": "1", "glass_id": "1",
            "width_mm": 4000, "height_mm": 2500, "quantity": 1, "hardware": [],
        }],
        "hardware": [
            {"hardware_id": "1", "quantity": 1},
            {"hardware_id": "3", "quantity": 2},
        ],
        "services": [
            {"service_id": "1", "enabled": True},
            {"service_id": "2", "enabled": True},
            {"service_id": "3", "enabled": True},
            {"service_id": "4", "enabled": True},
            {"service_id": "5", "enabled": False},
        ],
        "discount_type": "процент",
        "discount_value": "5",
    }


def save_calc(client, qty=1):
    payload = quote_payload()
    payload["structures"][0]["quantity"] = qty
    data = client.post("/quote/save", json=payload).get_json()
    assert data["ok"], data
    return data


def preview(client, calc_id, **extra):
    body = {"price_source": "current"}
    body.update(extra)
    return client.post(f"/calculations/{calc_id}/recalc/preview",
                       json=body).get_json()


def confirm(client, calc_id, **extra):
    body = {"price_source": "current", "confirm": True}
    body.update(extra)
    return client.post(f"/calculations/{calc_id}/recalc/confirm",
                       json=body).get_json()


def raise_glass_price(admin_client, new_price="4000"):
    resp = admin_client.post("/admin/price/update", data={
        "entity": "rules", "entity_id": "-",
        "field": "базовая_цена_стекла_руб_м2", "new_value": new_price},
        follow_redirects=True)
    assert "Сохранено" in resp.get_data(as_text=True)


# ------------------------------------------------------------- 1–2. итоги

def test_recalc_same_price_same_total(admin_client, app_env):
    """1. Пересчёт по неизменённому прайсу даёт тот же итог."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]
    assert db.get_calculation(calc_id)["total"] == "174800.00"

    prev = preview(admin_client, calc_id)
    assert prev["ok"], prev["errors"]
    assert Decimal(prev["old_total"]) == Decimal("174800.00")
    assert Decimal(prev["new_total"]) == Decimal("174800.00")
    labels = [r["label"] for r in prev["comparison"]]
    assert "Итог" in labels and "Конструкции" in labels

    conf = confirm(admin_client, calc_id)
    assert conf["ok"], conf
    assert conf["version_no"] == 2
    new = db.get_calculation(conf["calc_id"])
    assert new["total"] == "174800.00"
    assert Decimal(new["difference"]) == Decimal("0")


def test_recalc_price_change_adjusts_total(admin_client, app_env):
    """2. Изменение прайса меняет итог новой версии (5 шт × 10 м² = 50 м²)."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client, qty=5)["calc_id"]
    assert db.get_calculation(calc_id)["total"] == "203300.00"  # стекло 3000 ₽/м²

    raise_glass_price(admin_client, "4000")  # стекло 4000 ₽/м²

    prev = preview(admin_client, calc_id)
    assert prev["ok"], prev["errors"]
    assert Decimal(prev["new_total"]) == Decimal("250800.00")   # 264000 − 5%
    glass = next(r for r in prev["comparison"] if r["label"] == "Стекло")
    assert Decimal(glass["old"]) == Decimal("150000")
    assert Decimal(glass["new"]) == Decimal("200000")

    conf = confirm(admin_client, calc_id)
    assert conf["ok"], conf
    new = db.get_calculation(conf["calc_id"])
    assert new["total"] == "250800.00"
    assert Decimal(new["difference"]) == Decimal("47500")


# ------------------------------------------------------------- 3. неизменность исходного

def test_recalc_does_not_modify_original(admin_client, app_env):
    """3. Исходный расчёт (цены, скидка, дата, итог) неизменен после пересчёта."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client, qty=5)["calc_id"]
    before = db.get_calculation(calc_id)

    raise_glass_price(admin_client, "4000")
    conf = confirm(admin_client, calc_id)
    assert conf["ok"], conf

    after = db.get_calculation(calc_id)
    for key in ("number", "total", "subtotal", "discount", "price_version",
                "snapshot_json", "created_at", "user_login"):
        assert after[key] == before[key], key
    assert after["total"] == "203300.00"

    versions = db.list_calculation_versions(calc_id)
    assert [v["version_no"] for v in versions] == [1, 2]
    assert versions[0]["id"] == calc_id
    assert versions[1]["id"] == conf["calc_id"]


# ------------------------------------------------------------- 4. цены и версия новой версии

def test_new_version_uses_new_price_snapshot(admin_client, app_env):
    """4. Новая версия хранит новые цены и новую версию прайса; исходная — старые."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client, qty=5)["calc_id"]

    raise_glass_price(admin_client, "4000")
    conf = confirm(admin_client, calc_id)
    assert conf["ok"], conf

    new = db.get_calculation(conf["calc_id"])
    old = db.get_calculation(calc_id)
    assert new["parent_id"] == calc_id
    assert new["version_no"] == 2
    assert new["price_version"] == app_env.CURRENT_PRICE.version
    assert new["price_version"] != old["price_version"]
    assert str(new["snapshot"]["price"]["rules"]["базовая_цена_стекла_руб_м2"]) == "4000"
    assert str(old["snapshot"]["price"]["rules"]["базовая_цена_стекла_руб_м2"]) == "3000"

    # метаданные пересчёта
    assert new["recalc_confirmed"] == 1
    assert new["recalc_user"] == "admin"
    assert new["recalculated_at"]
    assert new["old_total"] == "203300.00"
    assert new["new_total"] == "250800.00"
    assert new["difference"] == "47500.00"


# ------------------------------------------------------------- 5. отсутствие ≠ ноль

def test_missing_position_blocks_and_is_never_zeroed(admin_client, app_env):
    """5. Отсутствующая в новом прайсе позиция блокирует пересчёт, а не обнуляется."""
    import recalc
    from calculator import PriceData

    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]
    calc = db.get_calculation(calc_id)

    new_price = PriceData.from_dict(app_env.CURRENT_PRICE.to_dict())
    del new_price.hardware["1"]  # позиция исчезла из прайса

    result = recalc.analyze(calc, new_price)
    assert result["ok"] is False
    assert result["new_quote"] is None            # итог НЕ подставлен нулевой
    assert any(m["id"] == "1" for m in result["unresolved_missing"])
    missing = next(m for m in result["unresolved_missing"] if m["id"] == "1")
    assert missing["required"] is True
    assert missing["candidates"], "должны предлагаться замены"
    assert any("не найдена" in e["message"] for e in result["errors"])

    # исходный расчёт цел, новая версия не создана
    assert db.get_calculation(calc_id)["total"] == "174800.00"
    assert len(db.list_calculation_versions(calc_id)) == 1


# ------------------------------------------------------------- 6. битый Excel

def test_invalid_excel_cancels_recalc_and_damages_nothing(admin_client, app_env):
    """6. Некорректный Excel-файл: пересчёт отменён, данные не пострадали."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]
    versions_before = len(db.list_price_versions())
    price_before = app_env.CURRENT_PRICE.to_dict()

    with open(app_env.EXCEL_PATH, "wb") as fh:
        fh.write(b"this is not an xlsx file at all")

    prev = preview(admin_client, calc_id, price_source="excel")
    assert prev["ok"] is False
    assert prev["errors"]

    conf = confirm(admin_client, calc_id, price_source="excel")
    assert conf["ok"] is False

    assert app_env.CURRENT_PRICE.to_dict() == price_before
    assert len(db.list_price_versions()) == versions_before
    assert len(db.list_calculation_versions(calc_id)) == 1
    assert db.get_calculation(calc_id)["total"] == "174800.00"


# ------------------------------------------------------------- 7. только с подтверждением

def test_no_recalc_without_explicit_confirmation(admin_client, app_env):
    """7. Пересчёт требует явного подтверждения; предупреждения — отдельного."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]

    # превью ничего не сохраняет
    preview(admin_client, calc_id)
    assert len(db.list_calculation_versions(calc_id)) == 1

    # подтверждение отсутствует или отрицательное
    for body in ({"price_source": "current"},
                 {"price_source": "current", "confirm": False}):
        res = admin_client.post(f"/calculations/{calc_id}/recalc/confirm",
                                json=body).get_json()
        assert res["ok"] is False
        assert any("не подтверждён" in e["message"] for e in res["errors"])
    assert len(db.list_calculation_versions(calc_id)) == 1

    # изменение правила прайса → предупреждение, требующее отдельного «ознакомлен»
    price = app_env.CURRENT_PRICE
    assert str(price.rules.get("проверка_мин_стоимости")) == "да"
    price.rules["проверка_мин_стоимости"] = "нет"

    prev = preview(admin_client, calc_id)
    assert prev["ok"], prev["errors"]
    assert any("проверка_мин_стоимости" in w for w in prev["warnings"])

    res = admin_client.post(f"/calculations/{calc_id}/recalc/confirm", json={
        "price_source": "current", "confirm": True}).get_json()
    assert res["ok"] is False
    assert any("предупреждения" in e["message"].lower() for e in res["errors"])
    assert len(db.list_calculation_versions(calc_id)) == 1

    res = admin_client.post(f"/calculations/{calc_id}/recalc/confirm", json={
        "price_source": "current", "confirm": True, "ack_warnings": True}).get_json()
    assert res["ok"], res.get("errors")
    assert len(db.list_calculation_versions(calc_id)) == 2


# ------------------------------------------------------------- 8. права менеджера

def test_manager_cannot_recalc_foreign_calc(admin_client, manager_client, app_env):
    """8. Менеджер не пересчитывает чужой расчёт, но пересчитывает свой."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]

    resp = manager_client.get(f"/calculations/{calc_id}/recalc")
    assert resp.status_code == 302
    assert "recalc" not in resp.headers["Location"]
    resp = manager_client.post(f"/calculations/{calc_id}/recalc/preview",
                               json={"price_source": "current"})
    assert resp.status_code == 403
    resp = manager_client.post(f"/calculations/{calc_id}/recalc/confirm",
                               json={"price_source": "current", "confirm": True})
    assert resp.status_code == 403
    assert len(db.list_calculation_versions(calc_id)) == 1

    # менеджер может пересчитать СВОЙ расчёт
    own_id = save_calc(manager_client)["calc_id"]
    prev = preview(manager_client, own_id)
    assert prev["ok"], prev.get("errors")
    conf = confirm(manager_client, own_id)
    assert conf["ok"], conf


# ------------------------------------------------------------- 9. PDF с кириллицей

def test_pdf_export_contains_cyrillic(admin_client, app_env):
    """9. PDF содержит кириллицу: заголовок, клиента, суммы."""
    from urllib.parse import unquote

    from pypdf import PdfReader

    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]

    resp = admin_client.get(f"/calculations/{calc_id}/export/pdf")
    assert resp.status_code == 200
    assert resp.data[:5] == b"%PDF-"
    # имя файла с кириллицей (Werkzeug отдаёт его в RFC 5987: filename*=UTF-8'')
    disposition = unquote(resp.headers.get("Content-Disposition", ""))
    assert "Расчет_" in disposition

    reader = PdfReader(io.BytesIO(resp.data))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    assert "ЛидерПро" in text
    assert "Иванов" in text
    assert "174" in text  # итог 174 800,00 ₽


# ------------------------------------------------------------- 10. листы XLSX

def test_xlsx_export_has_required_sheets(admin_client, app_env):
    """10. XLSX содержит листы Итог/Конструкции/Детализация/Версия прайса."""
    from openpyxl import load_workbook

    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]

    resp = admin_client.get(f"/calculations/{calc_id}/export/xlsx")
    assert resp.status_code == 200
    wb = load_workbook(io.BytesIO(resp.data), data_only=True)
    assert wb.sheetnames == ["Итог", "Конструкции", "Детализация", "Версия прайса"]

    итог = {row[0].value: row[1].value
            for row in wb["Итог"].iter_rows() if row[0].value}
    assert итог["Компания"] == "ЛидерПро"
    assert итог["Клиент"] == "Иванов Иван"
    assert float(итог["Итого к оплате"]) == 174800.0

    rows = list(wb["Конструкции"].iter_rows(min_row=2, values_only=True))
    assert rows and rows[0][1] == "Межкомнатная перегородка"

    assert wb["Версия прайса"]["B1"].value == \
        db.get_calculation(calc_id)["price_version"]


# ------------------------------------------------------------- 11. экспорт старых цен

def test_old_version_export_uses_old_prices_after_update(admin_client, app_env):
    """11. После обновления прайса экспорт старой версии использует её снимок."""
    from openpyxl import load_workbook

    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]

    raise_glass_price(admin_client, "4000")

    resp = admin_client.get(f"/calculations/{calc_id}/export/xlsx")
    wb = load_workbook(io.BytesIO(resp.data), data_only=True)
    ws = wb["Версия прайса"]
    rules = {row[0]: row[1] for row in ws.iter_rows(min_row=5, values_only=True)
             if row[0] and row[1] is not None}
    assert rules["базовая_цена_стекла_руб_м2"] == "3000"      # старая цена

    итог = {row[0].value: row[1].value
            for row in wb["Итог"].iter_rows() if row[0].value}
    assert float(итог["Итого к оплате"]) == 174800.0           # старый итог
    assert итог["Версия прайса"] != app_env.CURRENT_PRICE.version


# ------------------------------------------------------------- 12. перезапуск

def test_history_and_recalc_survive_restart(admin_client, app_env, monkeypatch):
    """12. Версии расчётов и прайс восстанавливаются после перезапуска."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client, qty=5)["calc_id"]
    raise_glass_price(admin_client, "4000")
    conf = confirm(admin_client, calc_id)
    assert conf["ok"], conf

    # «перезапуск»: сбрасываем память, читаем заново с диска той же базы
    monkeypatch.setattr(app_env, "CURRENT_PRICE", None)
    monkeypatch.setattr(app_env, "CURRENT_PRICE_VERSION_ID", None)
    app_env.load_latest_price_into_memory()
    assert app_env.CURRENT_PRICE is not None

    root = db.get_calculation_root_id(db.get_calculation(conf["calc_id"]))
    assert root == calc_id
    versions = db.list_calculation_versions(root)
    assert [v["version_no"] for v in versions] == [1, 2]
    assert versions[0]["total"] == "203300.00"
    assert versions[1]["total"] == "250800.00"
    assert str(versions[1]["difference"]) == "47500.00"

    new = db.get_calculation(conf["calc_id"])
    assert app_env.CURRENT_PRICE.version == new["price_version"]


# ------------------------------------------------------------- 13. калькулятор цел

def test_existing_calculator_logic_still_works(admin_client, app_env):
    """13. Модуль calculator работает без изменений и совпадает с HTTP-расчётом."""
    from calculator import calculate_quote, calculate_structure
    import price_service

    load_price(admin_client, app_env)
    price, errors, _stats = price_service.load_price_from_file(app_env.EXCEL_PATH)
    assert price is not None, errors

    payload = quote_payload()
    res = calculate_structure(price, payload["structures"][0])
    assert not [e for e in res["errors"] if e["blocking"]]
    assert Decimal(str(res["area_total"])) == Decimal("10")     # 4 × 2.5 м²

    quote = calculate_quote(price, payload)
    assert quote["ok"], quote["errors"]
    assert str(quote["subtotal"]) == "184000.00"
    assert str(quote["total"]) == "174800.00"

    http_quote = admin_client.post("/quote/calculate", json=payload).get_json()
    assert http_quote["ok"], http_quote["errors"]
    assert http_quote["subtotal"] == "184000.00"
    assert http_quote["total"] == str(quote["total"])


# ------------------------------------------------------------- 14. что изменилось в прайсе

def change_entity(admin_client, entity, entity_id, field, new_value):
    resp = admin_client.post("/admin/price/update", data={
        "entity": entity, "entity_id": entity_id,
        "field": field, "new_value": new_value}, follow_redirects=True)
    assert "Сохранено" in resp.get_data(as_text=True)


def test_price_changes_empty_when_price_unchanged(admin_client, app_env):
    """14. Если прайс не менялся, список изменений прайса пуст."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client)["calc_id"]

    prev = preview(admin_client, calc_id)
    assert prev["ok"], prev["errors"]
    assert prev["price_changes"] == []


def test_price_changes_reports_non_binding_change(admin_client, app_env):
    """15. Не влияющее на итог изменение прайса показывается пользователю.

    Сценарий пользователя: изменена «цена» конструкции (минимальная стоимость),
    но она ниже расчётной стоимости, поэтому пересчёт даёт тот же итог, а список
    изменений прайса объясняет, что именно изменилось.
    """
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client, qty=5)["calc_id"]

    change_entity(admin_client, "constructions", "1", "минимальная_стоимость",
                  "100000")  # было 120000; обе ниже расчётной стоимости (150000)

    prev = preview(admin_client, calc_id)
    assert prev["ok"], prev["errors"]
    assert Decimal(prev["new_total"]) == Decimal(prev["old_total"])
    assert all(Decimal(r["diff"]) == 0 for r in prev["comparison"])

    entry = next(c for c in prev["price_changes"]
                 if c["field"] == "минимальная_стоимость")
    assert entry["kind"] == "constructions" and entry["id"] == "1"
    assert entry["name"] == "Межкомнатная перегородка"
    assert Decimal(str(entry["old"])) == Decimal("120000")
    assert Decimal(str(entry["new"])) == Decimal("100000")


def test_price_changes_reports_used_rule(admin_client, app_env):
    """16. Изменение правила прайса попадает в список изменений."""
    load_price(admin_client, app_env)
    calc_id = save_calc(admin_client, qty=5)["calc_id"]

    raise_glass_price(admin_client, "4000")

    prev = preview(admin_client, calc_id)
    assert prev["ok"], prev["errors"]
    assert Decimal(prev["new_total"]) != Decimal(prev["old_total"])
    keys = {(c["kind"], c["id"]) for c in prev["price_changes"]}
    assert ("rules", "базовая_цена_стекла_руб_м2") in keys
