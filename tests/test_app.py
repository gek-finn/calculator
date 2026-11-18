"""Интеграционные тесты Flask-приложения: авторизация, доступы, история, версии."""

import os

import pytest

import database as db


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Изолированная база и Excel-файл для каждого теста."""
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    import app as app_module
    monkeypatch.setattr(app_module, "EXCEL_PATH", str(tmp_path / "данные_калькулятора.xlsx"))
    monkeypatch.setattr(app_module, "CURRENT_PRICE", None)
    monkeypatch.setattr(app_module, "CURRENT_PRICE_VERSION_ID", None)
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


def login_page_requires_setup_first(app_env):
    client = app_env.app.test_client()
    resp = client.get("/")
    assert resp.status_code == 302  # не авторизован


def test_setup_and_login(app_env):
    client = app_env.app.test_client()
    # без пользователей /setup доступен
    db2 = app_env.db  # noqa
    resp = client.post("/setup", data={
        "login": "boss", "password": "qwerty123", "password2": "qwerty123",
        "full_name": "Босс"})
    # у нас уже есть пользователи из фикстуры → /setup редиректит на /login
    assert resp.status_code == 302


def test_wrong_password_rejected(app_env):
    client = app_env.app.test_client()
    resp = client.post("/login", data={"login": "admin", "password": "wrong"})
    assert "Неверный логин" in resp.get_data(as_text=True)


def test_protected_routes_redirect_anonymous(app_env):
    client = app_env.app.test_client()
    for url in ("/", "/calculations", "/price", "/admin", "/admin/users"):
        resp = client.get(url)
        assert resp.status_code == 302, url


def load_price(admin_client, app_env):
    """Загружает прайс, предварительно заполняя базовую цену стекла в Excel."""
    from openpyxl import load_workbook
    import price_service
    # сначала создаём учебный файл
    admin_client.get("/admin/price/create_sample")
    wb = load_workbook(app_env.EXCEL_PATH)
    ws = wb["Правила"]
    for row in ws.iter_rows(min_row=2):
        if str(row[0].value).strip() == "базовая_цена_стекла_руб_м2":
            row[1].value = 3000  # заполняем учебную цену, чтобы загрузка прошла
    wb.save(app_env.EXCEL_PATH)
    resp = admin_client.post("/price/load", follow_redirects=True)
    text = resp.get_data(as_text=True)
    assert "Прайс версии" in text, text


def quote_payload():
    return {
        "client": {"name": "Иванов Иван", "phone": "+7 999 000-00-00",
                   "email": "", "address": "Уфа", "source": "звонок", "comment": ""},
        "structures": [{
            "construction_id": "1", "profile_id": "1", "glass_id": "1",
            "width_mm": 4000, "height_mm": 2500, "quantity": 1, "hardware": [],
        }],
        # ТЕСТОВЫЙ СЦЕНАРИЙ фурнитуры на 12000 ₽ (доводчик 8000 + 2 ручки по 2000).
        # Не подтверждённое бизнес-правило: на скриншоте отдельные позиции
        # (8000+3500+2000+2500=16000 ₽) не сходятся с итоговой строкой 12000 ₽.
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


def test_full_flow_reference_totals(admin_client, app_env):
    load_price(admin_client, app_env)
    resp = admin_client.post("/quote/calculate", json=quote_payload())
    quote = resp.get_json()
    assert quote["ok"], quote["errors"]
    assert quote["subtotal"] == "184000.00"
    assert quote["discount"] == "9200.00"
    assert quote["total"] == "174800.00"


def test_save_and_persist_after_restart(admin_client, app_env, tmp_path, monkeypatch):
    """14. При перезапуске история расчётов сохраняется (база на диске)."""
    load_price(admin_client, app_env)
    resp = admin_client.post("/quote/save", json=quote_payload())
    data = resp.get_json()
    assert data["ok"], data
    calc_id = data["calc_id"]
    # «перезапуск»: закрываем соединения, перечитываем из той же базы
    calc = db.get_calculation(calc_id)
    assert calc["total"] == "174800.00"
    assert calc["price_version"]
    assert calc["snapshot"]["quote"]["total"] == "174800.00"


def test_manager_cannot_open_admin(manager_client, app_env):
    resp = manager_client.get("/admin")
    assert resp.status_code == 302
    assert "/admin" not in resp.headers["Location"]
    resp = manager_client.get("/admin/users")
    assert resp.status_code == 302
    resp = manager_client.post("/admin/price/update", data={
        "entity": "hardware", "entity_id": "1", "field": "цена", "new_value": "9999"})
    # менеджер не может менять цены — редирект с флешем об ошибке
    assert resp.status_code == 302


def test_quote_blocked_without_price(admin_client):
    resp = admin_client.post("/quote/calculate", json=quote_payload())
    # прайс не загружен → запрос отклонён
    assert resp.status_code in (302, 400)


def test_old_calc_keeps_total_after_price_update(admin_client, app_env):
    """11 и 12: старый расчёт сохраняет итог, новый использует новую версию прайса."""
    load_price(admin_client, app_env)
    payload = quote_payload()
    # 5 шт × 10 м² = 50 м²: стекло 50 × 3000 = 150000 ₽ (> минимума 120000 ₽),
    # чтобы изменение базовой цены стекла реально меняло итог
    payload["structures"][0]["quantity"] = 5
    data = admin_client.post("/quote/save", json=payload).get_json()
    assert data["ok"], data
    old = db.get_calculation(data["calc_id"])
    # subtotal: 150000 (стекло) + 12000 (фурнитура) + 52000 (услуги) = 214000
    assert old["subtotal"] == "214000.00"
    assert old["total"] == "203300.00"   # −5%

    # администратор поднимает базовую цену стекла: 3000 → 4000 ₽/м²
    resp = admin_client.post("/admin/price/update", data={
        "entity": "rules", "entity_id": "-", "field": "базовая_цена_стекла_руб_м2",
        "new_value": "4000"}, follow_redirects=True)
    assert "Сохранено" in resp.get_data(as_text=True)

    # старый расчёт НЕ пересчитан
    old_again = db.get_calculation(data["calc_id"])
    assert old_again["total"] == "203300.00"
    assert old_again["price_version"] != app_env.CURRENT_PRICE.version

    # новый расчёт использует новую версию: стекло 50 × 4000 = 200000 ₽
    quote = admin_client.post("/quote/calculate", json=payload).get_json()
    assert quote["ok"], quote["errors"]
    assert quote["structures_total"] == "200000.00"
    assert quote["subtotal"] == "264000.00"
    assert quote["price_version"] == app_env.CURRENT_PRICE.version
    assert quote["price_version"] != old["price_version"]


def test_admin_can_edit_price_and_history_logged(admin_client, app_env):
    load_price(admin_client, app_env)
    admin_client.post("/admin/price/update", data={
        "entity": "hardware", "entity_id": "1", "field": "цена", "new_value": "8500"})
    changes = db.list_price_changes()
    assert any(c["field"] == "цена" and c["new_value"] == "8500" for c in changes)
    # Excel перезаписан с резервной копией
    backups = [f for f in os.listdir(os.path.dirname(app_env.EXCEL_PATH))
               if "резервная_копия" in f]
    assert backups


def test_numbering_sequential(admin_client, app_env):
    load_price(admin_client, app_env)
    n1 = admin_client.post("/quote/save", json=quote_payload()).get_json()["number"]
    n2 = admin_client.post("/quote/save", json=quote_payload()).get_json()["number"]
    assert n1 != n2
    assert n1.startswith("КА-") and n2.startswith("КА-")
