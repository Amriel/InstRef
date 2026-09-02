"""Витяг кадрів із відео, щоб модель бачила ролик, а не одну обкладинку.

Обкладинка reels — це перший кадр, який часто буває чорним, титром або лого.
Судити за ним про весь ролик — головне джерело помилок класифікації, тому
беремо кілька кадрів, рівномірно розкиданих по тривалості.

Кадри стискаються до розумного розміру: моделі не потрібен 4K, а кожен зайвий
піксель — це токени й час.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

MAX_SIDE = 640          # довша сторона кадру; більше моделі не дає користі
JPEG_QUALITY = 80
VIDEO_EXT = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}

# Кадр, темніший за це (середня яскравість 0..255), — затемнення чи чорний
# перехід; моделі з нього нічого, а місце в запиті займає.
DARK_LIMIT = 14
# Скільки семплів брати для пошуку змін сцени: досить, щоб побачити монтаж,
# і мало, щоб не декодувати весь ролик у повний розмір.
SCENE_SAMPLES = 240
SCENE_SIDE = 96


def frame_budget(duration: float, base: int, ceiling: int,
                 seconds_per_frame: float = 5.0) -> int:
    """Скільки кадрів брати з ролика такої тривалості.

    Одна й та сама кількість для 10-секундного reel і трихвилинного туторіалу
    не має сенсу: перший вона перевантажує, другий — не показує. Тому
    приблизно один кадр на seconds_per_frame, але не менше базового і не
    більше стелі.
    """
    base = max(1, int(base or 1))
    ceiling = max(base, int(ceiling or base))
    if not duration or duration <= 0 or seconds_per_frame <= 0:
        return base
    wanted = int(round(duration / seconds_per_frame))
    return max(base, min(ceiling, wanted))


def video_duration(path: Path) -> float:
    """Тривалість у секундах; 0.0, якщо не вдалось дізнатись."""
    try:
        import cv2
    except ImportError:
        return 0.0
    capture = None
    try:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            return 0.0
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        total = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        return total / fps if fps > 0 and total > 0 else 0.0
    except Exception:  # noqa: BLE001
        return 0.0
    finally:
        if capture is not None:
            capture.release()


def available() -> bool:
    """Чи є чим декодувати відео."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False
    return True


def side_for(count: int) -> int:
    """Розмір кадру під їхню кількість.

    Шістдесят кадрів по 640 px — це кілька мегабайтів base64 в одному запиті
    й десятки тисяч токенів контексту. Коли кадрів багато, важлива не різкість
    кожного, а те, що видно весь ролик, — тож зменшуємо сторону.
    """
    count = max(1, int(count or 1))
    if count <= 12:
        return MAX_SIDE
    if count <= 24:
        return 512
    if count <= 40:
        return 448
    return 384


def even_positions(total: int, count: int, skip_edges: bool = True) -> List[int]:
    """Рівномірно розкидані номери кадрів. Краї підрізаються: перші й останні
    відсотки — це зазвичай затемнення, титри або лого."""
    if total <= 0 or count <= 0:
        return []
    first, last = (0.06, 0.94) if skip_edges and total > 30 else (0.0, 1.0)
    if count == 1:
        return [int(total * 0.5)]
    step = (last - first) / (count - 1)
    return [max(0, min(total - 1, int(total * (first + step * i)))) for i in range(count)]


def _is_dark(cv2, frame) -> bool:
    try:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        return float(gray.mean()) < DARK_LIMIT
    except Exception:  # noqa: BLE001
        return False


def scene_positions(cv2, capture, total: int, count: int,
                    skip_edges: bool = True) -> List[int]:
    """Номери кадрів за зміною сцени, а не за рівними кроками.

    Ролик семплюється дрібними кадрами; різниця гістограм між сусідніми
    семплами показує монтажні склейки. Кадри беруться з середини найдовших
    сцен — там, де сцена «стоїть», а не в переході. Порожній список означає
    «не вийшло» — тоді викликач бере рівномірні позиції.
    """
    if total < 12 or count <= 0:
        return []
    import numpy as np

    step = max(1, total // SCENE_SAMPLES)
    lo, hi = (int(total * 0.04), int(total * 0.96)) if skip_edges and total > 30 else (0, total)
    hists = []
    positions = []
    dark = []
    index = 0
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    while True:
        ok = capture.grab()
        if not ok:
            break
        if index % step == 0 and lo <= index < hi:
            ok, frame = capture.retrieve()
            if not ok or frame is None:
                index += 1
                continue
            height, width = frame.shape[:2]
            scale = SCENE_SIDE / max(height, width, 1)
            small = cv2.resize(frame, (max(8, int(width * scale)), max(8, int(height * scale))),
                               interpolation=cv2.INTER_AREA)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            hists.append(hist.flatten())
            positions.append(index)
            dark.append(_is_dark(cv2, small))
        index += 1
        if index >= hi:
            break
    if len(positions) < 2:
        return []

    diffs = [0.0]
    for prev, cur in zip(hists, hists[1:]):
        diffs.append(float(np.abs(cur - prev).sum()))
    # Склейка — різниця помітно більша за типову (медіана + стійка сигма).
    arr = np.array(diffs)
    median = float(np.median(arr))
    mad = float(np.median(np.abs(arr - median))) or 1e-6
    threshold = median + 4.0 * mad
    cuts = [i for i, d in enumerate(diffs) if i > 0 and d > threshold and d > 0.3]

    # Сцени як відрізки між склейками, з відкиданням темних семплів.
    bounds = [0] + cuts + [len(positions)]
    scenes = []
    for start, end in zip(bounds, bounds[1:]):
        members = [i for i in range(start, end) if not dark[i]]
        if members:
            scenes.append(members)
    if not scenes:
        return []

    # Довгі сцени важливіші за миготіння; серед відібраних — хронологічно.
    chosen = sorted(scenes, key=len, reverse=True)[:count]
    chosen.sort(key=lambda m: m[0])
    picked = [positions[m[len(m) // 2]] for m in chosen]

    # Кадрів менше, ніж просили (сцен мало) — доливаємо з найдовших сцен,
    # рівномірно всередині кожної.
    while len(picked) < count:
        added = False
        for members in sorted(scenes, key=len, reverse=True):
            if len(picked) >= count:
                break
            if len(members) < 3:
                continue
            quarter = positions[members[len(members) // 4]]
            three = positions[members[(3 * len(members)) // 4]]
            for candidate in (quarter, three):
                if candidate not in picked and len(picked) < count:
                    picked.append(candidate)
                    added = True
        if not added:
            break
    return sorted(set(picked))


def extract(path: Path, count: int = 6, skip_edges: bool = True,
            max_side: int = 0, by_scene: bool = True) -> List[bytes]:
    """Дістає `count` кадрів із ролика.

    by_scene=True — за монтажними склейками (див. scene_positions), із
    відкиданням темних кадрів; інакше або якщо не вийшло — рівномірно.
    Повертає список JPEG-байтів; порожній список означає «не вдалось».
    """
    path = Path(path)
    if count <= 0 or not path.exists() or path.suffix.lower() not in VIDEO_EXT:
        return []
    side = max_side or side_for(count)
    try:
        import cv2
    except ImportError:
        return []

    capture = None
    frames: List[bytes] = []
    try:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            return []
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            return []

        positions: List[int] = []
        if by_scene and count > 1:
            try:
                positions = scene_positions(cv2, capture, total, count, skip_edges)
            except Exception:  # noqa: BLE001 — запасний шлях нижче
                positions = []
        if not positions:
            positions = even_positions(total, count, skip_edges)

        seen = set()
        for position in positions:
            position = max(0, min(total - 1, position))
            if position in seen:
                continue
            seen.add(position)
            capture.set(cv2.CAP_PROP_POS_FRAMES, position)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            if by_scene and len(positions) > 1 and _is_dark(cv2, frame):
                continue
            encoded = _encode(cv2, frame, side)
            if encoded:
                frames.append(encoded)
    except Exception:  # noqa: BLE001 — кадри не варті зламаної синхронізації
        return frames
    finally:
        if capture is not None:
            capture.release()
    return frames


def _encode(cv2, frame, max_side: int = MAX_SIDE) -> Optional[bytes]:
    max_side = max(64, int(max_side or MAX_SIDE))
    height, width = frame.shape[:2]
    longest = max(height, width)
    if longest > max_side:
        scale = max_side / longest
        frame = cv2.resize(
            frame, (max(1, int(width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
    return buffer.tobytes() if ok else None


IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}


def shots_from_file(path: Path, count: int = 6) -> List[bytes]:
    """Кадри з будь-якого файлу: з відео — кілька, з картинки — вона сама.

    Свідомо перевіряє тип: раніше відео, з якого не вдалось дістати кадри,
    йшло моделі як «image/jpeg» цілим mp4 — вона щось відповідала, і зрозуміти,
    що вона дивилась на сміття, було ніяк.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXT:
        return extract(path, count)
    if suffix not in IMAGE_EXT:
        return []
    try:
        return [shrink_image(path.read_bytes(), side_for(count))]
    except OSError:
        return []


def shrink_image(data: bytes, max_side: int = MAX_SIDE) -> bytes:
    """Стискає готове зображення (обкладинку чи слайд каруселі)."""
    if not data:
        return data
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return data
        return _encode(cv2, frame, max_side) or data
    except Exception:  # noqa: BLE001
        return data


# ------------------------------------------------------- перцептивний хеш
HASH_SIZE = 8


def dhash(data: bytes) -> Optional[int]:
    """dHash кадру: 64 біти «яскравіше за сусіда праворуч».

    Стійкий до перекодування, зміни розміру й легкої компресії — саме того,
    що робить репост тим самим роликом із іншим хешем файлу.
    """
    if not data:
        return None
    try:
        import cv2
        import numpy as np

        frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if frame is None:
            return None
        small = cv2.resize(frame, (HASH_SIZE + 1, HASH_SIZE), interpolation=cv2.INTER_AREA)
        diff = small[:, 1:] > small[:, :-1]
        value = 0
        for bit in diff.flatten():
            value = (value << 1) | int(bool(bit))
        return value
    except Exception:  # noqa: BLE001
        return None


def hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


def fingerprint(path: Path, samples: int = 3) -> List[int]:
    """Кілька хешів файлу: для відео — з кадрів, для картинки — один."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXT:
        shots = extract(path, samples, by_scene=False, max_side=160)
    elif suffix in IMAGE_EXT:
        try:
            shots = [path.read_bytes()]
        except OSError:
            return []
    else:
        return []
    result = []
    for shot in shots:
        value = dhash(shot)
        if value is not None:
            result.append(value)
    return result
