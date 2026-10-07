"""Спільні фікстури.

Реальна LM Studio на машині розробника не має бути учасником тестів: прогрів
моделі й запит `/api/v0/models` без заглушки йшли б у справжній сервер на
localhost:1234 (і вантажили б справжню модель). Тести, яким ці виклики потрібні
самі по собі, просять фікстуру `real_lm_calls` — вона знімає заглушки.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture
def real_lm_calls():
    """Маркер: цей тест сам дає фейковий сервер і хоче справжні warm_up/типи."""


@pytest.fixture(autouse=True)
def _no_real_lm_studio(monkeypatch, request):
    if "real_lm_calls" in request.fixturenames:
        return
    from igsaved import vision

    monkeypatch.setattr(vision.VisionClient, "warm_up",
                        lambda self, timeout=None: self.model)
    monkeypatch.setattr(vision.VisionClient, "_model_info",
                        lambda self, refresh=False: {})
