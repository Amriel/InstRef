"""Порівняння візуальних моделей LM Studio на ТВОЇХ обкладинках.

Загальні бенчмарки (MMMU, DocVQA) міряють зовсім інше — читання документів і
складні міркування. У нас задача простіша й вужча: одним поглядом віднести кадр
до пʼяти категорій. Тому єдина чесна відповідь на «яка модель краща для нашого
випадку» — прогнати їх на реальних кадрах і подивитись на швидкість та згоду
між моделями.

Як користуватись
----------------
1. У LM Studio завантаж кілька візуальних моделей одночасно
   (Developer → Start Server; моделі лишаються в памʼяті, сервер віддає всі).
2. .venv\\Scripts\\python.exe bench_vision.py
3. Дивись таблицю: час на кадр і що саме кожна модель відповіла.

Прапорці:
    --limit 12          скільки файлів узяти (типово 10)
    --frames 6          кадрів з кожного ролика (типово — як у налаштуваннях)
    --models a,b,c      конкретні моделі; типово всі завантажені
    --url http://...    інша адреса LM Studio
    --folder <шлях>     звідки брати файли (типово _thumbnails, _review і корінь)
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parent))

from igsaved.config import Config  # noqa: E402
from igsaved import frames as framegrab  # noqa: E402
from igsaved.vision import CATEGORY_LABELS, VisionClient, VisionError  # noqa: E402

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MEDIA_EXT = IMAGE_EXT | framegrab.VIDEO_EXT


def load_shots(path: Path, count: int) -> tuple[List[bytes], str]:
    """Кадри для одного файлу: з відео — кілька, з картинки — вона сама.

    Бенчмарк має міряти те саме, що робить синхронізація, інакше цифри
    красиві, а на реальних роликах модель поводиться інакше.
    """
    if path.suffix.lower() in framegrab.VIDEO_EXT:
        shots = framegrab.extract(path, count)
        if shots:
            return shots, "reel"
        return [], "reel"
    try:
        return [framegrab.shrink_image(path.read_bytes())], "photo"
    except OSError:
        return [], "photo"


def collect_images(cfg: Config, folder: str, limit: int) -> List[Path]:
    roots = [Path(folder)] if folder else [cfg.thumbs_dir, cfg.review_dir, cfg.root]
    found: List[Path] = []
    seen = set()
    for root in roots:
        if not root or not root.exists():
            continue
        for path in sorted(root.iterdir()):
            if path.is_file() and path.suffix.lower() in MEDIA_EXT and path.name not in seen:
                seen.add(path.name)
                found.append(path)
            if len(found) >= limit:
                return found
    return found


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--frames", type=int, default=0,
                        help="кадрів з відео; типово — як у налаштуваннях")
    parser.add_argument("--models", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--folder", default="")
    args = parser.parse_args(argv)

    cfg = Config.load()
    url = args.url or cfg.vision_url
    args.frames = args.frames or max(1, int(cfg.vision_frames or 1))

    try:
        available = VisionClient(url).list_models()
    except VisionError as exc:
        print(f"✖ {exc}")
        print("  У LM Studio: Developer → Start Server, і завантаж хоча б одну модель.")
        return 2

    models = [m.strip() for m in args.models.split(",") if m.strip()] or available
    unknown = [m for m in models if m not in available]
    if unknown:
        print(f"⚠ не завантажені в LM Studio: {', '.join(unknown)}")
        models = [m for m in models if m in available]
    if not models:
        print("✖ Немає моделей для перевірки.")
        return 2

    images = collect_images(cfg, args.folder, args.limit)
    if not images:
        print("✖ Не знайдено картинок. Спершу щось завантаж або вкажи --folder.")
        return 2

    print(f"Кадрів: {len(images)} · моделей: {len(models)}\n")
    results: Dict[str, List] = {}
    timings: Dict[str, List[float]] = {}

    for model in models:
        client = VisionClient(url, model)
        answers, times = [], []
        print(f"── {model}")
        for image_path in images:
            shots, kind = load_shots(image_path, args.frames)
            if not shots:
                continue
            started = time.monotonic()
            verdict = client.classify(shots, kind=kind)
            elapsed = time.monotonic() - started
            times.append(elapsed)
            answers.append(verdict)
            mark = verdict.label if verdict.ok else f"✖ {verdict.error}"
            extra = f"{len(shots)}к" + ("+опис" if verdict.description else "")
            print(f"   {elapsed:5.1f}s  {mark:<12} {extra:<9} {image_path.name[:44]}")
        results[model] = answers
        timings[model] = times
        print()

    print("=" * 74)
    print(f"{'модель':<38} {'сер. час':>9} {'медіана':>9} {'збоїв':>7}")
    print("-" * 74)
    for model in models:
        times = timings[model]
        fails = sum(1 for a in results[model] if not a.ok)
        print(f"{model[:38]:<38} {statistics.mean(times):8.1f}s "
              f"{statistics.median(times):8.1f}s {fails:7d}")

    if len(models) > 1:
        print("\nЗгода між моделями (де всі відповіли):")
        agree = 0
        counted = 0
        for index, image_path in enumerate(images):
            verdicts = [results[m][index] for m in models]
            if not all(v.ok for v in verdicts):
                continue
            counted += 1
            categories = {v.category for v in verdicts}
            if len(categories) == 1:
                agree += 1
            else:
                detail = ", ".join(
                    f"{m.split('/')[-1][:20]}={results[m][index].label}" for m in models
                )
                print(f"   ≠ {image_path.name[:40]:<40} {detail}")
        if counted:
            print(f"\n   збіг: {agree}/{counted} ({agree / counted * 100:.0f}%)")
        print("\nРозбіжності — найцікавіше: це саме ті кадри, де вибір моделі важить.")
        print("Дивись на них очима й бери ту, що частіше має рацію на твоєму контенті.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
