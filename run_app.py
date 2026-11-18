# -*- coding: utf-8 -*-
"""Точка входа приложения: локальный запуск Flask и удобства для пользователя.

Используется и при сборке в EXE (PyInstaller), и при обычном запуске
``python run_app.py``. Файл ``app.py`` остаётся прежней точкой входа для
``запуск.bat`` и тестов — вся логика приложения не меняется.

Что делает лаунчер:
* запускает Flask ТОЛЬКО на 127.0.0.1 (локально, без доступа из сети);
* подбирает свободный порт (по умолчанию 5000, при занятости — следующий);
* автоматически открывает системный браузер на странице калькулятора;
* не даёт запустить вторую копию: при повторном запуске открывает уже
  работающий экземпляр;
* даёт понятный способ завершения (значок в трее, а при его недоступности —
  страница «Настройки» → «Завершить приложение»);
* пишет журнал в папку данных и показывает понятное сообщение об ошибке
  вместо трассировки.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import webbrowser
from logging.handlers import RotatingFileHandler

import app_paths

MUTEX_NAME = "PartitionCalculator_LiderPro_SingleInstance"
HOST = "127.0.0.1"


def _configure_console() -> None:
    """В оконной сборке нет консоли: направляем вывод в журнал, чтобы
    случайные print()/сообщения Werkzeug не приводили к ошибкам."""
    try:
        if sys.stdout is None or sys.stderr is None:
            stream = open(os.path.join(app_paths.LOG_DIR, "console.log"),
                          "a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = stream
            if sys.stderr is None:
                sys.stderr = stream
    except OSError:
        pass


def _setup_logging() -> logging.Logger:
    os.makedirs(app_paths.LOG_DIR, exist_ok=True)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(
            os.path.join(app_paths.LOG_DIR, "app.log"),
            maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return logging.getLogger("launcher")


def _message_box(title: str, text: str, error: bool = False) -> None:
    """Понятное сообщение вместо трассировки (Windows). Иначе — в журнал."""
    try:
        import ctypes
        flags = 0x10 if error else 0x40  # MB_ICONERROR | MB_ICONINFORMATION
        ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:  # noqa: BLE001
        logging.getLogger("launcher").info("Сообщение пользователю: %s", text)


def acquire_single_instance():
    """Не даёт запустить вторую копию приложения.

    Возвращает непрозрачный дескриптор (успех) или None, если копия уже
    запущена. Реализовано через именованный мьютекс Windows; на других ОС
    ограничение не накладывается.
    """
    if os.name != "nt":
        return object()
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL,
                                           wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return object()
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return None
        return handle
    except Exception:  # noqa: BLE001
        return object()


def is_port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
            return True
        except OSError:
            return False


def find_free_port(host: str, preferred: int, limit: int = 50) -> int:
    """Свободный локальный порт начиная с preferred."""
    for port in range(preferred, preferred + limit):
        if is_port_free(host, port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def _port_file() -> str:
    return os.path.join(app_paths.DATA_DIR, "app.port")


def write_port_file(port: int) -> None:
    try:
        os.makedirs(app_paths.DATA_DIR, exist_ok=True)
        with open(_port_file(), "w", encoding="utf-8") as fh:
            fh.write(str(int(port)))
    except OSError:
        pass


def read_port_file() -> int | None:
    try:
        with open(_port_file(), "r", encoding="utf-8") as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def _serve_in_thread(server) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, name="flask-server",
                              daemon=True)
    thread.start()
    return thread


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "да")


def _open_browser(url: str, log: logging.Logger) -> None:
    """Открывает системный браузер и фиксирует результат в журнале."""
    try:
        opened = webbrowser.open(url)
        log.info("Открываю браузер: %s (результат: %s)", url, opened)
    except Exception:  # noqa: BLE001
        log.exception("Не удалось открыть браузер автоматически")


def _run_with_tray(server, url: str, log: logging.Logger) -> bool:
    """Значок в системном трее (основной способ управления в оконной сборке)."""
    if _env_flag("CALCULATOR_NO_TRAY"):
        return False
    try:
        import pystray
        from PIL import Image
    except Exception:  # noqa: BLE001
        return False
    try:
        icon_path = os.path.join(app_paths.resource_dir(), "assets", "app.ico")
        if os.path.exists(icon_path):
            image = Image.open(icon_path)
        else:
            image = Image.new("RGB", (64, 64), (34, 160, 90))

        def on_open(icon, item):  # noqa: ANN001, ARG001
            _open_browser(url, log)

        def on_quit(icon, item):  # noqa: ANN001, ARG001
            log.info("Завершение по команде из трея")
            try:
                server.shutdown()
            finally:
                icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("Открыть калькулятор", on_open, default=True),
            pystray.MenuItem("Завершить", on_quit),
        )
        tray = pystray.Icon("liderpro", image, app_paths.APP_DISPLAY_NAME, menu)
        serve_thread = _serve_in_thread(server)

        def _watch_server() -> None:
            # Если сервер остановлен из веб-интерфейса («Завершить приложение»)
            # или по таймеру — убираем значок из трея и завершаем приложение,
            # чтобы не осталось скрытого работающего процесса.
            serve_thread.join()
            try:
                tray.stop()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_watch_server, name="tray-watch", daemon=True).start()
        tray.run()
        return True
    except Exception:  # noqa: BLE001
        log.exception("Не удалось запустить значок в трее")
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=app_paths.APP_DISPLAY_NAME)
    parser.add_argument("--port", type=int, default=int(
        os.environ.get("CALCULATOR_PORT", app_paths.DEFAULT_PORT)))
    parser.add_argument("--no-browser", action="store_true",
                        default=_env_flag("CALCULATOR_NO_BROWSER"))
    args = parser.parse_args(argv)

    app_paths.ensure_data_dir()
    _configure_console()
    log = _setup_logging()
    log.info("Запуск. Папка данных: %s", app_paths.DATA_DIR)

    lock = acquire_single_instance()
    if lock is None:
        port = read_port_file() or args.port
        url = f"http://{HOST}:{port}/"
        log.info("Копия уже запущена — открываю %s", url)
        if not args.no_browser:
            _open_browser(url, log)
        return 0

    try:
        # Импорт приложения — после настройки журнала (в оконной сборке нет консоли).
        import app as app_module

        port = find_free_port(HOST, args.port)
        write_port_file(port)

        from werkzeug.serving import make_server
        server = make_server(HOST, port, app_module.app, threaded=True)
        app_module.app.config["SHUTDOWN_SERVER"] = server.shutdown
        url = f"http://{HOST}:{port}/"
        app_module.app.config["APP_URL"] = url
        log.info("Сервер запущен: %s", url)

        if not args.no_browser:
            threading.Timer(1.2, lambda: _open_browser(url, log)).start()

        exit_after = os.environ.get("CALCULATOR_EXIT_AFTER")
        if exit_after and exit_after.isdigit():
            threading.Timer(float(exit_after), server.shutdown).start()

        if _run_with_tray(server, url, log):
            log.info("Приложение завершено (трей).")
            return 0

        # Трей недоступен: сервер в основном потоке, остановка — через страницу
        # «Настройки» → «Завершить приложение» либо закрытие процесса.
        log.info("Трей недоступен. Сервер работает; завершить можно в «Настройках».")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            log.info("Остановка по Ctrl+C")
            server.shutdown()
        return 0
    except Exception:  # noqa: BLE001
        log.exception("Не удалось запустить приложение")
        log_path = os.path.join(app_paths.LOG_DIR, "app.log")
        _message_box(
            app_paths.APP_DISPLAY_NAME,
            "Не удалось запустить приложение.\n\n"
            "Подробности записаны в журнал:\n" + log_path,
            error=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
