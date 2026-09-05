"""Оновлення застосунку з GitHub Releases однією кнопкою.

Два способи запуску — два шляхи:

* **зібраний .exe** (`config.FROZEN`): качаємо інсталятор із релізу й запускаємо
  його тихо; він закриє застосунок, замінить файли й запустить знову. Робочі
  файли лежать у %APPDATA%\\InstRef і інсталятора не стосуються;
* **запуск із вихідників**: качаємо архів тегу, розпаковуємо в тимчасову теку
  й переносимо поверх проєкту все, крім робочих файлів (config.json,
  session.json, state.db…), `.git` і `.venv`; потім `pip install -r
  requirements.txt` тим самим інтерпретатором, і перезапуск.

Прогрес і результат — через колбеки: модуль не знає про Qt.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import FROZEN, resource_dir
from .updates import installer_asset

Progress = Callable[[str, int, int], None]      # (повідомлення, зроблено, всього)

# Те, що належить користувачу, а не застосунку. Ніколи не перезаписується.
KEEP = {
    "config.json", "session.json", "session.json.tmp", "device.json", "state.db",
    "state.db-wal", "state.db-shm", "taxonomy.json", "last_run.json", "sync.lock",
}
KEEP_DIRS = {".git", ".venv", "venv", "logs", "backups", "downloads", "_archive",
             "_to_delete", "__pycache__", ".pytest_cache", ".ruff_cache"}
CHUNK = 256 * 1024


class UpdateError(RuntimeError):
    pass


def project_root() -> Path:
    """Тека, у якій живе код при запуску з вихідників."""
    return resource_dir()


def download(url: str, dest: Path, progress: Progress = lambda *a: None,
             timeout: int = 30) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, stream=True, timeout=timeout,
                          headers={"User-Agent": "InstRef"}) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            with open(dest, "wb") as handle:
                for chunk in resp.iter_content(CHUNK):
                    if not chunk:
                        continue
                    handle.write(chunk)
                    done += len(chunk)
                    progress(f"Завантаження {dest.name}", done, total)
    except requests.RequestException as exc:
        raise UpdateError(f"Не вдалось завантажити: {exc}") from exc
    return dest


# ------------------------------------------------------------ зібраний .exe
def update_installed(latest: dict, progress: Progress = lambda *a: None) -> str:
    """Качає інсталятор і запускає його. Повертає шлях до інсталятора.

    Далі має відбутись вихід із застосунку: інсталятор чекає, поки .exe
    закриється (/CLOSEAPPLICATIONS), і запускає новий (/RESTARTAPPLICATIONS).
    """
    asset = installer_asset(latest)
    if not asset or not asset.get("url"):
        raise UpdateError("У релізі немає інсталятора InstRef-Setup-*.exe.")
    target = Path(tempfile.gettempdir()) / asset["name"]
    download(asset["url"], target, progress)
    if asset.get("size") and target.stat().st_size != int(asset["size"]):
        raise UpdateError("Інсталятор завантажився не повністю — спробуй ще раз.")
    progress("Запуск інсталятора…", 1, 1)
    try:
        subprocess.Popen(
            [str(target), "/SILENT", "/SUPPRESSMSGBOXES", "/CLOSEAPPLICATIONS",
             "/RESTARTAPPLICATIONS", "/NORESTART"],
            close_fds=True,
        )
    except OSError as exc:
        raise UpdateError(f"Не вдалось запустити інсталятор: {exc}") from exc
    return str(target)


# ------------------------------------------------------------- з вихідників
def _should_skip(relative: Path) -> bool:
    parts = relative.parts
    if not parts:
        return True
    if parts[0] in KEEP_DIRS:
        return True
    if relative.name in KEEP:
        return True
    return False


def apply_source_archive(archive: Path, root: Path, progress: Progress = lambda *a: None) -> int:
    """Переносить файли з архіву тегу поверх проєкту. Повертає кількість файлів.

    Архів GitHub має один кореневий каталог `Amriel-InstRef-<sha>/` — його
    зрізаємо. Робочі файли й теки з KEEP не чіпаються.
    """
    copied = 0
    with zipfile.ZipFile(archive) as bundle:
        names = [n for n in bundle.namelist() if not n.endswith("/")]
        if not names:
            raise UpdateError("Архів порожній.")
        prefix = names[0].split("/", 1)[0] + "/"
        total = len(names)
        for index, name in enumerate(names, start=1):
            relative = Path(name[len(prefix):]) if name.startswith(prefix) else Path(name)
            if _should_skip(relative):
                continue
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            data = bundle.read(name)
            if relative.suffix.lower() == ".bat":
                # Windows виконує .bat лише з CRLF; архів з git несе LF.
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
            tmp = target.with_suffix(target.suffix + ".new")
            tmp.write_bytes(data)
            os.replace(tmp, target)
            copied += 1
            progress("Оновлення файлів", index, total)
    return copied


def install_requirements(root: Path, progress: Progress = lambda *a: None) -> str:
    """`pip install -r requirements.txt` тим інтерпретатором, що запустив застосунок."""
    requirements = root / "requirements.txt"
    if not requirements.exists():
        return "requirements.txt не знайдено — залежності не оновлював."
    progress("Оновлення залежностей (pip)…", 0, 0)
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements), "-q",
             "--disable-pip-version-check"],
            capture_output=True, text=True, timeout=900, cwd=str(root),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise UpdateError(f"pip не впорався: {exc}") from exc
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()[-5:]
        raise UpdateError("pip повернув помилку:\n" + "\n".join(tail))
    return "Залежності оновлено."


def update_from_source(latest: dict, progress: Progress = lambda *a: None,
                       root: Optional[Path] = None) -> str:
    """Повний шлях для запуску з вихідників. Повертає підсумок для журналу."""
    root = Path(root or project_root())
    if not (root / "igsaved").is_dir():
        raise UpdateError(f"У {root} немає пакета igsaved — не знаю, що оновлювати.")
    zip_url = latest.get("zipball")
    if not zip_url:
        raise UpdateError("У релізі немає посилання на архів вихідників.")
    with tempfile.TemporaryDirectory(prefix="instref-update-") as tmp:
        archive = download(zip_url, Path(tmp) / "source.zip", progress)
        try:
            copied = apply_source_archive(archive, root, progress)
        except zipfile.BadZipFile as exc:
            raise UpdateError("Архів пошкоджений.") from exc
    note = install_requirements(root, progress)
    return f"Оновлено {copied} файл(ів). {note}"


def restart_from_source(root: Optional[Path] = None) -> None:
    """Запускає нову копію застосунку з вихідників; вийти має викликач."""
    root = Path(root or project_root())
    entry = root / "app.pyw"
    if not entry.exists():
        entry = root / "entry.py"
    executable = sys.executable
    if sys.platform == "win32":
        # pythonw.exe — без консольного вікна, якщо є поруч.
        candidate = Path(executable).with_name("pythonw.exe")
        if candidate.exists():
            executable = str(candidate)
    subprocess.Popen([executable, str(entry)], cwd=str(root), close_fds=True)


def update(latest: dict, progress: Progress = lambda *a: None) -> str:
    """Той шлях, що відповідає способу запуску."""
    if FROZEN:
        path = update_installed(latest, progress)
        return f"Інсталятор запущено: {path}. Застосунок зараз закриється."
    return update_from_source(latest, progress)


def mode_label() -> str:
    return "встановлений застосунок (інсталятор)" if FROZEN else "запуск із вихідників"
