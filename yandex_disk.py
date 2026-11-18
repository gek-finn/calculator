"""Получение актуального Excel-прайса с Яндекс Диска по ПУБЛИЧНОЙ ссылке.

Модуль не требует логина, пароля или токена: используется официальный API
публичных ресурсов Яндекс Диска.

Ключевые правила безопасности:
- адрес источника задаётся ТОЛЬКО конфигурацией приложения, а не пользователем;
- у запросов есть тайм-ауты и ограничение максимального размера файла;
- временная ссылка на скачивание НЕ сохраняется как постоянный адрес источника;
- полученный файл проверяется по «магическим» байтам (должен быть Excel).

Все сетевые обращения идут через две функции (`_http_json` и `_http_download`),
которые легко подменяются в тестах — тесты не зависят от реального Яндекс Диска.
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from price_service import detect_excel_format

DEFAULT_API_BASE = "https://cloud-api.yandex.net/v1/disk/public/resources"
USER_AGENT = "LiderPro-calculator/1.0 (+local price loader)"
CHUNK_SIZE = 64 * 1024
DEFAULT_MAX_BYTES = 20 * 1024 * 1024       # 20 МБ — с запасом для прайс-листа
DEFAULT_TIMEOUT = 60                       # тайм-аут скачивания файла, секунды
DEFAULT_API_TIMEOUT = 20                   # тайм-аут запросов к API, секунды
DEFAULT_RETRIES = 3                        # повторы при обрывах сети/SSL


class YandexDiskError(Exception):
    """Понятная пользователю ошибка получения файла с Яндекс Диска."""

    def __init__(self, message: str, *, kind: str = "error"):
        super().__init__(message)
        self.message = message
        self.kind = kind  # unavailable | timeout | not_found | api | size | format


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context()


def _http_json(url: str, timeout: int) -> dict:
    """Запрос к API Яндекс Диска. Возвращает разобранный JSON."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context()) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("message", "")
        except Exception:  # noqa: BLE001
            detail = ""
        if exc.code == 404:
            raise YandexDiskError(
                "Публичный ресурс не найден. Проверьте, что ссылка Яндекс Диска "
                "открыта для всех и файл не удалён.", kind="not_found") from exc
        raise YandexDiskError(
            f"Яндекс Диск вернул ошибку {exc.code}"
            + (f": {detail}" if detail else "."), kind="api") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise YandexDiskError("Истекло время ожидания ответа Яндекс Диска.",
                              kind="timeout") from exc
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
            raise YandexDiskError("Истекло время ожидания ответа Яндекс Диска.",
                                  kind="timeout") from exc
        raise YandexDiskError(
            "Не удалось связаться с Яндекс Диском. Проверьте подключение к сети.",
            kind="unavailable") from exc
    except OSError as exc:
        raise YandexDiskError(
            "Не удалось связаться с Яндекс Диском. Проверьте подключение к сети.",
            kind="unavailable") from exc

    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise YandexDiskError("Яндекс Диск вернул ответ неизвестного формата.",
                              kind="api") from exc


def _http_download(href: str, dest_path: str, max_bytes: int,
                   timeout: int) -> dict:
    """Скачивает файл по временной ссылке, ограничивая размер.

    Возвращает {"bytes", "content_type", "declared_size"}. При ошибке удаляет
    недокачанный файл и бросает YandexDiskError.
    """
    req = urllib.request.Request(href, headers={
        "User-Agent": USER_AGENT, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=_ssl_context()) as resp:
            content_type = (resp.headers.get("Content-Type") or "").lower()
            declared = resp.headers.get("Content-Length")
            declared_size = int(declared) if declared and declared.isdigit() else None
            if declared_size is not None and declared_size > max_bytes:
                raise YandexDiskError(
                    f"Файл слишком большой: {declared_size} байт "
                    f"(предел {max_bytes}).", kind="size")
            total = 0
            with open(dest_path, "wb") as out:
                while True:
                    chunk = resp.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise YandexDiskError(
                            f"Файл слишком большой (превышен предел "
                            f"{max_bytes} байт).", kind="size")
                    out.write(chunk)
    except YandexDiskError:
        _safe_remove(dest_path)
        raise
    except (socket.timeout, TimeoutError) as exc:
        _safe_remove(dest_path)
        raise YandexDiskError("Обрыв загрузки: истекло время ожидания.",
                              kind="timeout") from exc
    except urllib.error.HTTPError as exc:
        _safe_remove(dest_path)
        raise YandexDiskError(
            f"Яндекс Диск прервал скачивание (код {exc.code}).",
            kind="unavailable") from exc
    except (urllib.error.URLError, OSError) as exc:
        _safe_remove(dest_path)
        raise YandexDiskError(
            "Обрыв загрузки файла с Яндекс Диска. Проверьте сеть и повторите.",
            kind="unavailable") from exc
    return {"bytes": total, "content_type": content_type,
            "declared_size": declared_size}


def _safe_remove(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def _api_url(base: str, public_url: str, path: str | None = None,
             limit: int | None = None) -> str:
    params = {"public_key": public_url}
    if path:
        params["path"] = path
    if limit:
        params["limit"] = str(limit)
    return base + "?" + urllib.parse.urlencode(params)


def _extract_resource(meta: dict) -> dict:
    return {
        "name": meta.get("name", ""),
        "type": meta.get("type", "file"),
        "size": meta.get("size"),
        "mime": meta.get("mime_type", ""),
        "path": meta.get("path", ""),
    }


def _pick_excel_item(items: list[dict]) -> dict:
    excel = [it for it in items
             if it.get("type") == "file"
             and str(it.get("name", "")).lower().endswith((".xlsx", ".xlsm", ".xls"))]
    if not excel:
        raise YandexDiskError(
            "В публичном ресурсе не найден Excel-файл (.xlsx или .xls). "
            "Убедитесь, что ссылка ведёт на файл прайса.", kind="format")
    if len(excel) > 1:
        names = ", ".join(it.get("name", "?") for it in excel[:5])
        raise YandexDiskError(
            "В публичной папке несколько Excel-файлов: " + names +
            ". Укажите ссылку на конкретный файл прайса.", kind="format")
    return excel[0]


def _resolve_metadata(public_url: str, api_timeout: int,
                      api_base: str) -> tuple[dict, str | None]:
    """Возвращает (метаданные файла, путь внутри ресурса для скачивания)."""
    meta = _http_json(_api_url(api_base, public_url), api_timeout)
    info = _extract_resource(meta)
    if info["type"] == "dir":
        listing = _http_json(_api_url(api_base, public_url, limit=1000), api_timeout)
        items = (((listing or {}).get("_embedded") or {}).get("items")) or []
        chosen = _pick_excel_item(items)
        return _extract_resource(chosen), chosen.get("path") or None
    return info, None


def _safe_filename(name: str, fmt: str) -> str:
    """Безопасное имя файла без путей и лишних символов."""
    base = os.path.basename(str(name or "").replace("\\", "/")).strip()
    base = "".join(ch for ch in base if ch not in '<>:"/\\|?*').strip(". ")
    if not base:
        base = "прайс_с_яндекс_диска"
    ext = ".xls" if fmt == "xls" else ".xlsx"
    root, _ = os.path.splitext(base)
    return f"{root}{ext}"


def get_price_file(public_url: str, dest_dir: str,
                   max_bytes: int = DEFAULT_MAX_BYTES,
                   timeout: int = DEFAULT_TIMEOUT,
                   api_timeout: int = DEFAULT_API_TIMEOUT,
                   retries: int = DEFAULT_RETRIES,
                   api_base: str = DEFAULT_API_BASE) -> dict:
    """Скачивает Excel-прайс по публичной ссылке во временную директорию.

    Возвращает словарь:
        {"path", "filename", "format", "size", "content_type", "source", "downloaded_at"}

    Бросает YandexDiskError с понятным сообщением при любой проблеме.
    """
    os.makedirs(dest_dir, exist_ok=True)
    source = public_url

    meta, res_path = _resolve_metadata(public_url, api_timeout, api_base)
    size = meta.get("size")
    if isinstance(size, int) and size > max_bytes:
        raise YandexDiskError(
            f"Файл слишком большой: {size} байт (предел {max_bytes}).", kind="size")

    href = ""
    last_error: YandexDiskError | None = None
    part_path = os.path.join(dest_dir, "_download.part")

    for attempt in range(1, max(1, retries) + 1):
        try:
            dl = _http_json(
                _api_url(api_base + "/download", public_url, path=res_path),
                api_timeout)
            href = (dl or {}).get("href") or ""
            if not href:
                raise YandexDiskError(
                    "Яндекс Диск не вернул ссылку на скачивание. Возможно, файл "
                    "защищён или ссылка больше не публичная.", kind="api")
            info = _http_download(href, part_path, max_bytes, timeout)
            break
        except YandexDiskError as exc:
            last_error = exc
            _safe_remove(part_path)
            # Повторяем только при временных сбоях сети/SSL.
            if exc.kind in ("size", "format", "not_found", "api") or attempt == retries:
                raise
            time.sleep(min(2.0, attempt))
    else:  # pragma: no cover — цикл всегда прерывается raise/break
        raise last_error or YandexDiskError("Не удалось скачать файл.", kind="api")

    content_type = info.get("content_type", "")
    if content_type.startswith("text/html"):
        _safe_remove(part_path)
        raise YandexDiskError(
            "Вместо Excel-файла получена HTML-страница. Вероятно, ссылка ведёт "
            "на страницу просмотра, а не на файл.", kind="format")

    with open(part_path, "rb") as fh:
        fmt = detect_excel_format(fh.read(8))
    if fmt is None:
        _safe_remove(part_path)
        raise YandexDiskError(
            "Полученный файл не является Excel-файлом (.xlsx или .xls). "
            "Проверьте, что публичная ссылка ведёт на прайс.", kind="format")

    filename = _safe_filename(meta.get("name", ""), fmt)
    final_path = os.path.join(dest_dir, filename)
    os.replace(part_path, final_path)
    return {
        "path": final_path,
        "filename": filename,
        "format": fmt,
        "size": info.get("bytes"),
        "content_type": content_type,
        "source": source,
        "downloaded_at": datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
    }
