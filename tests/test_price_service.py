"""Тесты импорта и проверки прайса (price_service)."""

import os

from openpyxl import load_workbook

import price_service


def test_create_sample_and_load(tmp_path):
    path = str(tmp_path / "данные_калькулятора.xlsx")
    msg = price_service.create_sample_workbook(path)
    assert os.path.exists(path)
    # повторный вызов не перезаписывает файл
    msg2 = price_service.create_sample_workbook(path)
    assert "не будет перезаписан" in msg2 or "существует" in msg2
    price, errors, stats = price_service.load_price_from_file(path)
    # в учебном файле базовая цена стекла намеренно пуста → блокирующая ошибка
    assert price is None
    blocking = [e for e in errors if e["blocking"]]
    assert any("базовая цена стекла" in e["message"].lower() for e in blocking)
    assert stats["counts"]["Конструкции"] == 8


def test_sample_file_structure(tmp_path):
    path = str(tmp_path / "p.xlsx")
    price_service.create_sample_workbook(path)
    wb = load_workbook(path)
    for sheet in price_service.REQUIRED_SHEETS:
        assert sheet in wb.sheetnames, f"нет листа {sheet}"
    # 7 видов стекла с надбавками скриншота
    rows = price_service.read_workbook_rows(path)
    glass = {r["название"]: r["надбавка_процент"] for r in rows["Стекло"]}
    assert glass["Прозрачное закалённое"] == 0
    assert glass["Матовое"] == 10
    assert glass["Серое / бронза"] == 22
    assert glass["Серое / бронза матовое"] == 35
    assert glass["Осветлённое"] == 35
    assert glass["Осветлённое матовое"] == 40
    assert glass["Рифлёное"] == 40


def valid_rows():
    """Валидный набор строк (без пустых критичных цен)."""
    return {
        "Конструкции": [
            {"_строка": 2, "id": "1", "название": "Перегородка", "активна": "да",
             "единица_расчёта": "за_м2", "минимальная_стоимость": 120000,
             "описание": "", "базовая_цена": None},
        ],
        "Профили": [
            {"_строка": 2, "id": "1", "название": "38×38", "ширина_мм": 38,
             "высота_мм": 38, "цена_за_погонный_метр": 250, "активен": "да"},
        ],
        "Стекло": [
            {"_строка": 2, "id": "1", "название": "Прозрачное", "надбавка_процент": 0, "активно": "да"},
        ],
        "Фурнитура": [
            {"_строка": 2, "id": "1", "название": "Доводчик", "единица": "за_штуку",
             "цена": 8000, "активна": "да"},
        ],
        "Услуги": [
            {"_строка": 2, "id": "1", "название": "Монтаж", "тип_расчёта": "фиксированная",
             "цена": 35000, "единица": "заказ", "активна": "да"},
        ],
        "Правила": [
            {"_строка": 2, "ключ": "версия_прайса", "значение": "тест", "тип": "строка", "описание": ""},
            {"_строка": 3, "ключ": "базовая_цена_стекла_руб_м2", "значение": 3000,
             "тип": "число", "описание": ""},
        ],
        "История_версий": [
            {"_строка": 2, "версия": "тест", "дата_создания": "01.01.2026",
             "имя_файла": "p.xlsx", "комментарий": ""},
        ],
    }


def test_valid_rows_accepted():
    errors = price_service.validate_rows(valid_rows())
    assert not [e for e in errors if e["blocking"]]


def test_duplicate_id_rejected():
    rows = valid_rows()
    rows["Стекло"].append({"_строка": 3, "id": "1", "название": "Дубль",
                           "надбавка_процент": 5, "активно": "да"})
    errors = price_service.validate_rows(rows)
    assert any("Дубликат id" in e["message"] for e in errors)


def test_bad_unit_rejected():
    rows = valid_rows()
    rows["Конструкции"][0]["единица_расчёта"] = "за_тонну"
    errors = price_service.validate_rows(rows)
    assert any("Недопустимая единица" in e["message"] for e in errors)


def test_non_numeric_price_rejected():
    rows = valid_rows()
    rows["Фурнитура"][0]["цена"] = "восемь тысяч"
    errors = price_service.validate_rows(rows)
    assert any("не число" in e["message"] for e in errors)


def test_missing_sheet_rejected():
    rows = valid_rows()
    del rows["Услуги"]
    errors = price_service.validate_rows(rows)
    assert any("Отсутствует обязательный лист" in e["message"] for e in errors)


def test_missing_glass_price_is_blocking():
    rows = valid_rows()
    rows["Правила"][1]["значение"] = None
    errors = price_service.validate_rows(rows)
    assert any(e["blocking"] and "базовая цена стекла" in e["message"].lower()
               for e in errors)


def test_missing_profile_price_is_warning_not_blocker():
    rows = valid_rows()
    rows["Профили"][0]["цена_за_погонный_метр"] = None
    errors = price_service.validate_rows(rows)
    blocking = [e for e in errors if e["blocking"]]
    assert not blocking
    assert any("Цена погонного метра" in e["message"] for e in errors)


def test_corrupt_file_rejected(tmp_path):
    path = tmp_path / "битый.xlsx"
    path.write_bytes(b"this is not an excel file")
    price, errors, stats = price_service.load_price_from_file(str(path))
    assert price is None
    assert errors


def test_missing_file_rejected(tmp_path):
    price, errors, _stats = price_service.load_price_from_file(str(tmp_path / "нет.xlsx"))
    assert price is None
    assert any("не найден" in e["message"] for e in errors)


def test_build_price_and_export_roundtrip(tmp_path):
    rows = valid_rows()
    price = price_service.build_price_data(rows, loaded_at="01.01.2026 12:00:00")
    assert price.version == "тест"
    assert price.rules["базовая_цена_стекла_руб_м2"] == 3000
    path = str(tmp_path / "out.xlsx")
    price_service.write_workbook_from_price(price, path, create_backup=False)
    price2, errors, _stats = price_service.load_price_from_file(path)
    assert price2 is not None, errors
    assert price2.version == "тест"
    assert price2.glasses["1"]["надбавка_процент"] == 0


def test_export_creates_backup(tmp_path):
    rows = valid_rows()
    price = price_service.build_price_data(rows)
    path = str(tmp_path / "out.xlsx")
    price_service.write_workbook_from_price(price, path, create_backup=False)
    price.version = "2.0"
    price_service.write_workbook_from_price(price, path, create_backup=True)
    backups = [f for f in os.listdir(tmp_path) if "резервная_копия" in f]
    assert len(backups) == 1
