"""Приведення вже завантаженої бібліотеки до поточного шаблону імен і тегів.

Потрібно, коли шаблон змінили після того, як щось уже скачано: файли з іменами
на кшталт `2026-06-09_studioname_DZXi35noMC8.mp4` перейменовуються за підписом
поста, а всередину дописуються опис і автор.

Джерело підписів — локальна база; якщо файлу в базі нема, шукаємо сусідній
`_metadata/<ім'я>.json`, який писали попередні версії.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import tagging
from .config import Config
from .naming import (
    asset_name, caption_slug, hashtags, render_template, safe_component,
    short_title, unique_path,
)
from .state import State
from .tagging import MediaTags

MEDIA_EXT = {".mp4", ".m4v", ".mov", ".jpg", ".jpeg", ".png", ".webp"}


@dataclass
class RefreshStats:
    seen: int = 0
    renamed: int = 0
    tagged: int = 0
    skipped: int = 0
    failed: int = 0
    errors: List[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    def summary(self) -> str:
        return (
            f"переглянуто {self.seen}, перейменовано {self.renamed}, "
            f"теги записано {self.tagged}, без змін {self.skipped}, помилок {self.failed}"
        )


@dataclass
class _Post:
    """Все, що треба знати про пост, щоб зібрати ім'я й теги."""

    pk: str
    code: str = ""
    username: str = ""
    full_name: str = ""
    caption: str = ""
    taken_at: Optional[datetime] = None
    kind: str = "post"
    collections: List[str] = None
    files: List[Path] = None
    description: str = ""       # від візуальної моделі — про пост загалом
    ai_tags: List[str] = None
    # Номер слайда для кожного файлу і опис саме для нього: в каруселі
    # картинки різні, і спільний текст був би неправдою про кожну з них.
    indices: List[int] = None
    ai_by_idx: Dict[int, dict] = None
    # pk підбірок, а не назви: саме за pk синхронізація позначає імпорт у Eagle,
    # і плутанина між ними давала другу копію того самого файлу.
    collection_pks: List[str] = None

    def __post_init__(self):
        self.collections = self.collections or []
        self.files = self.files or []
        self.ai_tags = self.ai_tags or []
        self.indices = self.indices or []
        self.ai_by_idx = self.ai_by_idx or {}
        self.collection_pks = self.collection_pks or []

    def parts(self):
        """(файл, номер слайда) — щоб опис не поїхав у сусідній файл."""
        for position, path in enumerate(self.files):
            yield path, (self.indices[position] if position < len(self.indices) else 0)

    def meta_for(self, idx: int) -> dict:
        return self.ai_by_idx.get(int(idx or 0)) or self.ai_by_idx.get(0) or {}


def refresh_library(
    cfg: Config,
    state: State,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
    rename: bool = True,
    retag: bool = True,
) -> RefreshStats:
    stats = RefreshStats()
    posts = _from_state(state)
    known_paths = {str(path) for post in posts.values() for path in post.files}
    posts.update(_from_sidecars(cfg, known_paths))

    if not posts:
        log("Нема чого оновлювати: ні в базі, ні в _metadata нічого не знайдено.")
        return stats

    log(f"Знайдено постів для перевірки: {len(posts)}")
    for post in posts.values():
        if should_stop():
            log("Зупинено.")
            break
        _refresh_post(cfg, state, post, stats, log, rename, retag)

    log(f"Готово: {stats.summary()}")
    return stats


# ------------------------------------------------------------------ джерела
def _from_state(state: State) -> Dict[str, _Post]:
    posts: Dict[str, _Post] = {}
    try:
        rows = state.db.execute(
            """
            SELECT f.path, f.kind, f.idx, m.pk, m.code, m.username, m.taken_at,
                   m.caption, m.product_type, m.media_type
            FROM files f JOIN media m ON m.pk = f.media_pk
            WHERE f.kind IN ('video', 'photo')
            ORDER BY m.pk, f.idx
            """
        ).fetchall()
    except Exception:  # noqa: BLE001 — стара або пошкоджена база не має все ламати
        return posts

    try:
        ai_all = state.all_ai_meta()
    except Exception:  # noqa: BLE001 — база зі старою схемою
        ai_all = {}

    for row in rows:
        path = Path(row["path"])
        if not path.exists():
            continue
        pk = str(row["pk"])
        post = posts.get(pk)
        if post is None:
            ai = ai_all.get(pk, {})
            post = _Post(
                pk=pk,
                code=row["code"] or "",
                username=row["username"] or "",
                caption=row["caption"] or "",
                taken_at=_parse_date(row["taken_at"]),
                kind=_kind(row["media_type"], row["product_type"]),
                collections=state.collection_names_for(pk),
                collection_pks=state.collection_pks_for(pk),
                description=ai.get("description", ""),
                ai_tags=list(ai.get("tags", [])),
                ai_by_idx={
                    key[1]: value for key, value in ai_all.items()
                    if isinstance(key, tuple) and key[0] == pk
                },
            )
            posts[pk] = post
        post.files.append(path)
        post.indices.append(int(row["idx"] or 0))
    return posts


def _from_sidecars(cfg: Config, known: set) -> Dict[str, _Post]:
    """Підбирає файли, яких нема в базі, але поруч лежить .json від старих версій."""
    posts: Dict[str, _Post] = {}
    meta_dir = cfg.meta_dir
    if not meta_dir.exists():
        return posts

    for sidecar in sorted(meta_dir.glob("*.json")):
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        stem = sidecar.stem
        files = [
            path for path in sorted(cfg.root.glob(f"{_escape(stem)}*"))
            if path.is_file()
            and path.suffix.lower() in MEDIA_EXT
            and str(path) not in known
        ]
        if not files:
            continue

        author = data.get("author") or {}
        posts[f"sidecar:{stem}"] = _Post(
            pk=str(data.get("pk") or stem),
            code=data.get("code") or "",
            username=author.get("username") or "",
            full_name=author.get("full_name") or "",
            caption=data.get("caption") or "",
            taken_at=_parse_date(data.get("taken_at")),
            kind=_kind(data.get("media_type"), data.get("product_type")),
            collections=list(data.get("collections") or []),
            files=files,
        )
    return posts


# ------------------------------------------------------------------- робота
def _refresh_post(cfg, state, post: _Post, stats: RefreshStats,
                  log, rename: bool, retag: bool) -> None:
    collection = post.collections[0] if post.collections else ""
    base = render_template(
        cfg.filename_template,
        post.taken_at, post.username, post.code,
        kind=post.kind, media_id=post.pk, collection=collection,
        caption=post.caption,
    )
    tags = MediaTags(
        title=caption_slug(post.caption) or post.code,
        author=post.username,
        author_full=post.full_name,
        caption=post.caption,
        url=f"https://www.instagram.com/p/{post.code}/" if post.code else "",
        taken_at=post.taken_at,
        kind=post.kind,
        collections=post.collections,
        hashtags=hashtags(post.caption),
        description=post.description,
        ai_tags=post.ai_tags,
    )

    multi = len(post.files) > 1
    for position, (path, idx) in enumerate(post.parts(), start=1):
        stats.seen += 1
        current = path
        slide = post.meta_for(idx)
        tags.description = slide.get("description", post.description)
        tags.ai_tags = list(slide.get("tags", post.ai_tags))
        try:
            if rename:
                target = path.with_name(
                    asset_name(base, position if multi else None, path.suffix)
                )
                if target != path:
                    if target.exists():
                        target = unique_path(target)
                    path.rename(target)
                    _remember(state, str(path), str(target))
                    stats.renamed += 1
                    log(f"   ↻ {path.name}  →  {target.name}")
                    current = target
                else:
                    stats.skipped += 1
            if retag:
                ok, problem = tagging.apply(current, tags)
                if ok:
                    stats.tagged += 1
                elif problem:
                    log(f"   ⤼ {current.name}: {problem}")
        except OSError as exc:
            stats.failed += 1
            message = f"{path.name}: {exc}"
            stats.errors.append(message)
            log(f"   ✖ {message}")

    if rename:
        _move_companions(cfg, post, base, log)


def _move_companions(cfg: Config, post: _Post, base: str, log) -> None:
    """Тягне за перейменованим файлом його прев'ю та .json, щоб не розсинхронити."""
    for folder, suffix in ((cfg.thumbs_dir, ".jpg"), (cfg.meta_dir, ".json")):
        if not folder.exists():
            continue
        for original in list(post.files):
            candidate = folder / f"{original.stem}{suffix}"
            target = folder / f"{base}{suffix}"
            if not candidate.exists() or target == candidate:
                continue
            try:
                if target.exists():
                    target = unique_path(target)
                candidate.rename(target)
            except OSError as exc:
                log(f"   ⤼ {candidate.name}: {exc}")


# ------------------------------------------------- дозалив бібліотеки в Eagle
@dataclass
class PushStats:
    sent: int = 0
    already: int = 0
    missing: int = 0
    failed: int = 0
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        return (
            f"надіслано {self.sent}, вже було {self.already}, "
            f"файлів нема на диску {self.missing}, помилок {self.failed}"
        )


def push_to_eagle(
    cfg: Config,
    state: State,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
) -> PushStats:
    """Заливає в Eagle те, що вже лежить на диску.

    Потрібно, коли імпорт увімкнули пізніше: звичайна синхронізація в режимі
    «лише нове» зупиняється на перших же відомих постах і до старих не доходить.
    """
    from .eagle import EagleClient, EagleError, EagleItem

    stats = PushStats()
    client = EagleClient(cfg.eagle_url, cfg.eagle_token)
    try:
        info = client.ping()
    except EagleError as exc:
        stats.error = f"Eagle недоступний: {exc}"
        log(stats.error)
        return stats
    version = info.get("version", "") if isinstance(info, dict) else ""
    log("Eagle на звʼязку" + (f" (v{version})" if version else "") + ".")

    try:
        root = client.ensure_folder(cfg.eagle_root_folder)
    except EagleError as exc:
        stats.error = f"Не вдалось створити кореневу папку: {exc}"
        log(stats.error)
        return stats

    posts = _from_state(state)
    if not posts:
        log("У базі нема завантажених постів — нема чого заливати.")
        return stats

    log(f"Постів у базі: {len(posts)}")
    batch: Dict[Optional[str], List[EagleItem]] = {}
    marks: List[tuple] = []

    def flush() -> None:
        for folder_id, items in batch.items():
            if not items:
                continue
            try:
                stats.sent += client.add_items(items, folder_id)
            except EagleError as exc:
                stats.failed += len(items)
                log(f"   ✖ Eagle: {exc}")
        batch.clear()
        for pk, col_pk, folder_id in marks:
            state.mark_in_eagle(pk, col_pk, folder_id or "")
        marks.clear()

    for post in posts.values():
        if should_stop():
            log("Зупинено.")
            break
        if state.is_pending_review(post.pk):
            continue          # чекає на рішення — у бібліотеку ще зарано
        collection = post.collections[0] if post.collections else ""
        col_pk = post.collection_pks[0] if post.collection_pks else "root"
        if _in_eagle(cfg, state, post.pk, col_pk):
            stats.already += 1
            continue

        folder_id = root
        if cfg.eagle_folder_per_collection and collection:
            try:
                folder_id = client.ensure_folder(safe_component(collection, 60), root)
            except EagleError:
                folder_id = root

        added = 0
        for path, idx in post.parts():
            if not path.exists():
                stats.missing += 1
                continue
            batch.setdefault(folder_id, []).append(
                _eagle_item(cfg, post, collection, path, idx))
            added += 1
        if added:
            marks.append((post.pk, col_pk, folder_id))
        if sum(len(v) for v in batch.values()) >= 40:
            flush()

    flush()
    log(f"Готово: {stats.summary()}")
    return stats


# ------------------------------------------------- чистка папки завантажень
@dataclass
class CleanupStats:
    posts: int = 0
    files: int = 0
    bytes: int = 0
    missing: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (
            f"видалено {self.files} файл(ів) на {_human(self.bytes)}, "
            f"постів у памʼяті лишилось {self.posts}"
            + (f", не знайдено {self.missing}" if self.missing else "")
            + (f", помилок {self.failed}" if self.failed else "")
        )


def _human(num: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if abs(num) < 1024 or unit == "ГБ":
            return f"{num:,.0f} {unit}" if unit == "Б" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} ГБ"


def downloads_summary(state: State) -> tuple[int, int]:
    """Скільки файлів застосунку реально лежить на диску і скільки вони важать."""
    count = 0
    size = 0
    for row in state.tracked_files():
        path = Path(row["path"])
        if path.exists():
            count += 1
            try:
                size += path.stat().st_size
            except OSError:
                size += int(row["bytes"] or 0)
    return count, size


def clear_downloads(
    cfg: Config,
    state: State,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
) -> CleanupStats:
    """Видаляє завантажені файли, зберігаючи памʼять про те, що вже качалось.

    Свідомо чіпає ЛИШЕ те, що створив сам застосунок (записане в базі плюс
    власні підпапки). Чужі файли в тій самій теці лишаються недоторканими.
    Eagle не зачіпається взагалі.
    """
    stats = CleanupStats()
    touched: set = set()

    for row in state.tracked_files():
        if should_stop():
            log("Зупинено.")
            break
        path = Path(row["path"])
        touched.add(str(row["media_pk"]))
        if not path.exists():
            stats.missing += 1
            continue
        try:
            size = path.stat().st_size
            path.unlink()
            stats.files += 1
            stats.bytes += size
        except OSError as exc:
            stats.failed += 1
            log(f"   ✖ {path.name}: {exc}")

    # .json поруч — теж наші, але в базі їх немає
    if cfg.meta_dir.exists():
        for sidecar in cfg.meta_dir.glob("*.json"):
            try:
                sidecar.unlink()
                stats.files += 1
            except OSError:
                stats.failed += 1

    # Головне: пости лишаються в памʼяті як завантажені, інакше наступна
    # синхронізація почала б качати все з нуля.
    for pk in touched:
        state.mark_archived(pk)
    stats.posts = len(touched)

    # Тимчасові ролики, які модель не встигла подивитись, — теж наші.
    if cfg.cache_dir.exists():
        for leftover in cfg.cache_dir.glob("*"):
            try:
                leftover.unlink()
                stats.files += 1
            except OSError:
                stats.failed += 1

    for folder in (cfg.thumbs_dir, cfg.meta_dir, cfg.review_dir, cfg.cache_dir):
        try:
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()
        except OSError:
            pass

    log(f"Готово: {stats.summary()}")
    return stats


# ------------------------------------------- описи для вже зібраної бібліотеки
@dataclass
class DescribeStats:
    seen: int = 0
    described: int = 0
    already: int = 0
    missing: int = 0        # файл елемента не знайшовся на диску
    empty: int = 0          # модель відповіла, але нічого не написала
    failed: int = 0
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        return (
            f"переглянуто {self.seen}, описано {self.described}, "
            f"вже мали опис {self.already}"
            + (f", без файлу {self.missing}" if self.missing else "")
            + (f", модель промовчала {self.empty}" if self.empty else "")
            + (f", помилок {self.failed}" if self.failed else "")
        )


# За цим рядком упізнаємо власну роботу в нотатці Eagle: своїх позначок
# бібліотека не зберігає, а переписувати вже описане — марно палити години.
DESCRIPTION_MARK = "Visual summary:"
# Старий український заголовок теж вважаємо «вже описано» — інакше повторний
# прохід переписав би те, що вже зроблено, і згаяв би години.
DESCRIPTION_MARKS = (DESCRIPTION_MARK, "Опис:")


def describe_library(
    cfg: Config,
    state: State,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
    limit: int = 0,
    redo: bool = False,
    only_stale: bool = False,
    model_override: str = "",
) -> DescribeStats:
    """Дописує опис і теги до того, що вже лежить у Eagle.

    Потрібно, бо звичайна синхронізація описує лише НОВІ пости, а бібліотека
    здебільшого зібрана раніше — та ще й після чистки папки завантажень самі
    файли лишились тільки всередині Eagle. Тому й джерелом кадрів тут виступає
    бібліотека, а не диск.

    redo — переписати й уже описане; only_stale — переписати лише те, що
    описано іншою інструкцією чи моделлю (за prompt_hash у базі);
    model_override — інша модель LM Studio, ніж у налаштуваннях (переопис
    сильнішою моделлю).
    """
    from . import frames as framegrab
    from . import taxonomy
    from . import vision
    from .eagle import EagleClient, EagleError

    stats = DescribeStats()
    if not cfg.vision_enabled:
        stats.error = "Візуальна модель вимкнена в налаштуваннях."
        log(stats.error)
        return stats

    client = vision.client_for(cfg)
    if model_override:
        client.model = model_override.strip()
    try:
        model = client.resolve_model()
    except vision.VisionError as exc:
        stats.error = f"Модель недоступна: {exc}"
        log(stats.error)
        return stats
    log(f"Модель: {model}")

    eagle = EagleClient(cfg.eagle_url, cfg.eagle_token)
    try:
        eagle.ping()
        library = eagle.library_path()
    except EagleError as exc:
        stats.error = f"Eagle недоступний: {exc}"
        log(stats.error)
        return stats
    if not library:
        stats.error = "Eagle не сказав, де лежить бібліотека."
        log(stats.error)
        return stats

    folders = _our_folder_ids(eagle, cfg, log)
    try:
        if folders and not eagle.list_items(folders, limit=1):
            # Фільтр за теками в різних збірках Eagle поводиться по-різному.
            # Порожня відповідь тут — привід не зупинятись, а глянути ширше.
            log("У теці застосунку Eagle нічого не показує — дивлюсь усю бібліотеку.")
            folders = None
    except EagleError:
        folders = None

    base = max(1, min(vision.MAX_FRAMES, int(cfg.vision_frames or 1)))
    stems = state.files_by_stem()
    urls = state.media_by_url()
    current_hash = vision.prompt_hash(cfg.vision_prompt, model)
    examples = state.exemplars()
    missing_ids = set(state.without_eagle_item_id())
    skip_pks = _pks_in_collections(state, cfg.describe_skip_collections)
    per = float(cfg.vision_seconds_per_frame or 0)

    log("Читаю бібліотеку Eagle…")
    for item in eagle.iter_items(folders):
        if should_stop():
            log("Зупинено.")
            break
        if limit and stats.described >= limit:
            log(f"Ліміт {limit} — на цьому спиняюсь.")
            break
        stats.seen += 1

        name = str(item.get("name") or "")
        known = stems.get(name) or _by_url(urls, item.get("url"))
        item_id = str(item.get("id") or "")
        if known and item_id and known[0] in missing_ids:
            state.set_eagle_item_id(known[0], item_id)
            missing_ids.discard(known[0])

        annotation = str(item.get("annotation") or "")
        described_before = any(mark in annotation for mark in DESCRIPTION_MARKS)
        if described_before and not redo:
            stale = False
            if only_stale and known:
                meta = state.ai_meta(known[0], known[1]) or {}
                stale = bool(meta) and meta.get("prompt_hash", "") != current_hash
            if not stale:
                stats.already += 1
                continue
        if known and known[0] in skip_pks:
            stats.already += 1
            continue

        path = eagle.item_file(item, library)
        if not path:
            stats.missing += 1
            continue

        want = base
        if Path(path).suffix.lower() in framegrab.VIDEO_EXT:
            if per > 0:
                want = framegrab.frame_budget(framegrab.video_duration(Path(path)),
                                              base, vision.MAX_FRAMES, per)
            shots = framegrab.extract(Path(path), want, by_scene=cfg.vision_frames_by_scene)
        else:
            shots = framegrab.shots_from_file(Path(path), want)
        if not shots:
            stats.missing += 1
            continue

        # Відбитки для пошуку репостів — заодно, поки файл під рукою.
        if known and not state.has_fingerprints(known[0]):
            prints = framegrab.fingerprint(Path(path))
            if prints:
                state.set_fingerprints(known[0], known[1], prints)

        name = name or Path(path).stem
        mode = taxonomy.mode_for(path)
        answer = client.classify(shots, caption=_strip_description(annotation)[:400],
                                 kind="reel" if mode == taxonomy.VIDEO else "photo",
                                 mode=mode,
                                 collections=state.collection_names_for(known[0]) if known else None,
                                 examples=examples)
        if answer.dropped:
            state.note_tag_candidates(answer.dropped)
        if answer.error and not answer.has_text:
            stats.failed += 1
            log(f"   ✖ {name[:50]}: {answer.error}")
            continue
        if not answer.has_text:
            stats.empty += 1
            log(f"   ⤼ {name[:50]}: модель нічого не написала")
            continue

        old_ai = set()
        if known and (redo or only_stale):
            # Переопис: старі теги моделі прибираємо, ручні теги власника лишаються.
            previous = state.ai_meta(known[0], known[1]) or {}
            old_ai = {str(t).lower() for t in previous.get("tags", [])}
        tags = [t for t in (item.get("tags") or []) if str(t).lower() not in old_ai] \
            + list(answer.tags)
        seen_tags, unique = set(), []
        for tag in tags:
            key = str(tag).strip().lower()
            if key and key not in seen_tags:
                seen_tags.add(key)
                unique.append(str(tag).strip())
        try:
            eagle.update_item(
                str(item.get("id")),
                tags=unique,
                annotation=tagging.annotation(
                    _strip_description(annotation), answer.description,
                    answer.on_screen_text),
            )
        except EagleError as exc:
            stats.failed += 1
            log(f"   ✖ {name[:50]}: Eagle не прийняв — {exc}")
            continue

        stats.described += 1
        log(f"   ✎ {name[:44]}: {answer.short_description(90)}")

        # Якщо пост відомий базі — лишаємо опис і в себе, щоб він потрапляв
        # у майбутні файли й не питався вдруге.
        if known:
            pk, idx = known
            state.set_ai_meta(pk, answer.category if answer.ok else "",
                              answer.confidence, answer.description, answer.tags,
                              model, answer.frames, idx=idx, prompt_hash=current_hash,
                              screen_text=answer.on_screen_text)

    log(f"Готово: {stats.summary()}")
    return stats


def _our_folder_ids(eagle, cfg: Config, log) -> Optional[List[str]]:
    """Тека застосунку в Eagle разом із підпапками підбірок.

    Якщо її немає — повертаємо None і йдемо по всій бібліотеці: краще
    переглянути зайве, ніж мовчки не знайти нічого.
    """
    from .eagle import EagleError

    try:
        root = eagle.find_folder(cfg.eagle_root_folder)
    except EagleError:
        root = None
    if not root:
        log(f"Теки «{cfg.eagle_root_folder}» у Eagle немає — дивлюсь усю бібліотеку.")
        return None

    ids = [root]
    try:
        for folder in eagle._walk(eagle.list_folders()):  # noqa: SLF001
            if str(folder.get("id")) != str(root):
                continue
            for child in folder.get("children") or folder.get("folders") or []:
                ids.append(str(child.get("id")))
    except EagleError:
        pass
    return ids


# ------------------------------------------------- дублікати в бібліотеці Eagle
@dataclass
class DupeStats:
    scanned: int = 0
    groups: int = 0         # постів, що потрапили в бібліотеку більше разу
    extra: int = 0          # зайвих копій
    removed: int = 0
    failed: int = 0
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        if not self.groups:
            return f"переглянуто {self.scanned} — дублікатів немає"
        text = (f"переглянуто {self.scanned}, постів із копіями {self.groups}, "
                f"зайвих файлів {self.extra}")
        if self.removed:
            text += f", у кошик відправлено {self.removed}"
        if self.failed:
            text += f", помилок {self.failed}"
        return text


def find_eagle_duplicates(
    cfg: Config,
    state: State,
    log: Callable[[str], None] = print,
    remove: bool = False,
    should_stop: Callable[[], bool] = lambda: False,
) -> DupeStats:
    """Шукає в Eagle кілька елементів на один пост Instagram.

    Такі копії з'являлись, коли один пост лежить і в збережених, і в лайках:
    Eagle на кожен імпорт КОПІЮЄ файл, тож виходив не один елемент на двох
    полицях, а два окремі. Впізнаємо їх за адресою поста — вона однакова.

    Нічого не видаляє без прямої вказівки, і навіть тоді лише переносить у
    кошик Eagle, звідки все можна дістати назад.
    """
    from .eagle import EagleClient, EagleError

    stats = DupeStats()
    client = EagleClient(cfg.eagle_url, cfg.eagle_token)
    try:
        client.ping()
    except EagleError as exc:
        stats.error = f"Eagle недоступний: {exc}"
        log(stats.error)
        return stats

    folders = _our_folder_ids(client, cfg, log)
    by_url: Dict[str, List[dict]] = {}
    try:
        for item in client.iter_items(folders):
            if should_stop():
                log("Зупинено.")
                break
            stats.scanned += 1
            url = str(item.get("url") or "").rstrip("/")
            if url:
                by_url.setdefault(url, []).append(item)
    except EagleError as exc:
        stats.error = f"Eagle: {exc}"
        log(stats.error)
        return stats

    doomed: List[str] = []
    for url, items in by_url.items():
        if len(items) < 2:
            continue
        # Один пост може мати кілька РІЗНИХ файлів — карусель. Дублікатами
        # вважаємо лише однакові імена: саме їх створює повторний імпорт.
        by_name: Dict[str, List[dict]] = {}
        for item in items:
            by_name.setdefault(str(item.get("name") or ""), []).append(item)
        for name, same in by_name.items():
            if len(same) < 2:
                continue
            stats.groups += 1
            stats.extra += len(same) - 1
            # найстарший лишається: у нього більше шансів мати твої правки
            same.sort(key=lambda i: int(i.get("modificationTime") or 0))
            doomed.extend(str(i.get("id")) for i in same[1:])
            log(f"   ×{len(same)} {name[:60]}")

    if remove and doomed:
        for chunk in (doomed[i:i + 50] for i in range(0, len(doomed), 50)):
            try:
                stats.removed += client.trash_items(chunk)
            except EagleError as exc:
                stats.failed += len(chunk)
                log(f"   ✖ Eagle: {exc}")

    log(f"Готово: {stats.summary()}")
    return stats


def _in_eagle(cfg: Config, state: State, pk: str, collection_pk: str) -> bool:
    """Та сама перевірка, що й у синхронізації — інакше кожна з них імпортує
    той самий пост окремо, і в бібліотеці з'являється дубль."""
    if cfg.eagle_one_item_per_post:
        return state.is_in_eagle(pk)
    return state.is_in_eagle(pk, collection_pk)


def _by_url(urls: Dict[str, str], url) -> Optional[tuple]:
    pk = urls.get(str(url or "").rstrip("/"))
    return (pk, 0) if pk else None


def _strip_description(annotation: str) -> str:
    """Прибирає наш попередній опис (і текст з екрана), щоб повторний прохід
    не наростив хвіст."""
    for candidate in (*DESCRIPTION_MARKS, tagging.SCREEN_LABEL):
        head, mark, _ = annotation.partition(f"\n\n{candidate}")
        if mark:
            annotation = head
    return annotation


def _pks_in_collections(state: State, collection_pks) -> set:
    """Пости з підбірок, для яких описи не потрібні."""
    wanted = {str(pk) for pk in (collection_pks or []) if str(pk)}
    if not wanted:
        return set()
    with state._lock:  # noqa: SLF001
        rows = state.db.execute(
            f"SELECT DISTINCT media_pk FROM membership WHERE collection_pk IN "
            f"({','.join('?' for _ in wanted)})", tuple(wanted),
        ).fetchall()
    return {str(r["media_pk"]) for r in rows}


# ------------------------------------------- ретро-нормалізація тегів
@dataclass
class NormalizeStats:
    rows: int = 0
    changed: int = 0
    eagle_updated: int = 0
    eagle_missing: int = 0
    failed: int = 0
    error: str = ""

    def summary(self) -> str:
        if self.error:
            return self.error
        return (
            f"записів {self.rows}, змінено {self.changed}, "
            f"оновлено в Eagle {self.eagle_updated}"
            + (f", не знайдено в Eagle {self.eagle_missing}" if self.eagle_missing else "")
            + (f", помилок {self.failed}" if self.failed else "")
        )


def normalize_library(
    cfg: Config,
    state: State,
    log: Callable[[str], None] = print,
    should_stop: Callable[[], bool] = lambda: False,
    update_eagle: bool = True,
) -> NormalizeStats:
    """Проганяє збережені теги моделі через поточний словник і виправляє
    вже імпортовані елементи Eagle — без жодного запиту до моделі.

    Перші описи зроблено до появи словника: їхні теги («cinematic», «render»,
    «3d») уже в бібліотеці й нічого не знаходять. Словник із того часу виріс
    синонімами — і застосувати його заднім числом дешевше, ніж переописувати.
    """
    from . import taxonomy as taxonomy_mod
    from .eagle import EagleClient, EagleError

    stats = NormalizeStats()
    tax = taxonomy_mod.Taxonomy.load()
    changes: Dict[tuple, tuple] = {}     # (pk, idx) → (старі, нові)

    for row in state.ai_meta_rows():
        if should_stop():
            break
        stats.rows += 1
        old = [t for t in (row["tags"] or "").split("\n") if t]
        if not old:
            continue
        mode = taxonomy_mod.VIDEO
        pk, idx = str(row["media_pk"]), int(row["idx"] or 0)
        files = state.media_files(pk)
        if files:
            target = files[min(max(idx - 1, 0), len(files) - 1)] if idx else files[0]
            mode = taxonomy_mod.mode_for(target)
        new, _dropped = tax.normalize(old, mode)
        if [t.lower() for t in old] == [t.lower() for t in new]:
            continue
        stats.changed += 1
        state.update_ai_tags(pk, idx, new)
        changes[(pk, idx)] = (old, new)
        log(f"   {pk}/{idx}: {len(old)} → {len(new)} тег(ів)")

    if not update_eagle or not changes:
        log(f"Готово: {stats.summary()}")
        return stats

    client = EagleClient(cfg.eagle_url, cfg.eagle_token)
    try:
        client.ping()
    except EagleError as exc:
        stats.error = f"Теги в базі виправлено, але Eagle недоступний: {exc}"
        log(stats.error)
        return stats

    # Елементи Eagle знаходимо за збереженим id, а без нього — за адресою поста.
    ids = state.eagle_item_ids()
    need_lookup = {pk for (pk, _idx) in changes if pk not in ids}
    by_url: Dict[str, List[dict]] = {}
    if need_lookup:
        urls = state.media_by_url()
        wanted_urls = {url for url, pk in urls.items() if pk in need_lookup}
        try:
            for item in client.iter_items(_our_folder_ids(client, cfg, log)):
                url = str(item.get("url") or "").rstrip("/")
                if url in wanted_urls:
                    by_url.setdefault(url, []).append(item)
        except EagleError as exc:
            stats.error = f"Eagle не віддав список: {exc}"
            log(stats.error)
            return stats
    urls_by_pk = {pk: url for url, pk in state.media_by_url().items()}

    for (pk, idx), (old, new) in changes.items():
        if should_stop():
            break
        items: List[dict] = []
        if pk in ids:
            try:
                fetched = client.get_item(ids[pk])
            except EagleError:
                fetched = None
            if fetched:
                items = [fetched]
        if not items:
            items = by_url.get(urls_by_pk.get(pk, ""), [])
        if not items:
            stats.eagle_missing += 1
            continue
        drop = {t.lower() for t in old}
        for item in items:
            current = [t for t in (item.get("tags") or []) if str(t).lower() not in drop]
            merged, seen = [], set()
            for tag in current + list(new):
                key = str(tag).lower()
                if key and key not in seen:
                    seen.add(key)
                    merged.append(str(tag))
            try:
                client.update_item(str(item.get("id")), tags=merged)
                stats.eagle_updated += 1
            except EagleError as exc:
                stats.failed += 1
                log(f"   ✖ {pk}: Eagle не прийняв — {exc}")

    log(f"Готово: {stats.summary()}")
    return stats


# ------------------------------------------------------ звіт про словник
def vocabulary_report(state: State, top: int = 15) -> str:
    """Які теги перевикористані (>40% описів), а які мертві (0 ужитків)."""
    from . import taxonomy as taxonomy_mod

    tax = taxonomy_mod.Taxonomy.load()
    rows = state.ai_meta_rows()
    total = 0
    counts: Dict[str, int] = {}
    for row in rows:
        tags = [t for t in (row["tags"] or "").split("\n") if t and t != taxonomy_mod.MARKER]
        if not tags:
            continue
        total += 1
        for tag in set(tags):
            counts[tag] = counts.get(tag, 0) + 1
    if not total:
        return "Описів із тегами ще немає — звітувати нема про що."
    lines = [f"Описів із тегами: {total}. Тегів у словнику: {len(set(tax.all_tags()))}.", ""]
    overused = [(t, c) for t, c in counts.items() if c / total > 0.4]
    overused.sort(key=lambda x: -x[1])
    lines.append("Перевикористані (у понад 40% описів — такі нічого не знаходять):")
    lines.extend(f"   {t}: {c} ({100 * c // total}%)" for t, c in overused[:top]) if overused \
        else lines.append("   немає")
    used = set(counts)
    dead = [t for t in tax.all_tags() if t not in used]
    lines.append("")
    lines.append(f"Мертві (жодного вжитку): {len(dead)}")
    lines.append("   " + ", ".join(sorted(dead)[:60]) + (" …" if len(dead) > 60 else ""))
    lines.append("")
    lines.append("Найчастіші:")
    lines.extend(f"   {t}: {c}" for t, c in sorted(counts.items(), key=lambda x: -x[1])[:top])
    return "\n".join(lines)


def push_media(cfg: Config, state: State, media_pk: str,
               log: Callable[[str], None] = print) -> bool:
    """Заливає в Eagle один конкретний пост — після схвалення в ревʼю."""
    from .eagle import EagleClient, EagleError

    if not cfg.eagle_enabled:
        return False

    posts = _from_state(state)
    post = posts.get(str(media_pk))
    if post is None or not post.files:
        return False

    client = EagleClient(cfg.eagle_url, cfg.eagle_token)
    try:
        client.ping()
        root = client.ensure_folder(cfg.eagle_root_folder)
    except EagleError as exc:
        log(f"Eagle недоступний, файл лишився лише на диску: {exc}")
        return False

    collection = post.collections[0] if post.collections else ""
    folder_id = root
    if cfg.eagle_folder_per_collection and collection:
        try:
            folder_id = client.ensure_folder(safe_component(collection, 60), root)
        except EagleError:
            folder_id = root

    items = [_eagle_item(cfg, post, collection, path, idx)
             for path, idx in post.parts() if path.exists()]
    if not items:
        return False
    try:
        client.add_items(items, folder_id)
    except EagleError as exc:
        log(f"Eagle не прийняв файл: {exc}")
        return False
    state.mark_in_eagle(
        post.pk, (post.collection_pks[0] if post.collection_pks else "root"),
        folder_id or "")
    log(f"→ Eagle: додано {len(items)} файл(ів)")
    return True


def _eagle_item(cfg: Config, post: _Post, collection: str, path: Path, idx: int = 0):
    from .eagle import EagleItem

    slide = post.meta_for(idx)
    tags = list(cfg.eagle_extra_tags)
    if cfg.eagle_tag_author and post.username:
        tags.append(f"@{post.username}")
    tags.append(post.kind)
    if cfg.eagle_tag_collection and collection:
        tags.append(collection)
    if cfg.eagle_tags_from_hashtags:
        tags.extend(hashtags(post.caption))
    tags.extend(slide.get("tags", post.ai_tags))

    seen, unique = set(), []
    for tag in tags:
        key = str(tag).lower()
        if tag and key not in seen:
            seen.add(key)
            unique.append(tag)

    return EagleItem(
        path=str(path),
        name=short_title(post.caption, post.username, post.code),
        website=f"https://www.instagram.com/p/{post.code}/" if post.code else "",
        annotation=tagging.annotation(
            post.caption, slide.get("description", post.description),
            slide.get("screen_text", ""), slide.get("transcript", "")),
        tags=unique,
    )


def _remember(state: State, old: str, new: str) -> None:
    try:
        with state._lock:  # noqa: SLF001 — свій же клас, окремий метод тут зайвий
            state.db.execute("UPDATE files SET path = ? WHERE path = ?", (new, old))
            state.db.commit()
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------- дрібне
def _parse_date(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _kind(media_type, product_type) -> str:
    try:
        media_type = int(media_type or 0)
    except (TypeError, ValueError):
        media_type = 0
    product = (product_type or "").lower()
    if media_type == 8:
        return "carousel"
    if media_type == 2:
        return "reel" if product in ("clips", "reel") else "video"
    if media_type == 1:
        return "photo"
    return "post"


def _escape(text: str) -> str:
    """Екранує символи, які glob сприймає як шаблон."""
    return text.replace("[", "[[]")
