# -*- coding: utf-8 -*-
"""Упаковывает готовую папку dist/Calculator в ZIP-архив для передачи.

Использует стандартный zipfile (UTF-8 имена файлов, включая кириллицу).
В архив не попадают кэши и временные файлы.

Запуск:
    python tools/package_zip.py [source_dir] [output_zip]
По умолчанию: dist/Calculator -> dist/Calculator-portable.zip
"""

from __future__ import annotations

import os
import sys
import zipfile

EXCLUDE_DIRS = {"__pycache__", ".pytest_cache", "build"}
EXCLUDE_EXTS = {".pyc", ".pyo", ".log", ".tmp"}
EXCLUDE_NAMES = {"app.port", "secret.key"}


def build_zip(source_dir: str, output_zip: str) -> tuple[str, int, int]:
    source_dir = os.path.abspath(source_dir)
    if not os.path.isdir(source_dir):
        raise FileNotFoundError(f"Нет папки для упаковки: {source_dir}")
    output_zip = os.path.abspath(output_zip)
    os.makedirs(os.path.dirname(output_zip), exist_ok=True)
    if os.path.exists(output_zip):
        os.remove(output_zip)

    base_name = os.path.basename(source_dir.rstrip("\\/"))
    count = 0
    total = 0
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(source_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for name in files:
                if os.path.splitext(name)[1].lower() in EXCLUDE_EXTS:
                    continue
                if name in EXCLUDE_NAMES:
                    continue
                full = os.path.join(root, name)
                rel = os.path.relpath(full, source_dir)
                arcname = os.path.join(base_name, rel)
                zf.write(full, arcname)
                count += 1
                total += os.path.getsize(full)
    return output_zip, count, total


def main(argv: list[str]) -> int:
    src = argv[1] if len(argv) > 1 else os.path.join("dist", "Calculator")
    out = argv[2] if len(argv) > 2 else os.path.join("dist", "Calculator-portable.zip")
    try:
        path, count, total = build_zip(src, out)
    except FileNotFoundError as exc:
        print(f"[ОШИБКА] {exc}")
        return 1
    size_mb = os.path.getsize(path) / (1024 * 1024)
    print(f"ZIP создан: {path}")
    print(f"Файлов: {count}, распакованный размер: {total / (1024 * 1024):.1f} МБ, "
          f"архив: {size_mb:.1f} МБ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
