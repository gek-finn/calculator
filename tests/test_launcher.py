# -*- coding: utf-8 -*-
"""Тесты лаунчера: подбор порта, файл порта, единственный экземпляр."""

from __future__ import annotations

import socket

import app_paths
import run_app


def test_find_free_port_returns_bindable_port():
    port = run_app.find_free_port("127.0.0.1", 5000)
    assert isinstance(port, int) and 1024 < port < 65536
    assert run_app.is_port_free("127.0.0.1", port)


def test_find_free_port_skips_busy_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        busy_port = busy.getsockname()[1]
        chosen = run_app.find_free_port("127.0.0.1", busy_port, limit=5)
    assert chosen != busy_port
    assert run_app.is_port_free("127.0.0.1", chosen)


def test_port_file_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setattr(app_paths, "DATA_DIR", str(tmp_path))
    assert run_app.read_port_file() is None
    run_app.write_port_file(5233)
    assert run_app.read_port_file() == 5233


def test_single_instance_detects_second_launch():
    first = run_app.acquire_single_instance()
    try:
        assert first is not None
        second = run_app.acquire_single_instance()
        assert second is None
    finally:
        # Освобождаем мьютекс для других тестов (только Windows).
        try:
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(first)  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001
            pass
