# -*- coding: utf-8 -*-
"""
project_inventory.py
Инвентаризация проекта:
1) Полное дерево каталогов/файлов от корня (относительные пути).
2) Сборка указанных "скриптовых" файлов в единый Markdown с кодовыми блоками.

Пример:
  python project_inventory.py --root "D:/Work/BigProj" --out "Инвентарь.md" --ext .py .lua .sql -v
"""
from __future__ import annotations
import os
import sys
import argparse
import traceback
from pathlib import Path
from typing import Iterable, List, Tuple, Sequence
import datetime as _dt

HERE = Path(__file__).resolve().parent

# Автовыбор корня как в твоём примере (при желании поправь под себя)
if (HERE / "crm" / "scripts").is_dir():
    DEFAULT_ROOT = HERE / "crm" / "scripts"
else:
    DEFAULT_ROOT = HERE

DEFAULT_OUT = HERE / "Инвентарь_проекта.md"

IGNORE_DIRS = {
    "__pycache__", ".git", ".idea", ".vscode",
    "venv", ".venv", "env", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "node_modules",
}

IGNORE_FILES = set()  # можно добавить маски или имена, если нужно

# ---------- утилиты ----------
def eprint(*a, **kw):
    print(*a, file=sys.stderr, flush=True, **kw)

def vprint(verbose: bool, *a, **kw):
    if verbose:
        print(*a, flush=True, **kw)

def _lang_for(path: str) -> str:
    p = path.lower()
    if p.endswith(".py"): return "python"
    if p.endswith(".lua"): return "lua"
    if p.endswith(".sql"): return "sql"
    if p.endswith(".js"): return "javascript"
    if p.endswith(".ts"): return "typescript"
    if p.endswith(".json"): return "json"
    if p.endswith(".sh"): return "bash"
    if p.endswith(".ps1"): return "powershell"
    if p.endswith(".bat") or p.endswith(".cmd"): return ""
    if p.endswith(".yml") or p.endswith(".yaml"): return "yaml"
    if p.endswith(".ini") or p.endswith(".cfg"): return ""
    return ""

def _safe_rel(path: Path, root: Path) -> str:
    rel = path.relative_to(root)
    return str(rel).replace("\\", "/")

# ---------- сбор дерева ----------
def build_tree_lines(root: Path,
                     ignore_dirs: Iterable[str],
                     ignore_files: Iterable[str],
                     verbose: bool = False) -> List[str]:
    """
    Возвращает "красивое" дерево (список строк) со всеми файлами/папками.
    """
    root = root.resolve()
    ignore_dirs = set(ignore_dirs)
    ignore_files = set(ignore_files)

    if not root.exists():
        raise FileNotFoundError(f"Папка не найдена: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"Это не папка: {root}")

    vprint(verbose, f"[tree] старт: {root}")
    lines: List[str] = [f"{root.name}/"]

    # Для детерминированности сортируем
    def dir_entries(p: Path) -> Tuple[List[Path], List[Path]]:
        dirs, files = [], []
        for child in p.iterdir():
            name = child.name
            if child.is_dir():
                if name in ignore_dirs:
                    vprint(verbose, f"  └─ skip dir: {child}")
                    continue
                dirs.append(child)
            else:
                if name in ignore_files:
                    vprint(verbose, f"  └─ skip file: {child}")
                    continue
                files.append(child)
        return sorted(dirs, key=lambda x: x.name.lower()), sorted(files, key=lambda x: x.name.lower())

    def walk(node: Path, prefix: str):
        dirs, files = dir_entries(node)
        total = len(dirs) + len(files)
        for i, d in enumerate(dirs):
            is_last = (i == len(dirs) - 1) and (len(files) == 0)
            branch = "└── " if is_last else "├── "
            lines.append(f"{prefix}{branch}{d.name}/")
            new_prefix = f"{prefix}{'    ' if is_last else '│   '}"
            walk(d, new_prefix)

        for j, f in enumerate(files):
            is_last_file = (j == len(files) - 1)
            branch = "└── " if is_last_file else "├── "
            try:
                size = f.stat().st_size
                size_note = f" ({size} B)"
            except OSError:
                size_note = ""
            lines.append(f"{prefix}{branch}{f.name}{size_note}")

    walk(root, "")
    vprint(verbose, f"[tree] готово: {len(lines)} строк")
    return lines

# ---------- сбор скриптов ----------
def collect_scripts(root: Path,
                    exts: Iterable[str],
                    ignore_dirs: Iterable[str],
                    max_size_mb: float | None = None,
                    verbose: bool = False) -> List[Tuple[str, str]]:
    """
    Рекурсивно собирает файлы расширений exts из root.
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

            rel_str = _safe_rel(fpath, root)
            scripts.append((rel_str, code))
            vprint(verbose, f"  + {rel_str}")

    scripts.sort(key=lambda x: x[0].lower())
    vprint(verbose, f"[collect] готово: файлов {len(scripts)}")
    return scripts

# ---------- запись отчёта ----------
def write_report(tree_lines: Sequence[str],
                 scripts: Sequence[Tuple[str, str]],
                 output_file: Path,
                 verbose: bool = False) -> None:
    output_file = output_file.resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    vprint(verbose, f"[write] -> {output_file}")

    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(output_file, "w", encoding="utf-8", newline="\n") as out:
        # Шапка
        out.write(f"# Инвентарь проекта\n")
        out.write(f"_Сгенерировано: {ts}_\n\n")

        # Раздел 1: Полная структура
        out.write("## 1. Полная структура (относительно корня)\n\n")
        out.write("```\n")
        for line in tree_lines:
            out.write(line)
            out.write("\n")
        out.write("```\n\n")

        # Раздел 2: Содержимое скриптов
        out.write(f"## 2. Скрипты (всего: {len(scripts)})\n\n")
        for relpath, code in scripts:
            lang = _lang_for(relpath)
            out.write(f"### {relpath}\n")
            out.write(f"```{lang}\n")
            out.write(code)
            out.write("\n```\n\n")

# ---------- CLI ----------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Инвентаризация проекта: дерево и сборка скриптов в Markdown.")
    ap.add_argument("--root", type=str, default=str(DEFAULT_ROOT),
                    help=f"Корневая папка (по умолчанию: {DEFAULT_ROOT})")
    ap.add_argument("--out", type=str, default=str(DEFAULT_OUT),
                    help=f"Итоговый Markdown-файл (по умолчанию: {DEFAULT_OUT})")
    ap.add_argument("--ext", nargs="+", default=[".py"],
                    help="Расширения скриптов (напр.: --ext .py .lua .sql .js .ts)")
    ap.add_argument("--max-size-mb", type=float, default=None,
                    help="Максимальный размер файла (МБ) для включения в сборку кода")
    ap.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод")
    args = ap.parse_args(argv)

    root = Path(args.root)
    out = Path(args.out)

    print(f"[run] root={root}")
    print(f"[run] out ={out}")
    print(f"[run] ext ={', '.join(args.ext)}")
    if args.max_size_mb is not None:
        print(f"[run] max_size_mb={args.max_size_mb}")

    tree = build_tree_lines(root, IGNORE_DIRS, IGNORE_FILES, verbose=args.verbose)
    scripts = collect_scripts(root, exts=args.ext, ignore_dirs=IGNORE_DIRS,
                              max_size_mb=args.max_size_mb, verbose=args.verbose)
    write_report(tree, scripts, out, verbose=args.verbose)
    print(f"Строк в дереве: {len(tree)}")
    print(f"Скриптов собрано: {len(scripts)}")
    print(f"Готово: {out}")
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        # Всегда пишем трейсбек в лог, чтобы не «падать молча»
        log_path = Path(__file__).with_name("project_inventory_error.log")
        tb = traceback.format_exc()
        try:
            log_path.write_text(tb, encoding="utf-8")
        except Exception:
            pass
        eprint("[fatal] скрипт завершился с ошибкой. См. лог:", log_path)
        eprint(tb)
        if os.name == "nt" and not sys.stdin.isatty():
            os.system("pause")
        sys.exit(1)
