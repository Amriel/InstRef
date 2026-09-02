"""Голос за кадром → текст (опційно, faster-whisper).

Туторіали пояснюють техніку словами; кадри цього не передають. Транскрипт
іде в інструкцію моделі як контекст і в анотацію Eagle. Залежність важка,
тому не в requirements: без неї функція чесно каже, що недоступна.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

INSTALL_HINT = "pip install faster-whisper"
MAX_CHARS = 1500

_models: dict = {}


def available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def transcribe(path: Path, model_size: str = "small", language: Optional[str] = None,
               max_seconds: float = 240.0) -> str:
    """Текст мовлення з ролика; порожній рядок — нема мови або нема чим."""
    if not available():
        return ""
    path = Path(path)
    if not path.exists():
        return ""
    try:
        from faster_whisper import WhisperModel

        model = _models.get(model_size)
        if model is None:
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            _models[model_size] = model
        segments, _info = model.transcribe(
            str(path), language=language or None, vad_filter=True, beam_size=1,
        )
        parts = []
        for segment in segments:
            if segment.start > max_seconds:
                break
            text = " ".join(str(segment.text or "").split())
            if text:
                parts.append(text)
        return " ".join(parts)[:MAX_CHARS]
    except Exception:  # noqa: BLE001 — транскрипт не вартий зламаного опису
        return ""
