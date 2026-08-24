"""Тиха синхронізація без вікна консолі (використовується Планувальником завдань)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from igsaved.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(["--sync", "--quiet"]))
