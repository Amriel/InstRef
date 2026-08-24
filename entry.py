"""Єдина точка входу зібраного застосунку.

Один .exe замість двох свідомо: PySide6 з opencv важать ~400 МБ, і другий
бінар подвоїв би інсталятор заради єдиної відмінності — наявності консолі.
Планувальник викликає той самий файл із --sync.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    argv = sys.argv[1:]
    if "--sync" in argv:
        from igsaved.cli import main as cli_main

        return cli_main(argv)
    from igsaved.__main__ import main as gui_main

    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
