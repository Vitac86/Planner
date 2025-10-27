# -*- coding: utf-8 -*-
"""
Собирает все скрипты из папки (рекурсивно) в один файл.
Не падает молча: валидирует путь, печатает прогресс (в режиме --verbose),
пишет traceback в collect_all_error.log при любой ошибке.
"""
from __future__ import annotations
import os
import sys
import argparse
import traceback
from pathlib import Path
from typing import Iterable, List, Tuple, Sequence
import datetime as _dt

# ---------- настройки по умолчанию (устойчивые) ----------
HERE = Path(__file__).resolve().parent

# Авто-определение корня: если рядом есть crm/scripts — берём его,
# иначе берём текущую папку как корень обхода
if (HERE / "crm" / "scripts").is_dir():
    DEFAULT_ROOT = HERE / "crm" / "scripts"
else:
    DEFAULT_ROOT = HERE

DEFAULT_OUT = HERE / "Все_Скрипты.txt"

IGNORE_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode",
    "venv", ".venv", "env", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "node_modules",
}

# ---------- утилиты ----------
def eprint(*a, **kw):
    print(*a, file=sys.stderr, flush=True, **kw)

def vprint(verbose: bool, *a):
    if verbose:
        print(*a, flush=True)

def collect_scripts(root: Path,
                    exts: Iterable[str] = (".py",),
                    ignore_dirs: Iterable[str] = IGNORE_DIRS,
                    max_size_mb: float | None = None,
                    verbose: bool = False) -> List[Tuple[str, str]]:
    """
    Рекурсивно собирает файлы нужных расширений из root и подпапок.
    Возвращает список (relative_path, code).
    """
    root = root.resolve()
    exts = tuple(e.lower() for e in exts)
    ignore = set(ignore_dirs)

    if not root.exists():
        raise FileNotFoundError(f"Папка не найдена: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Это не папка: {root}")

    scripts: List[Tuple[str, str]] = []
    vprint(verbose, f"[collect] старт: {root}")

    for dirpath, dirnames, filenames in os.walk(root):
        # фильтруем подпапки на месте — os.walk туда не зайдёт
        before = list(dirnames)
        dirnames[:] = [d for d in dirnames if d not in ignore]
        dropped = set(before) - set(dirnames)
        if dropped and verbose:
            vprint(verbose, f"  └─ skip dirs: {', '.join(sorted(dropped))}")

        for fname in filenames:
            if not any(fname.lower().endswith(ext) for ext in exts):
                continue
            fpath = Path(dirpath) / fname

            try:
                if max_size_mb is not None and fpath.stat().st_size > max_size_mb * 1024 * 1024:
                    vprint(verbose, f"  └─ skip big file: {fpath}")
                    continue
            except OSError as ex:
                eprint(f"[warn] не удалось получить размер: {fpath} ({ex})")
                continue

            try:
                code = fpath.read_text(encoding="utf-8", errors="replace")
            except OSError as ex:
                eprint(f"[warn] не удалось прочитать: {fpath} ({ex})")
                continue

            rel = fpath.relative_to(root)
            rel_str = str(rel).replace("\\", "/")
            scripts.append((rel_str, code))
            vprint(verbose, f"  + {rel_str}")

    scripts.sort(key=lambda x: x[0].lower())
    vprint(verbose, f"[collect] готово: файлов {len(scripts)}")
    return scripts

def _lang_for(path: str) -> str:
    p = path.lower()
    if p.endswith(".py"): return "python"
    if p.endswith(".bas"): return ""
    if p.endswith(".frm"): return ""
    return ""

def write_all_scripts(scripts: Sequence[Tuple[str, str]], output_file: Path, verbose: bool = False) -> None:
    output_file = output_file.resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    vprint(verbose, f"[write] -> {output_file}")

    with open(output_file, "w", encoding="utf-8", newline="\n") as out:
        header = f"# Сборка скриптов\n# Сгенерировано: {_dt.datetime.now():%Y-%m-%d %H:%M:%S}\n# Всего файлов: {len(scripts)}\n\n"
        out.write(header)
        for relpath, code in scripts:
            lang = _lang_for(relpath)
            out.write(f"### {relpath}\n")
            out.write(f"```{lang}\n")
            out.write(code)
            out.write("\n```\n\n")

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Собрать все скрипты рекурсивно в один файл.")
    ap.add_argument("--root", type=str, default=str(DEFAULT_ROOT),
                    help=f"Корневая папка (по умолчанию: {DEFAULT_ROOT})")
    ap.add_argument("--out", type=str, default=str(DEFAULT_OUT),
                    help=f"Итоговый файл (по умолчанию: {DEFAULT_OUT})")
    ap.add_argument("--ext", nargs="+", default=[".py"],
                    help="Какие расширения включать (напр.: --ext .py .bas .frm)")
    ap.add_argument("--max-size-mb", type=float, default=None,
                    help="Максимальный размер файла в МБ (по умолчанию без ограничения)")
    ap.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод")
    args = ap.parse_args(argv)

    root = Path(args.root)
    out = Path(args.out)

    print(f"[run] root={root}")
    print(f"[run] out ={out}")
    print(f"[run] ext ={', '.join(args.ext)}")
    if args.max_size_mb:
        print(f"[run] max_size_mb={args.max_size_mb}")

    scripts = collect_scripts(root, exts=args.ext, max_size_mb=args.max_size_mb, verbose=args.verbose)
    write_all_scripts(scripts, out, verbose=args.verbose)
    print(f"Собрано файлов: {len(scripts)}")
    print(f"Все скрипты сохранены в {out}")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit as se:
        raise
    except Exception:
        # Ловим любые падения и пишем в лог — чтобы не было «молчаливо»
        log_path = Path(__file__).with_name("collect_all_error.log")
        tb = traceback.format_exc()
        try:
            log_path.write_text(tb, encoding="utf-8")
        except Exception:
            pass
        eprint("[fatal] скрипт завершился с ошибкой. См. лог:", log_path)
        eprint(tb)
        # Если запустили двойным кликом без консоли — задержка, чтобы увидеть сообщение
        if os.name == "nt" and not sys.stdin.isatty():
            os.system("pause")
        sys.exit(1)
