"""Додаткові компоненти, які ставляться з вікна застосунку, а не з консолі.

Транскрипція голосу тягне за собою ~200 МБ коліс (ctranslate2, tokenizers,
av). Класти таке в інсталятор заради функції, якою користується не кожен, —
несерйозно; вимагати `pip install` від людини, що поставила .exe, — теж.
Тому компоненти живуть окремо в %APPDATA%\\InstRef\\packages і додаються в
sys.path на старті: застосунок лишається легким, а кнопка «Встановити» робить
рівно те, що зробила б консоль.

Ставити треба ЧИМОСЬ. У запуску з вихідників це той самий інтерпретатор, що
запустив застосунок. У зібраному .exe інтерпретатора немає — pip у нього не
вбудований, — тож шукається системний Python тієї ж версії (колеса з
бінарними розширеннями сумісні лише в межах однієї x.y), а якщо його немає,
застосунок кладе поруч embeddable-збірку з python.org. Це єдиний спосіб
обійтися без «відкрий консоль».
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .config import FROZEN, app_dir

Progress = Callable[[str], None]

PACKAGES_DIR = app_dir() / "packages"
PYTHON_DIR = app_dir() / "python"

PY_TAG = f"{sys.version_info.major}.{sys.version_info.minor}"
EMBED_URL = ("https://www.python.org/ftp/python/{v}/python-{v}-embed-amd64.zip")
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

WHISPER_SIZES = ["tiny", "base", "small", "medium", "large-v3"]


class ExtrasError(RuntimeError):
    """Те, що варто показати людині дослівно."""


@dataclass
class Component:
    key: str
    title: str
    packages: List[str]
    module: str
    hint: str = ""
    size: str = ""
    dists: List[str] = field(default_factory=list)   # що видаляти при знесенні

    @property
    def distributions(self) -> List[str]:
        return self.dists or [p.split("[")[0] for p in self.packages]


COMPONENTS: List[Component] = [
    Component(
        key="whisper",
        title="Транскрипція голосу (faster-whisper)",
        packages=["faster-whisper"],
        module="faster_whisper",
        hint="Розшифровує голос за кадром у текст: він іде моделі як контекст "
             "і в нотатку Eagle. Потрібен для туторіалів, де техніку пояснюють "
             "словами, а кадри цього не передають.",
        size="≈250 МБ",
        dists=["faster-whisper", "ctranslate2", "tokenizers", "huggingface-hub",
               "onnxruntime", "av", "coloredlogs", "humanfriendly", "flatbuffers",
               "sympy", "mpmath", "protobuf", "tqdm", "filelock", "fsspec",
               "hf-xet", "hf_transfer"],
    ),
]


def component(key: str) -> Component:
    for item in COMPONENTS:
        if item.key == key:
            return item
    raise ExtrasError(f"Невідомий компонент: {key}")


# ------------------------------------------------------------------ sys.path
def ensure_path() -> None:
    """Додає теку компонентів у sys.path — викликати ДО імпорту компонента."""
    target = str(PACKAGES_DIR)
    if PACKAGES_DIR.is_dir() and target not in sys.path:
        sys.path.insert(0, target)
    # Бінарні колеса кладуть .dll поруч із пакетом; на Windows їх треба
    # показати завантажувачу явно, інакше імпорт падає «DLL load failed».
    if os.name == "nt" and PACKAGES_DIR.is_dir():
        for name in ("ctranslate2", "onnxruntime", "av"):
            folder = PACKAGES_DIR / name
            if folder.is_dir():
                try:
                    os.add_dll_directory(str(folder))
                except (OSError, AttributeError):
                    pass


def installed_version(comp: Component) -> str:
    """Версія встановленого компонента або порожній рядок."""
    ensure_path()
    try:
        from importlib import metadata

        return metadata.version(comp.distributions[0])
    except Exception:  # noqa: BLE001 — «нема» теж відповідь
        return ""


def is_installed(comp: Component) -> bool:
    if installed_version(comp):
        return True
    ensure_path()
    try:
        from importlib import util

        return util.find_spec(comp.module) is not None
    except Exception:  # noqa: BLE001
        return False


def status(comp: Component) -> Tuple[bool, str]:
    version = installed_version(comp)
    if version:
        return True, f"встановлено {version}"
    if is_installed(comp):
        return True, "встановлено"
    return False, "не встановлено"


# --------------------------------------------------------------- інтерпретатор
def _probe(executable: str) -> Optional[str]:
    """Повертає «x.y», якщо це робочий Python з pip; інакше None."""
    try:
        result = subprocess.run(
            [executable, "-c",
             "import sys,importlib.util as u;"
             "print(f'{sys.version_info.major}.{sys.version_info.minor}',"
             "int(u.find_spec('pip') is not None))"],
            capture_output=True, text=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    parts = (result.stdout or "").split()
    if result.returncode != 0 or len(parts) != 2 or parts[1] != "1":
        return None
    return parts[0]


_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _candidates() -> List[str]:
    names = []
    if os.name == "nt":
        names += [f"py -{PY_TAG}", "python", "python3"]
        for base in (os.environ.get("LOCALAPPDATA", ""), "C:\\"):
            if base:
                names.append(str(Path(base) / "Programs" / "Python"
                                  / f"Python{PY_TAG.replace('.', '')}" / "python.exe"))
    else:
        names += [f"python{PY_TAG}", "python3", "python"]
    return names


def find_python() -> Optional[str]:
    """Інтерпретатор, яким можна ставити колеса для ЦЬОГО застосунку.

    Версія має збігатися до x.y: колесо з бінарним розширенням, зібране під
    3.11, у 3.12 просто не імпортується.
    """
    if not FROZEN:
        return sys.executable
    embedded = PYTHON_DIR / ("python.exe" if os.name == "nt" else "bin/python3")
    if embedded.exists() and _probe(str(embedded)) == PY_TAG:
        return str(embedded)
    for name in _candidates():
        if name.startswith("py -"):
            launcher = shutil.which("py")
            if not launcher:
                continue
            path = _py_launcher_path(launcher)
            if path and _probe(path) == PY_TAG:
                return path
            continue
        found = shutil.which(name) if not Path(name).is_absolute() else name
        if found and Path(found).exists() and _probe(found) == PY_TAG:
            return found
    return None


def _py_launcher_path(launcher: str) -> Optional[str]:
    """`py -3.12` → повний шлях до python.exe (щоб не тягати аргументи далі)."""
    try:
        result = subprocess.run(
            [launcher, f"-{PY_TAG}", "-c", "import sys;print(sys.executable)"],
            capture_output=True, text=True, timeout=30, creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    path = (result.stdout or "").strip()
    return path if result.returncode == 0 and path else None


def python_label() -> str:
    """Що показати у вікні поруч із рядком «Чим ставити»."""
    if not FROZEN:
        return f"Python {PY_TAG} застосунку"
    found = find_python()
    if not found:
        return f"Python {PY_TAG} не знайдено — застосунок завантажить свій"
    if str(PYTHON_DIR) in found:
        return f"власний Python {PY_TAG} (у теці застосунку)"
    return f"системний Python {PY_TAG}"


# ----------------------------------------------------------- embeddable Python
def _download(url: str, dest: Path, progress: Progress) -> Path:
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(dest, "wb") as handle:
            for chunk in response.iter_content(1 << 16):
                handle.write(chunk)
    return dest


def embed_urls() -> List[str]:
    """Кандидати архіву: точна версія, далі — сусідні патчі.

    Embeddable-збірка є не для кожного патч-релізу (пізні виходять лише як
    вихідники), тому перебираємо вниз від поточного.
    """
    micro = sys.version_info.micro
    return [EMBED_URL.format(v=f"{PY_TAG}.{m}") for m in range(micro, -1, -1)]


def bootstrap_python(progress: Progress = lambda *a: None) -> str:
    """Кладе поруч із налаштуваннями embeddable-Python і pip до нього."""
    if os.name != "nt":
        raise ExtrasError("Автоматична установка Python є лише для Windows.")
    PYTHON_DIR.mkdir(parents=True, exist_ok=True)
    archive = PYTHON_DIR / "python-embed.zip"
    last = ""
    for url in embed_urls():
        progress(f"Завантажую {url.rsplit('/', 1)[-1]}…")
        try:
            _download(url, archive, progress)
            break
        except Exception as exc:  # noqa: BLE001 — пробуємо наступний патч
            last = f"{exc}"
    else:
        raise ExtrasError(f"Не вдалось завантажити Python {PY_TAG}: {last}")

    progress("Розпаковую…")
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(PYTHON_DIR)
    archive.unlink(missing_ok=True)

    # Без цього embeddable-збірка не бачить встановлені пакети й не має site.
    for pth in PYTHON_DIR.glob("python*._pth"):
        lines = pth.read_text(encoding="utf-8").splitlines()
        lines = [line[1:] if line.strip() == "#import site" else line for line in lines]
        if "Lib\\site-packages" not in lines:
            lines.append("Lib\\site-packages")
        pth.write_text("\n".join(lines) + "\n", encoding="utf-8")

    progress("Ставлю pip…")
    get_pip = PYTHON_DIR / "get-pip.py"
    _download(GET_PIP_URL, get_pip, progress)
    executable = PYTHON_DIR / "python.exe"
    code, output = _run([str(executable), str(get_pip), "--no-warn-script-location"],
                        progress)
    get_pip.unlink(missing_ok=True)
    if code != 0:
        raise ExtrasError("pip не встановився:\n" + output[-400:])
    return str(executable)


def _run(cmd: List[str], progress: Progress, timeout: int = 3600) -> Tuple[int, str]:
    """Запускає команду, віддаючи рядки виводу в журнал по мірі появи."""
    lines: List[str] = []
    try:
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW,
        )
    except OSError as exc:
        raise ExtrasError(f"Не вдалось запустити {Path(cmd[0]).name}: {exc}") from exc
    assert process.stdout is not None
    for raw in process.stdout:
        line = raw.rstrip()
        if line:
            lines.append(line)
            progress(line)
    process.wait(timeout=timeout)
    return process.returncode, "\n".join(lines)


# ------------------------------------------------------------------ установка
def install(key: str, progress: Progress = lambda *a: None) -> str:
    comp = component(key)
    executable = find_python()
    if not executable:
        progress(f"Python {PY_TAG} у системі не знайшовся — беру власний.")
        executable = bootstrap_python(progress)
    PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    progress(f"Ставлю {', '.join(comp.packages)} у {PACKAGES_DIR}…")
    code, output = _run(
        [executable, "-m", "pip", "install", "--upgrade", "--no-input",
         "--no-warn-script-location", "--disable-pip-version-check",
         "--only-binary=:all:", "--target", str(PACKAGES_DIR), *comp.packages],
        progress,
    )
    if code != 0:
        tail = "\n".join(output.strip().splitlines()[-6:])
        raise ExtrasError(f"pip повернув помилку:\n{tail}")
    ensure_path()
    _invalidate()
    if not is_installed(comp):
        raise ExtrasError("pip відпрацював, але пакет не імпортується. "
                          f"Подивись, що лежить у {PACKAGES_DIR}.")
    return f"{comp.title}: {status(comp)[1]}."


def _invalidate() -> None:
    from importlib import invalidate_caches

    invalidate_caches()


def uninstall(key: str, progress: Progress = lambda *a: None) -> str:
    """Прибирає файли компонента з теки пакетів.

    `pip uninstall` не вміє --target, тому йдемо за RECORD кожного дистрибутива:
    це те саме, що зробив би pip, тільки в межах нашої теки.
    """
    comp = component(key)
    if not PACKAGES_DIR.is_dir():
        return "Нічого видаляти."
    removed = 0
    for dist in comp.distributions:
        removed += _remove_dist(dist, progress)
    _invalidate()
    return f"Видалено {removed} файл(ів). Перезапусти застосунок, щоб звільнити .dll."


def _remove_dist(dist: str, progress: Progress) -> int:
    stem = dist.replace("-", "_").lower()
    removed = 0
    for info in list(PACKAGES_DIR.glob("*.dist-info")):
        if not info.name.lower().startswith(f"{stem}-"):
            continue
        record = info / "RECORD"
        if record.exists():
            for line in record.read_text(encoding="utf-8", errors="replace").splitlines():
                relative = line.split(",", 1)[0].strip()
                if not relative or relative.startswith(("..", "/")):
                    continue
                path = PACKAGES_DIR / relative
                try:
                    if path.is_file():
                        path.unlink()
                        removed += 1
                except OSError as exc:
                    progress(f"не видалилось {path.name}: {exc}")
        shutil.rmtree(info, ignore_errors=True)
        progress(f"прибрано {info.name}")
    _prune_empty()
    return removed


def _prune_empty() -> None:
    for folder in sorted(PACKAGES_DIR.rglob("*"), key=lambda p: -len(p.parts)):
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()


# --------------------------------------------------------------- моделі whisper
def model_cache() -> Path:
    base = os.environ.get("HF_HOME") or os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def model_installed(size: str) -> bool:
    folder = model_cache()
    if not folder.is_dir():
        return False
    needle = f"faster-whisper-{size}".lower()
    return any(needle in item.name.lower() for item in folder.iterdir())


def download_model(size: str, progress: Progress = lambda *a: None) -> str:
    """Тягне ваги моделі наперед, щоб перша синхронізація не «зависала» мовчки."""
    ensure_path()
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ExtrasError("Спочатку встанови faster-whisper.") from exc
    progress(f"Завантажую модель «{size}» (перший раз — довго)…")
    try:
        WhisperModel(size, device="cpu", compute_type="int8")
    except Exception as exc:  # noqa: BLE001
        raise ExtrasError(f"Модель не завантажилась: {exc}") from exc
    return f"Модель «{size}» готова — лежить у {model_cache()}."
