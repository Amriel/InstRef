"""Запуск GUI без вікна консолі (використовується автозапуском Windows)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from igsaved.__main__ import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
