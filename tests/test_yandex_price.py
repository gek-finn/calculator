"""Тесты обновления прайса с Яндекс Диска.

Все сетевые обращения подменяются — тесты НЕ зависят от реального Яндекс Диска.
Живой тест реальной ссылки запускается только при YANDEX_LIVE_TEST=1.
"""

import copy
import os
import socket
from decimal import Decimal

import pytest
from openpyxl import Workbook

import database as db
import price_diff
import price_service
import yandex_disk

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
XLS_FIXTURE = os.path.join(FIXTURES, "price_valid.xls")
PUBLIC_URL = "https://disk.yandex.ru/d/YOUR_PUBLIC_PRICE_LINK"

SHEET_HEADERS = {
    "Конструкции": ["id", "название", "активна", "единица_расчёта",
                    "минимальная_стоимость", "описание", "базовая_цена"],
    "Профили": ["id", "название", "ширина_мм", "высота_мм",
                "цена_за_погонный_метр", "активен"],
    "Стекло": ["id", "название", "надбавка_процент", "активно"],
    "Фурнитура": ["id", "название", "единица", "цена", "активна"],
    "Услуги": ["id", "название", "тип_расчёта", "цена", "единица", "активна"],
    "Правила": ["ключ", "значение", "тип", "описание"],
    "История_версий": ["версия", "дата_создания", "имя_файла", "комментарий"],
}

DEFAULT_ROWS = {
    "Конструкции": [
        [1, "Перегородка", "да", "за_м2", 120000, "", 5000],
        [2, "Козырёк", "да", "за_м2", 25000, "", ""],
    ],
    "Профили": [[1, "Профиль 38x38", 38, 38, 1000, "да"]],
    "Стекло": [
        [1, "Прозрачное закалённое", 0, "да"],
        [2, "Матовое", 10, "да"],
    ],
    "Фурнитура": [
        [1, "Доводчик", "за_штуку", 8000, "да"],
        [2, "Замок", "за_штуку", 3500, "да"],
    ],
    "Услуги": [
        [1, "Дополнительные материалы", "фиксированная", 8500, "заказ", "да"],
        [2, "Замер", "фиксированная", 2500, "заказ", "да"],
        [3, "Доставка", "фиксированная", 6000, "заказ", "да"],
        [4, "Монтаж", "фиксированная", 35000, "заказ", "да"],
    ],
    "Правила": [
        ["версия_прайса", "1.0", "строка", ""],
        ["макс_скидка_менеджера_процент", 10, "число", ""],
        ["базовый_процент_скидки", 0, "число", ""],
        ["проверка_мин_стоимости", "да", "строка", ""],
        ["базовая_цена_стекла_руб_м2", 3000, "число", ""],
        ["профиль_правило_расчёта", "отключён", "строка", ""],
    ],
    "История_версий": [["1.0", "19.10.2026 12:00", "prices.xlsx", "тест"]],
}


def default_rows() -> dict:
    return copy.deepcopy(DEFAULT_ROWS)


def to_records(rows: dict) -> dict:
    return {sheet: [dict(zip(SHEET_HEADERS[sheet], row)) for row in rows[sheet]]
            for sheet in rows}


def price_from_rows(rows: dict):
    return price_service.build_price_data(to_records(rows), loaded_at="19.10.2026 12:00:00")


def write_xlsx(path, rows: dict) -> str:
    wb = Workbook()
    first = True
    for sheet, headers in SHEET_HEADERS.items():
        ws = wb.active if first else wb.create_sheet(sheet)
        first = False
        ws.title = sheet
        ws.append(headers)
        for row in rows.get(sheet, []):
            ws.append(row)
    wb.save(str(path))
    return str(path)


def sample_input() -> dict:
    return {
        "structures": [{"construction_id": "1", "glass_id": "1",
                        "width_mm": 4000, "height_mm": 2500, "quantity": 1}],
        "hardware": [{"hardware_id": "1", "quantity": 1}],
        "services": [{"service_id": "4", "enabled": True}],
        "discount_type": "", "discount_value": "",
    }


# =========================================================== yandex_disk (сеть)

def _fake_metadata(name="данные_калькулятора.xlsx", size=10406):
    return {"name": name, "type": "file", "size": size,
            "mime_type": "application/vnd.openxmlformats-officedocument."
                         "spreadsheetml.sheet", "path": "/" + name}


def test_get_price_file_success_xlsx(tmp_path, monkeypatch):
    src = write_xlsx(tmp_path / "src.xlsx", default_rows())
    payload = open(src, "rb").read()

    def fake_json(url, timeout):
        if "/download" in url:
            return {"href": "https://downloader.disk.yandex.ru/disk/abc"}
        return _fake_metadata()

    def fake_download(href, dest_path, max_bytes, timeout):
        with open(dest_path, "wb") as fh:
            fh.write(payload)
        return {"bytes": len(payload), "content_type": "application/octet-stream",
                "declared_size": len(payload)}

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    monkeypatch.setattr(yandex_disk, "_http_download", fake_download)

    info = yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"))
    assert info["format"] == "xlsx"
    assert info["filename"].endswith(".xlsx")
    assert info["size"] == len(payload)
    assert os.path.exists(info["path"])
    # скачанный файл действительно читается как прайс проекта
    price, errors, _stats = price_service.load_price_from_file(info["path"])
    assert price is not None


def test_get_price_file_html_instead_of_excel(tmp_path, monkeypatch):
    html = b"<!DOCTYPE html><html><body>page</body></html>"

    def fake_json(url, timeout):
        return ({"href": "https://x/y"} if "/download" in url else _fake_metadata())

    def fake_download(href, dest_path, max_bytes, timeout):
        with open(dest_path, "wb") as fh:
            fh.write(html)
        return {"bytes": len(html), "content_type": "text/html; charset=utf-8",
                "declared_size": len(html)}

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    monkeypatch.setattr(yandex_disk, "_http_download", fake_download)
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"))
    assert exc.value.kind == "format"


def test_get_price_file_non_excel_bytes(tmp_path, monkeypatch):
    junk = b"NOTEXCEL-just-some-bytes"

    def fake_json(url, timeout):
        return ({"href": "https://x/y"} if "/download" in url else _fake_metadata())

    def fake_download(href, dest_path, max_bytes, timeout):
        with open(dest_path, "wb") as fh:
            fh.write(junk)
        return {"bytes": len(junk), "content_type": "application/octet-stream",
                "declared_size": len(junk)}

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    monkeypatch.setattr(yandex_disk, "_http_download", fake_download)
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"))
    assert exc.value.kind == "format"


def test_get_price_file_too_large_by_metadata(tmp_path, monkeypatch):
    def fake_json(url, timeout):
        return _fake_metadata(size=50 * 1024 * 1024)

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"),
                                   max_bytes=1024)
    assert exc.value.kind == "size"


def test_get_price_file_yandex_unavailable(tmp_path, monkeypatch):
    def fake_json(url, timeout):
        raise yandex_disk.YandexDiskError(
            "Не удалось связаться с Яндекс Диском.", kind="unavailable")

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"))
    assert exc.value.kind == "unavailable"


def test_get_price_file_not_found(tmp_path, monkeypatch):
    def fake_json(url, timeout):
        raise yandex_disk.YandexDiskError("Публичный ресурс не найден.",
                                          kind="not_found")

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"))
    assert exc.value.kind == "not_found"


def test_get_price_file_no_download_href(tmp_path, monkeypatch):
    def fake_json(url, timeout):
        return {} if "/download" in url else _fake_metadata()

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"))
    assert exc.value.kind == "api"


def test_get_price_file_retries_after_transient_failure(tmp_path, monkeypatch):
    src = write_xlsx(tmp_path / "src.xlsx", default_rows())
    payload = open(src, "rb").read()
    calls = {"n": 0}

    def fake_json(url, timeout):
        return ({"href": "https://x/y"} if "/download" in url else _fake_metadata())

    def flaky_download(href, dest_path, max_bytes, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            raise yandex_disk.YandexDiskError("обрыв сети", kind="unavailable")
        with open(dest_path, "wb") as fh:
            fh.write(payload)
        return {"bytes": len(payload), "content_type": "application/octet-stream",
                "declared_size": len(payload)}

    monkeypatch.setattr(yandex_disk, "_http_json", fake_json)
    monkeypatch.setattr(yandex_disk, "_http_download", flaky_download)
    info = yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "dl"), retries=3)
    assert calls["n"] == 2
    assert info["format"] == "xlsx"


class _FakeResponse:
    def __init__(self, data: bytes, headers: dict):
        self._data = data
        self._pos = 0
        self.headers = headers

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_http_download_rejects_oversized_stream(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None, context=None):
        return _FakeResponse(b"x" * 5000, {})  # без Content-Length

    monkeypatch.setattr(yandex_disk.urllib.request, "urlopen", fake_urlopen)
    dest = str(tmp_path / "big.bin")
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk._http_download("https://x/y", dest, max_bytes=1000, timeout=5)
    assert exc.value.kind == "size"
    assert not os.path.exists(dest)  # недокачанный файл удалён


def test_http_download_timeout(tmp_path, monkeypatch):
    def fake_urlopen(req, timeout=None, context=None):
        raise socket.timeout("timed out")

    monkeypatch.setattr(yandex_disk.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(yandex_disk.YandexDiskError) as exc:
        yandex_disk._http_download("https://x/y", str(tmp_path / "f"), 1000, 5)
    assert exc.value.kind == "timeout"


# =========================================================== price_service

def test_detect_excel_format():
    assert price_service.detect_excel_format(b"PK\x03\x04rest") == "xlsx"
    assert price_service.detect_excel_format(
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1rest") == "xls"
    assert price_service.detect_excel_format(b"<!DOCTYPE html>") is None


def test_read_xls_fixture_and_validate():
    pytest.importorskip("xlrd")
    assert os.path.exists(XLS_FIXTURE), "нет фикстуры .xls"
    price, errors, _stats = price_service.load_price_from_file(XLS_FIXTURE)
    assert price is not None
    assert not [e for e in errors if e["blocking"]]
    assert len(price.services) == 4


def test_missing_required_column_blocks(tmp_path):
    rows = default_rows()
    rows["Услуги"] = [[1, "Монтаж", "фиксированная", 35000, "заказ", "да"]]
    # Ломаем заголовок: убираем колонку "цена"
    path = str(tmp_path / "bad.xlsx")
    wb = Workbook()
    first = True
    for sheet, headers in SHEET_HEADERS.items():
        ws = wb.active if first else wb.create_sheet(sheet)
        first = False
        ws.title = sheet
        ws.append([h for h in headers if not (sheet == "Услуги" and h == "цена")])
        for row in rows.get(sheet, []):
            if sheet == "Услуги":
                idx = headers.index("цена")
                row = row[:idx] + row[idx + 1:]
            ws.append(row)
    wb.save(path)
    price, errors, _stats = price_service.load_price_from_file(path)
    assert price is None
    assert any("цена" in e["message"] for e in errors if e["blocking"])


def test_negative_price_and_duplicate_id_block(tmp_path):
    rows = default_rows()
    rows["Фурнитура"] = [
        [1, "Доводчик", "за_штуку", -100, "да"],
        [1, "Дубль", "за_штуку", 8000, "да"],
    ]
    path = write_xlsx(tmp_path / "bad.xlsx", rows)
    price, errors, _stats = price_service.load_price_from_file(path)
    assert price is None
    messages = " ".join(e["message"] for e in errors if e["blocking"])
    assert "отрицательная" in messages.lower()
    assert "Дубликат id" in messages


def test_corrupt_xlsx_raises_clear_error(tmp_path):
    path = tmp_path / "corrupt.xlsx"
    path.write_bytes(b"PK\x03\x04-not-a-real-zip-file")
    with pytest.raises(ValueError):
        price_service.read_workbook_rows(str(path))


# =========================================================== price_diff

def test_compare_prices_detects_all_categories():
    current = price_from_rows(default_rows())
    rows = default_rows()
    rows["Услуги"][3][3] = 41000                 # Монтаж: цена изменилась
    rows["Конструкции"][1][1] = "Козырёк v2"     # переименование (id=2)
    rows["Фурнитура"] = [[1, "Доводчик", "за_штуку", 8000, "да"]]  # Замок удалён
    rows["Стекло"].append([3, "Новое стекло", 5, "да"])            # новая позиция
    candidate = price_from_rows(rows)

    diff = price_diff.compare_prices(current, candidate)
    assert diff["counts"]["changed_positions"] == 1
    assert diff["counts"]["new"] == 1
    assert diff["counts"]["missing"] == 1
    assert diff["counts"]["name_changed"] == 1
    assert diff["changed"][0]["direction"] == "up"
    assert diff["changed"][0]["percent"] == "17.14"


def test_find_unmapped_flags_empty_and_duplicate_ids(tmp_path):
    rows = {**default_rows(), "Фурнитура": [
        [1, "A", "за_штуку", 100, "да"],
        ["", "B", "за_штуку", 200, "да"],
        [1, "C", "за_штуку", 300, "да"],
    ]}
    data = price_service.read_workbook_rows(write_xlsx(tmp_path / "unmapped.xlsx", rows))
    unmapped = price_diff.find_unmapped(data)
    assert len(unmapped) == 2


# =========================================================== приложение (Flask)

@pytest.fixture()
def yapp(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    import app as app_module
    monkeypatch.setattr(app_module, "EXCEL_PATH", str(tmp_path / "данные_калькулятора.xlsx"))
    monkeypatch.setattr(app_module, "YANDEX_TMP_DIR", str(tmp_path / "yandex"))
    monkeypatch.setattr(app_module, "CURRENT_PRICE", None)
    monkeypatch.setattr(app_module, "CURRENT_PRICE_VERSION_ID", None)
    monkeypatch.setattr(app_module, "YANDEX_STAGED", None)
    db.init_db()
    db.create_user("admin", "secret123", "admin", "Администратор")
    app_module.app.config["TESTING"] = True
    return app_module


@pytest.fixture()
def client(yapp):
    c = yapp.app.test_client()
    c.post("/login", data={"login": "admin", "password": "secret123"})
    return c


def _set_current_price(app_module, rows):
    price = price_from_rows(rows)
    app_module.CURRENT_PRICE = price
    app_module.CURRENT_PRICE_VERSION_ID = db.save_price_version(price, "старт")
    return price


def _stage_file(app_module, monkeypatch, path, filename="данные_калькулятора.xlsx"):
    """Подменяет скачивание: на каждый вызов копирует файл в staging-папку."""
    import shutil
    os.makedirs(app_module.YANDEX_TMP_DIR, exist_ok=True)
    staged_path = os.path.join(app_module.YANDEX_TMP_DIR, filename)
    src_path = str(path)

    def fake_get_price_file(*args, **kwargs):
        shutil.copy2(src_path, staged_path)
        return {
            "path": staged_path, "filename": filename, "format": "xlsx",
            "size": os.path.getsize(staged_path),
            "content_type": "application/octet-stream",
            "source": app_module.YANDEX_PRICE_PUBLIC_URL,
            "downloaded_at": "19.10.2026 12:00:00",
        }

    monkeypatch.setattr(app_module.yandex_disk, "get_price_file", fake_get_price_file)


def _save_calc(price):
    from calculator import calculate_quote
    quote = calculate_quote(price, sample_input())
    user = db.get_user_by_login("admin")
    return db.save_calculation({
        "number": db.next_calculation_number(),
        "created_at": "19.10.2026 12:00:00", "user_id": user["id"],
        "user_login": "admin", "price_version_id": 1, "price_version": price.version,
        "snapshot": {"quote": quote, "client": {}, "input": sample_input(),
                     "price": price.to_dict()},
        "subtotal": str(quote["subtotal"]), "discount": str(quote["discount"]),
        "total": str(quote["total"]),
    }), quote


def test_download_endpoint_reports_friendly_error(client, yapp, monkeypatch):
    def boom(*a, **k):
        raise yapp.yandex_disk.YandexDiskError("Не удалось связаться с Яндекс Диском.",
                                               kind="unavailable")
    monkeypatch.setattr(yapp.yandex_disk, "get_price_file", boom)
    r = client.post("/price/yandex/download",
                    headers={"X-Requested-With": "fetch"})
    data = r.get_json()
    assert data["ok"] is False
    assert "Яндекс" in data["message"]


def test_preview_shows_comparison(client, yapp, tmp_path, monkeypatch):
    _set_current_price(yapp, default_rows())
    rows = default_rows()
    rows["Услуги"][3][3] = 41000
    path = write_xlsx(tmp_path / "new.xlsx", rows)
    _stage_file(yapp, monkeypatch, path)

    assert client.post("/price/yandex/download",
                       headers={"X-Requested-With": "fetch"}).get_json()["ok"]
    r = client.get("/price/yandex/preview")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Найдены изменения" in body
    assert "Монтаж" in body and "41000" in body


def test_full_apply_preserves_old_calc_and_updates_new(client, yapp, tmp_path, monkeypatch):
    from calculator import calculate_quote
    current = _set_current_price(yapp, default_rows())
    calc_id, old_quote = _save_calc(current)

    rows = default_rows()
    rows["Услуги"][3][3] = 41000                       # Монтаж +6000
    rows["Фурнитура"] = [[1, "Доводчик", "за_штуку", 8000, "да"]]  # Замок отсутствует
    rows["Стекло"].append([3, "Новое стекло", 5, "да"])
    path = write_xlsx(tmp_path / "new.xlsx", rows)
    _stage_file(yapp, monkeypatch, path)

    assert client.post("/price/yandex/download",
                       headers={"X-Requested-With": "fetch"}).get_json()["ok"]
    token = yapp.YANDEX_STAGED["token"]

    r = client.post("/price/yandex/apply",
                    data={"token": token, "ack_missing": "on"})
    assert r.status_code == 302

    # новая версия применена
    assert yapp.CURRENT_PRICE.services["4"]["цена"] == Decimal("41000")
    assert len(yapp.CURRENT_PRICE.glasses) == 3
    latest = db.latest_price_version()
    assert "Яндекс" in latest["comment"]

    # старый расчёт не изменился
    assert db.get_calculation(calc_id)["total"] == str(old_quote["total"])

    # новый расчёт использует обновлённые цены
    new_quote = calculate_quote(yapp.CURRENT_PRICE, sample_input())
    assert Decimal(str(new_quote["total"])) == Decimal(str(old_quote["total"])) + 6000


def test_apply_requires_ack_for_missing_positions(client, yapp, tmp_path, monkeypatch):
    _set_current_price(yapp, default_rows())
    rows = default_rows()
    rows["Фурнитура"] = [[1, "Доводчик", "за_штуку", 8000, "да"]]  # Замок удалён
    path = write_xlsx(tmp_path / "new.xlsx", rows)
    _stage_file(yapp, monkeypatch, path)
    client.post("/price/yandex/download", headers={"X-Requested-With": "fetch"})
    token = yapp.YANDEX_STAGED["token"]

    r = client.post("/price/yandex/apply", data={"token": token})
    assert r.status_code == 302
    assert yapp.CURRENT_PRICE.hardware.get("2") is not None  # прайс не изменился
    assert yapp.YANDEX_STAGED is not None                    # ожидание сохранилось


def test_preview_blocks_invalid_file(client, yapp, tmp_path, monkeypatch):
    _set_current_price(yapp, default_rows())
    rows = default_rows()
    rows["Фурнитура"] = [[1, "A", "за_штуку", -5, "да"]]     # отрицательная цена
    path = write_xlsx(tmp_path / "bad.xlsx", rows)
    _stage_file(yapp, monkeypatch, path)
    client.post("/price/yandex/download", headers={"X-Requested-With": "fetch"})

    body = client.get("/price/yandex/preview").get_data(as_text=True)
    assert "не соответствует структуре" in body
    assert "Подтвердить и применить" not in body

    token = yapp.YANDEX_STAGED["token"]
    r = client.post("/price/yandex/apply", data={"token": token, "ack_missing": "on"})
    assert r.status_code == 302
    assert yapp.CURRENT_PRICE.hardware["1"]["цена"] == Decimal("8000")  # не применено


def test_cancel_keeps_price_unchanged(client, yapp, tmp_path, monkeypatch):
    current = _set_current_price(yapp, default_rows())
    rows = default_rows()
    rows["Услуги"][3][3] = 41000
    _stage_file(yapp, monkeypatch, write_xlsx(tmp_path / "new.xlsx", rows))
    client.post("/price/yandex/download", headers={"X-Requested-With": "fetch"})
    assert yapp.YANDEX_STAGED is not None

    r = client.post("/price/yandex/cancel")
    assert r.status_code == 302
    assert yapp.YANDEX_STAGED is None
    assert yapp.CURRENT_PRICE.services["4"]["цена"] == Decimal("35000")
    assert equal_prices(yapp.CURRENT_PRICE, current)


def equal_prices(a, b) -> bool:
    return a.to_dict() == b.to_dict()


def test_save_error_does_not_damage_current_price(client, yapp, tmp_path, monkeypatch):
    from decimal import Decimal as _D
    current = _set_current_price(yapp, default_rows())
    # рабочий Excel-файл с «известным» содержимым
    write_xlsx(yapp.EXCEL_PATH, default_rows())
    before_bytes = open(yapp.EXCEL_PATH, "rb").read()

    rows = default_rows()
    rows["Услуги"][3][3] = 41000
    _stage_file(yapp, monkeypatch, write_xlsx(tmp_path / "new.xlsx", rows))
    client.post("/price/yandex/download", headers={"X-Requested-With": "fetch"})
    token = yapp.YANDEX_STAGED["token"]

    def boom(*a, **k):
        raise RuntimeError("сбой записи в БД")
    monkeypatch.setattr(db, "save_price_version", boom)

    r = client.post("/price/yandex/apply", data={"token": token, "ack_missing": "on"})
    assert r.status_code == 302
    # прайс в памяти не изменился
    assert yapp.CURRENT_PRICE.services["4"]["цена"] == _D("35000")
    # Excel восстановлен из резервной копии
    assert open(yapp.EXCEL_PATH, "rb").read() == before_bytes
    # в БД нет новой версии
    assert "Яндекс" not in (db.latest_price_version() or {}).get("comment", "")


def test_reimport_does_not_duplicate_positions(client, yapp, tmp_path, monkeypatch):
    _set_current_price(yapp, default_rows())
    rows = default_rows()
    rows["Услуги"][3][3] = 41000
    path = write_xlsx(tmp_path / "new.xlsx", rows)
    _stage_file(yapp, monkeypatch, path)

    for _ in range(2):
        client.post("/price/yandex/download", headers={"X-Requested-With": "fetch"})
        token = yapp.YANDEX_STAGED["token"]
        client.post("/price/yandex/apply", data={"token": token, "ack_missing": "on"})

    assert len(yapp.CURRENT_PRICE.services) == 4
    assert len(yapp.CURRENT_PRICE.hardware) == 2
    assert len(yapp.CURRENT_PRICE.constructions) == 2


def test_button_present_and_non_js_form_path(client, yapp, tmp_path, monkeypatch):
    _set_current_price(yapp, default_rows())
    # кнопка есть и на главной, и на странице прайса
    assert "Получить актуальный прайс с Яндекс Диска" in \
        client.get("/").get_data(as_text=True)
    assert "Получить актуальный прайс с Яндекс Диска" in \
        client.get("/price").get_data(as_text=True)

    # Обычная форма без JavaScript: POST без заголовка fetch → редирект на preview
    _stage_file(yapp, monkeypatch, write_xlsx(tmp_path / "new.xlsx", default_rows()))
    r = client.post("/price/yandex/download")
    assert r.status_code == 302
    assert "/price/yandex/preview" in r.headers["Location"]


# =========================================================== живой источник (опция)

@pytest.mark.skipif(os.environ.get("YANDEX_LIVE_TEST") != "1",
                    reason="живой тест реального Яндекс Диска (YANDEX_LIVE_TEST=1)")
def test_live_yandex_disk(tmp_path):
    info = yandex_disk.get_price_file(PUBLIC_URL, str(tmp_path / "live"))
    assert info["format"] in ("xlsx", "xls")
    price, errors, _stats = price_service.load_price_from_file(info["path"])
    assert price is not None
