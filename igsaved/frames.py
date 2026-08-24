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


def extract(path: Path, count: int = 6, skip_edges: bool = True,
            max_side: int = 0) -> List[bytes]:
    """Дістає `count` кадрів, рівномірно розподілених по ролику.

    Краї підрізаються: перші й останні відсотки — це зазвичай затемнення,
    титри або лого, і вони лише збивають модель.
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

        first, last = (0.06, 0.94) if skip_edges and total > 30 else (0.0, 1.0)
        if count == 1:
            positions = [int(total * 0.5)]
        else:
            step = (last - first) / (count - 1)
            positions = [int(total * (first + step * i)) for i in range(count)]

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
