# -*- coding: utf-8 -*-
"""Копирует краткую инструкцию пользователя в готовую сборку dist/Calculator.

Помощник нужен, чтобы имя файла с кириллицей не зависело от кодовой страницы
командной строки Windows.
"""

from __future__ import annotations

import os
import shutil

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE_NAME = "ПРОЧТИ_МЕНЯ.txt"


def main() -> int:
    src = os.path.join(ROOT, GUIDE_NAME)
    dst_dir = os.path.join(ROOT, "dist", "Calculator")
    if not os.path.isdir(dst_dir):
        print("Папка сборки не найдена:", dst_dir)
        return 1
    if not os.path.isfile(src):
        print("Инструкция не найдена:", src)
        return 0
    shutil.copy2(src, os.path.join(dst_dir, GUIDE_NAME))
    print("Инструкция скопирована в сборку:", GUIDE_NAME)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
