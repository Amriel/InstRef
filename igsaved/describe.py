"""Єдиний конвеєр опису: скільки кадрів → які кадри → модель → збереження.

Раніше це були три копії (`Engine._ask_vision`, `Engine._describe`,
`maintenance.describe_library`), і кожна по-своєму рахувала кадри, їхній розмір,
виклик моделі та збереження результату. Розбіжність між ними й була причиною
того, що однаковий пост описувався по-різному залежно від шляху, яким він
потрапив у модель.

Модуль не знає про Qt і про Instagram: тільки файл (або готові кадри) на вході
і вердикт на виході. Тому його однаково викликають синхронізація, обслуговування
й черга дозапису з диска.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional

from . import frames as framegrab
from . import taxonomy
from . import vision
from .taxonomy import profiles_for  # noqa: F401 — профілі за medium: той самий код для всіх викликачів
from .vision import VisionVerdict

# Пояснення для викликача, коли кадрів не вийшло дістати зовсім.
NO_FRAMES_ERROR = "нема з чого описати"

# Скільки токенів контексту з'їдають сама інструкція зі словником тегів, підпис,
# транскрипт і відповідь моделі. Виміряно за журналами LM Studio: словник
# разом з інструкцією — ≈2500, відповідь до 900 токенів.
PROMPT_OVERHEAD_TOKENS = 3500

MIN_SIDE_FOR_FIT = 512
SIDE_STEP = 128
MIN_COUNT_FOR_FIT = 3

# Назви категорій рішення — це не теги бібліотеки. Модель їх інколи кладе в
# список тегів («ad»), і словник бачив 197 «пропозицій» додати тег «ad».
CATEGORY_NAMES = frozenset(vision.CATEGORIES)


def tokens_per_frame(side: int) -> int:
    """Оцінка токенів одного кадру 16:9 для Qwen-VL: ділянки 32×32 px.

    Вертикальний кадр (9:16) має стільки ж ділянок, тож формула однакова для
    обох орієнтацій. 896 px → ≈440 токенів.
    """
    side = max(32, int(side or 0))
    return int(round((side / 32) * (side * 9 / 16 / 32)))


@dataclass
class FramePlan:
    count: int
    side: int
    # Що довелося змінити заради контексту; порожньо, якщо план як просили.
    note: str = ""

    @property
    def tokens(self) -> int:
        return self.count * tokens_per_frame(self.side)


def fit_plan(count: int, side: int, context_tokens: Optional[int] = None) -> FramePlan:
    """Вписує кадри в контекст завантаженої моделі.

    Спершу зменшуємо розмір (до 512 px) — це дешевше за втрату кадрів, — і лише
    тоді кількість (до 3). Невідомий контекст — нічого не міняємо.
    """
    count = max(1, int(count or 1))
    side = int(side or 896)
    ctx = int(context_tokens or 0)
    if ctx <= 0:
        return FramePlan(count, side)
    start = (count, side)
    while count * tokens_per_frame(side) + PROMPT_OVERHEAD_TOKENS > ctx:
        if side > MIN_SIDE_FOR_FIT:
            side = max(MIN_SIDE_FOR_FIT, side - SIDE_STEP)
        elif count > MIN_COUNT_FOR_FIT:
            count -= 1
        else:
            break
    note = ""
    if (count, side) != start:
        note = f"кадри зменшено до {count}×{side} px, щоб вписатись у контекст {ctx}"
    return FramePlan(count, side, note)


def ceiling(cfg) -> int:
    return max(1, min(vision.MAX_FRAMES, int(getattr(cfg, "vision_frames", 32) or 1)))


def frame_side(cfg) -> int:
    return max(64, int(getattr(cfg, "vision_frame_side", 896) or 896))


def plan_frames(source, cfg, context_tokens: Optional[int] = None) -> FramePlan:
    """Скільки кадрів і якого розміру брати з цього файлу.

    `source` — шлях до файлу (відео впізнається за розширенням, тривалість
    читається з нього), число (тривалість відео в секундах) або None (картинка).
    Тривалість визначає кількість: ≤10 с → 4, ≤30 с → 8, ≤60 с → 12, ≤120 с →
    20, довше → 32; невідома тривалість відео → 12. Стеля — `cfg.vision_frames`.
    Картинка й слайд — завжди один кадр. Довгий ролик має бути описаний ВЕСЬ, а
    не лише початок: при стороні 896 px модель читає й 32 кадри без деградації.
    """
    duration: Optional[float]
    if source is None:
        duration = None
    elif isinstance(source, (int, float)):
        duration = float(source)
    else:
        path = Path(source)
        duration = (framegrab.video_duration(path)
                    if path.suffix.lower() in framegrab.VIDEO_EXT else None)

    if duration is None:
        count = 1
    elif duration <= 0:
        count = 12         # тривалість невідома — середній варіант
    elif duration <= 10:
        count = 4
    elif duration <= 30:
        count = 8
    elif duration <= 60:
        count = 12
    elif duration <= 120:
        count = 20
    else:
        count = 32
    return fit_plan(min(count, ceiling(cfg)), frame_side(cfg), context_tokens)


def shots_for(path, plan: FramePlan, by_scene: bool = True) -> List[bytes]:
    """Кадри з файлу за планом: з відео — кілька, з картинки — вона сама."""
    path = Path(path)
    if path.suffix.lower() in framegrab.VIDEO_EXT:
        return framegrab.extract(path, plan.count, max_side=plan.side, by_scene=by_scene)
    return framegrab.shots_from_file(path, 1, plan.side)


def context_length(client) -> Optional[int]:
    """Контекст моделі, якщо клієнт уміє його сказати. Фейки й старі клієнти — ні."""
    getter = getattr(client, "context_length", None)
    if getter is None:
        return None
    try:
        return getter() or None
    except Exception:  # noqa: BLE001 — розмір кадру не варт зламаного проходу
        return None


@dataclass
class DescribeContext:
    """Усе, що модель має знати про пост, крім самих кадрів."""

    caption: str = ""
    username: str = ""
    kind: str = "post"
    mode: str = ""
    collections: Optional[List[str]] = None
    examples: Optional[List[str]] = None
    transcript: str = ""
    cfg: Any = None
    log: Optional[Callable[[str], None]] = None
    label: str = ""                    # як називати файл у журналі
    # Нотатки про вписування в контекст уже показані; спільна для проходу
    # множина — щоб той самий рядок не повторювався на кожному файлі.
    seen_notes: set = field(default_factory=set)


def note_once(ctx: DescribeContext, text: str) -> None:
    if not text or text in ctx.seen_notes:
        return
    ctx.seen_notes.add(text)
    if ctx.log:
        ctx.log(f"   ⤓ {text}")


def describe_file(client, path, ctx: DescribeContext) -> VisionVerdict:
    """План → кадри → модель. Помилки повертає вердиктом, а не кидає."""
    cfg = ctx.cfg
    if cfg is None:
        from .config import Config

        cfg = Config()
    plan = plan_frames(path, cfg, context_length(client))
    note_once(ctx, plan.note)
    shots = shots_for(path, plan, bool(getattr(cfg, "vision_frames_by_scene", True)))
    if not shots:
        return VisionVerdict(error=NO_FRAMES_ERROR)
    if ctx.log and ctx.label:
        # Один елемент — це кілька кадрів і запит до моделі на десятки секунд.
        # Без цього рядка журнал мовчить увесь цей час, і виглядає як зависання.
        ctx.log(f"   … {ctx.label[:44]}: {len(shots)} кадр(ів) по {plan.side} px, "
                "питаю модель…")
    verdict = run_model(
        client, shots, cfg, caption=ctx.caption, username=ctx.username, kind=ctx.kind,
        mode=ctx.mode or taxonomy.mode_for(Path(path).name),
        collections=ctx.collections, examples=ctx.examples,
        transcript=ctx.transcript,
    )
    verdict.transcript = ctx.transcript
    return verdict


def run_model(client, shots, cfg, **fields) -> VisionVerdict:
    """Один виклик моделі за політикою конфігу: опис + теги окремим запитом
    (`vision_two_pass`, типово) або все одним.

    Єдине місце вибору, щоб синхронізація, обслуговування й дозапис не
    розходились: інакше однаковий пост отримував би різні теги залежно від шляху.
    Клієнт без `describe_then_tag` (старі й чужі) працює однозапитно.
    """
    two_pass = bool(getattr(cfg, "vision_two_pass", True))
    if two_pass and hasattr(client, "describe_then_tag"):
        return client.describe_then_tag(shots, **fields)
    return client.classify(shots, **fields)


def note_dropped(state, verdict: VisionVerdict) -> None:
    """Рахує теги поза словником як кандидатів — без назв категорій рішення."""
    dropped = [tag for tag in (verdict.dropped or [])
               if str(tag).strip().lower() not in CATEGORY_NAMES]
    if dropped:
        state.note_tag_candidates(dropped)


def remember(state, pk: str, idx: int, verdict: VisionVerdict, model: str,
             prompt_hash: str, count_dropped: bool = True) -> bool:
    """Єдине місце, де вердикт потрапляє в базу. True — якщо щось збережено.

    Порожня відповідь не зберігається: порожній опис колись виглядав як «вже
    описано» і назавжди закривав повторну спробу. Відкинуті теги рахуються
    завжди, але без назв категорій: «ad» чи «art» — не пропозиція для словника.
    """
    if count_dropped:
        note_dropped(state, verdict)
    pk = str(pk or "")
    if not pk or not verdict.has_text:
        return False
    state.set_ai_meta(
        pk, verdict.category if verdict.ok else "", verdict.confidence,
        verdict.description, verdict.tags, model, verdict.frames, idx=idx,
        prompt_hash=prompt_hash, screen_text=verdict.on_screen_text,
        transcript=getattr(verdict, "transcript", ""),
        summary=getattr(verdict, "summary", ""),
    )
    return True


def log_rejected(log: Callable[[str], None], verdict: VisionVerdict,
                 prefix: str = "   ") -> None:
    """Рядки про вердикт, що не потрапляють в опис: профіль словника, те, чого
    не вдалось отримати, і теги, які модель сама спростувала власним доказом.

    Профіль і попередження живуть тут, бо цю функцію вже кличуть на кожному
    шляху (і коли опис збережено, і коли ні) — без нового виклику в кожному місці.
    """
    medium = getattr(verdict, "medium", "")
    profiles = getattr(verdict, "profiles", ())
    if profiles:
        log(f"{prefix}◫ профіль: {medium or 'невідомо'} → {', '.join(profiles)}")
    warning = getattr(verdict, "warning", "")
    if warning:
        log(f"{prefix}⤼ {warning}")
    if verdict.rejected:
        log(f"{prefix}⤬ без доказу: {', '.join(verdict.rejected)}")


def log_migration_notes(cfg, log: Callable[[str], None]) -> None:
    """Показує, що міграція конфігу змінила, — і лише раз за життя процесу."""
    notes = list(getattr(cfg, "migration_notes", None) or [])
    for note in notes:
        log(note)
    if notes:
        cfg.migration_notes.clear()


def merge_into_eagle_item(eagle, item: dict, verdict: VisionVerdict,
                          previous_ai_tags=()) -> None:
    """Дописує опис і теги моделі в елемент Eagle, не чіпаючи ручних тегів.

    Один шлях і для дозапису бібліотеки, і для черги з диска: власник міг
    додати свої теги й нотатку — їх не затираємо. `previous_ai_tags` — теги,
    які модель ставила раніше; їх при переописі прибираємо, щоб не копичились.
    Кидає EagleError.
    """
    from . import tagging
    from .maintenance import _strip_description

    old_ai = {str(t).lower() for t in previous_ai_tags}
    tags = [t for t in (item.get("tags") or []) if str(t).lower() not in old_ai] \
        + list(verdict.tags)
    seen, unique = set(), []
    for tag in tags:
        key = str(tag).strip().lower()
        if key and key not in seen:
            seen.add(key)
            unique.append(str(tag).strip())
    eagle.update_item(
        str(item.get("id")),
        tags=unique,
        annotation=tagging.annotation(
            _strip_description(str(item.get("annotation") or "")),
            verdict.description, verdict.on_screen_text,
            getattr(verdict, "transcript", ""),
            getattr(verdict, "summary", "")),
    )
