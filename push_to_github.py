# -*- coding: utf-8 -*-
"""
push_to_github.py — инициализация git-репозитория и пуш на GitHub.
Теперь поддерживает перезапись удалённой истории.

Примеры:
  # Перезаписать ветку main удалённо (без удаления чужих веток/тэгов):
  python push_to_github.py --remote https://github.com/<user>/<repo>.git --name "Your Name" --email you@example.com --overwrite

  # Полная синхронизация (зеркалирование всех веток/тэгов, удалит лишнее на GitHub):
  python push_to_github.py --remote https://github.com/<user>/<repo>.git --mirror

  # Явный путь к git.exe:
  python push_to_github.py --remote ... --git "C:\\Program Files\\Git\\cmd\\git.exe"
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from textwrap import dedent

DEFAULT_GITIGNORE = dedent("""\
    # Byte-compiled / cache
    __pycache__/
    *.py[cod]
    *$py.class

    # Environments
    .env
    .venv/
    venv/

    # Editors/IDE
    .idea/
    .vscode/
    *.iml

    # Build artifacts
    build/
    dist/
    *.egg-info/

    # Logs
    *.log

    # OS junk
    .DS_Store
    Thumbs.db

    # Project data (ignore by default)
    data/*.db
    *.sqlite
    *.sqlite3
    *.tsv
    *.csv
    *.tmp
""")

DEFAULT_GITATTRIBUTES_LFS = dedent("""\
    *.db filter=lfs diff=lfs merge=lfs -text
    *.sqlite filter=lfs diff=lfs merge=lfs -text
    *.sqlite3 filter=lfs diff=lfs merge=lfs -text
    *.tsv filter=lfs diff=lfs merge=lfs -text
    *.csv filter=lfs diff=lfs merge=lfs -text
    *.bin filter=lfs diff=lfs merge=lfs -text
""")

def find_git(explicit: str | None) -> str:
    for candidate in [explicit, os.environ.get("GIT_EXE"), os.environ.get("GIT")]:
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    which = shutil.which("git")
    if which:
        return which
    common_paths = [
        r"C:\Users\V.Pyatakov\AppData\Local\Programs\Git\cmd\git.exe",
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files (x86)\Git\bin\git.exe",
        r"C:\Program Files (x86)\Git\cmd\git.exe",
    ]
    for p in common_paths:
        if Path(p).exists():
            return p
    raise FileNotFoundError("git не найден. Установите Git for Windows или укажите путь параметром --git.")

def run(cmd, cwd=None, check=True):
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, shell=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}")
    return result

def run_git(git_exe: str, args: list[str], cwd: Path, check=True):
    return run([git_exe] + args, cwd=cwd, check=check)

def ensure_in_repo(git: str, project_dir: Path, branch: str):
    git_dir = project_dir / ".git"
    if git_dir.exists():
        run_git(git, ["rev-parse", "--is-inside-work-tree"], project_dir)
    else:
        run_git(git, ["init"], project_dir)
        run_git(git, ["branch", "-M", branch], project_dir)

def git_config(git: str, project_dir: Path, name: str | None, email: str | None):
    if name:
        run_git(git, ["config", "user.name", name], project_dir)
    if email:
        run_git(git, ["config", "user.email", email], project_dir)
    run_git(git, ["config", "core.autocrlf", "true"], project_dir)
    run_git(git, ["config", "core.longpaths", "true"], project_dir)

def ensure_file_contains(path: Path, content: str):
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="ignore")
        to_add = []
        have = set(line.rstrip() for line in existing.splitlines())
        for line in content.splitlines():
            if line.rstrip() not in have:
                to_add.append(line)
        if to_add:
            with path.open("a", encoding="utf-8") as f:
                f.write(("\n" if not existing.endswith("\n") else "") + "\n".join(to_add) + "\n")
    else:
        path.write_text(content, encoding="utf-8")

def setup_ignores(git: str, project_dir: Path, apply_lfs: bool):
    gi = project_dir / ".gitignore"
    ensure_file_contains(gi, DEFAULT_GITIGNORE)
    if apply_lfs:
        try:
            run_git(git, ["lfs", "install"], project_dir, check=False)
        except Exception:
            pass
        ga = project_dir / ".gitattributes"
        ensure_file_contains(ga, DEFAULT_GITATTRIBUTES_LFS)

def ensure_readme(project_dir: Path):
    readme = project_dir / "README.md"
    if not readme.exists():
        readme.write_text("# Project\n\nОписание проекта.\n", encoding="utf-8")

def initial_commit_if_needed(git: str, project_dir: Path, message: str):
    run_git(git, ["add", "."], project_dir)
    status = run_git(git, ["status", "--porcelain"], project_dir)
    if status.stdout.strip():
        run_git(git, ["commit", "-m", message], project_dir)
    else:
        print("Нет изменений для коммита — пропускаю commit.")

def set_remote(git: str, project_dir: Path, remote_url: str, remote_name: str = "origin"):
    remotes = run_git(git, ["remote"], project_dir)
    if remote_name in remotes.stdout.split():
        run_git(git, ["remote", "set-url", remote_name, remote_url], project_dir)
    else:
        run_git(git, ["remote", "add", remote_name, remote_url], project_dir)

def push(git: str, project_dir: Path, branch: str, remote_name: str = "origin",
         overwrite: bool = False, mirror: bool = False):
    # Если нужен полный зеркальный пуш (ОСТОРОЖНО: удалит лишние ветки/теги на GitHub)
    if mirror:
        print("[WARN] Выполняю зеркалирование: git push --mirror (все ветки/теги, с удалением лишних на удалённом).")
        run_git(git, ["push", "--mirror", remote_name], project_dir, check=True)
        return

    # Обычный пуш (или с перезаписью одной ветки)
    args = ["push", "-u", remote_name, branch]
    if overwrite:
        # Перезаписываем историю ветки на удалённом
        args.append("--force-with-lease")
    try:
        run_git(git, args, project_dir, check=True)
    except RuntimeError as e:
        # Если не указан overwrite — пробуем мягкий pull --rebase и повторный пуш
        if not overwrite:
            print("Первый push не удался. Пробую pull --rebase и повторный push ...", file=sys.stderr)
            run_git(git, ["pull", "--rebase", remote_name, branch], project_dir, check=False)
            run_git(git, ["push", "-u", remote_name, branch], project_dir, check=True)
        else:
            # В жёстком режиме даём пользователю подсказку про защиту ветки
            raise RuntimeError(
                f"Перезаписать ветку не получилось. Возможно, ветка защищена на GitHub.\n{e}"
            )

def main():
    parser = argparse.ArgumentParser(description="Подготовка и публикация проекта на GitHub (с опцией перезаписи).")
    parser.add_argument("--project-dir", default=".", help="Корень проекта (по умолчанию текущая папка).")
    parser.add_argument("--remote", required=True, help="URL удалённого репозитория, напр. https://github.com/user/repo.git")
    parser.add_argument("--branch", default="main", help="Имя основной ветки (по умолчанию main).")
    parser.add_argument("--name", default=None, help="Git user.name (необязательно).")
    parser.add_argument("--email", default=None, help="Git user.email (необязательно).")
    parser.add_argument("--message", default="Initial commit", help="Сообщение первого коммита.")
    parser.add_argument("--lfs", action="store_true", help="Включить Git LFS трекинг больших файлов.")
    parser.add_argument("--git", default=None, help="Полный путь к git.exe (если не в PATH).")
    parser.add_argument("--overwrite", action="store_true", help="Перезаписать удалённую ветку (--force-with-lease).")
    parser.add_argument("--mirror", action="store_true", help="Зеркалировать весь локальный репозиторий на удалённый (--mirror). ОПАСНО!")
    args = parser.parse_args()

    if args.overwrite and args.mirror:
        print("Нельзя одновременно использовать --overwrite и --mirror.", file=sys.stderr)
        sys.exit(2)

    project_dir = Path(args.project_dir).resolve()
    if not project_dir.exists():
        print(f"Папка не найдена: {project_dir}", file=sys.stderr)
        sys.exit(1)

    git_exe = find_git(args.git)
    print(f"Использую git: {git_exe}")

    ensure_in_repo(git_exe, project_dir, args.branch)
    git_config(git_exe, project_dir, args.name, args.email)
    setup_ignores(git_exe, project_dir, apply_lfs=args.lfs)
    ensure_readme(project_dir)
    initial_commit_if_needed(git_exe, project_dir, args.message)
    set_remote(git_exe, project_dir, args.remote)
    push(git_exe, project_dir, args.branch, overwrite=args.overwrite, mirror=args.mirror)

    print("\nГотово! Проект отправлен на GitHub.\nЕсли спросит логин/пароль — используйте GitHub-логин и Personal Access Token.")

if __name__ == "__main__":
    main()
