"""Оркестратор синхронізації: скан підбірок → завантаження → метадані → Eagle."""

from __future__ import annotations

import json
import shutil
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import classify as classifier
from . import frames as framegrab
from . import taxonomy
from . import vision
from .classify import DOWNLOAD, REVIEW, SKIP, Rules
from .config import ALL_POSTS_PK, Config, DEVICE_PATH, LIKED_PK, STRUCTURE_PER_COLLECTION
from .downloader import Downloader, TooLarge, human_size
from .eagle import EagleClient, EagleError, EagleItem
from .instagram import (
    CollectionInfo,
    IGClient,
    InstagramError,
    collect_assets,
    label_for,
    media_to_dict,
    media_url,
)
from . import tagging
from .naming import (
    asset_name, caption_slug, ext_from_url, hashtags, render_template, safe_component,
    short_title, unique_path,
)
from .tagging import MediaTags, annotation
from .state import REVIEW_MODEL, REVIEW_RULES, State

EAGLE_BATCH = 40


@dataclass
class Stats:
    scanned: int = 0
    downloaded: int = 0
    files: int = 0
    bytes: int = 0
    skipped: int = 0
    failed: int = 0
    eagle_added: int = 0
    filtered: int = 0        # відсіяно як меми
    to_review: int = 0       # відкладено на ревʼю
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"переглянуто {self.scanned}, нових {self.downloaded} "
            f"({self.files} файлів, {human_size(self.bytes)}), "
            f"пропущено {self.skipped}, помилок {self.failed}"
            + (f", у Eagle {self.eagle_added}" if self.eagle_added else "")
            + (f", відсіяно мемів {self.filtered}" if self.filtered else "")
            + (f", на ревʼю {self.to_review}" if self.to_review else "")
        )


class SyncEngine:
    """Один прохід синхронізації. Без залежностей від GUI — придатний і для CLI."""

    def __init__(
        self,
        config: Config,
        state: State,
        sessionid: str,
        log: Callable[[str], None] = print,
        progress: Callable[[str, int, int], None] = lambda msg, cur, total: None,
        should_stop: Callable[[], bool] = lambda: False,
    ):
        self.cfg = config
        self.state = state
        self.sessionid = sessionid
        self.log = log
        self.progress = progress
        self.should_stop = should_stop

        self.stats = Stats()
        self.ig = IGClient(
            device_path=DEVICE_PATH,
            delay_range=(config.page_delay_min, config.page_delay_max),
            log=log,
            proxy=config.proxy,
        )
        self.dl = Downloader(
            timeout=config.request_timeout,
            retries=config.max_retries,
            pause=config.download_delay,
            proxy=config.proxy,
            max_bytes=int(config.skip_larger_than_mb or 0) * 1024 * 1024,
        )
        self.rules = Rules(
            block_accounts=config.block_accounts,
            allow_accounts=config.allow_accounts,
            meme_tags=list(classifier.MEME_TAGS) + list(config.extra_meme_tags),
            art_tags=list(classifier.ART_TAGS) + list(config.extra_art_tags),
            max_meme_seconds=config.max_meme_seconds,
        )
        self._vision = None
        self._vision_model = ""
        # Ролики, завантажені наперед заради кадрів: url → файл у _cache.
        # Схвалене відео потім переїжджає на місце, а не качається вдруге.
        self._prefetch: Dict[str, Path] = {}
        self._warned_no_cv = False
        self.eagle: Optional[EagleClient] = None
        self.eagle_root: Optional[str] = None
        self._eagle_queue: List[tuple] = []  # (folder_id, EagleItem, media_pk, collection_pk)

    # ================================================================== запуск
    def cooldown_left(self) -> float:
        """Скільки годин лишилось до дозволеного наступного проходу."""
        limit = float(self.cfg.min_hours_between_runs or 0)
        if limit <= 0:
            return 0.0
        passed = self.state.hours_since_last_run()
        if passed is None:
            return 0.0
        return max(0.0, limit - passed)

    def run(self, only_collections: Optional[List[str]] = None,
            force: bool = False) -> Stats:
        left = 0.0 if force else self.cooldown_left()
        if left > 0:
            # Свідомо гучно: тиха відмова тут виглядала б як «нічого не знайшлось».
            minutes = int(round(left * 60))
            self.log(
                f"Пропускаю прохід: попередній був щойно. Наступний можна через "
                f"{minutes} хв ({self.cfg.min_hours_between_runs:g} год між проходами)."
            )
            self.log(
                "  Часті звернення — головна причина, чому Instagram позначає "
                "застосунок як автоматизацію. Змінити межу: Налаштування → Сканування."
            )
            self.stats.errors.append("cooldown")
            return self.stats

        run_id = self.state.start_run()
        note = ""
        try:
            self.ig.connect(self.sessionid)
            self._setup_eagle()
            self._vision = self._setup_vision()

            collections = self._pick_collections(only_collections)
            if not collections:
                self.log("Не обрано жодної підбірки.")
                return self.stats

            self.cfg.root.mkdir(parents=True, exist_ok=True)
            if self.cfg.download_thumbnails:
                self.cfg.thumbs_dir.mkdir(parents=True, exist_ok=True)
            if self.cfg.write_metadata:
                self.cfg.meta_dir.mkdir(parents=True, exist_ok=True)

            for position, col in enumerate(collections, start=1):
                if self.should_stop():
                    note = "зупинено користувачем"
                    break
                self.log(f"── [{position}/{len(collections)}] {col.display}")
                self._sync_collection(col)
                self._flush_eagle()
                self.state.touch_collection(col.pk)

            self.log(f"Готово: {self.stats.summary()}")
        except InstagramError as exc:
            note = str(exc)
            self.stats.errors.append(note)
            self.log(f"✖ {note}")
        except Exception as exc:  # noqa: BLE001
            note = f"{exc.__class__.__name__}: {exc}"
            self.stats.errors.append(note)
            self.log(f"✖ Несподівана помилка: {note}")
            self.log(traceback.format_exc(limit=4))
        finally:
            try:
                self._flush_eagle()
            except Exception:  # noqa: BLE001
                pass
            self._clear_cache()
            self.dl.close()
            self.state.finish_run(
                run_id, self.stats.scanned, self.stats.downloaded,
                self.stats.skipped, self.stats.failed, note,
            )
        return self.stats

    # ------------------------------------------------------------- підбірки
    def list_collections(self) -> List[CollectionInfo]:
        """Використовується GUI, щоб намалювати список із галочками."""
        self.ig.connect(self.sessionid)
        cols = self.ig.list_collections()
        for col in cols:
            self.state.upsert_collection(col.pk, col.display, col.media_count)
        return cols

    def _pick_collections(self, only: Optional[List[str]]) -> List[CollectionInfo]:
        cols = self.ig.list_collections()
        for col in cols:
            self.state.upsert_collection(col.pk, col.display, col.media_count)

        if only:
            wanted = {str(pk) for pk in only}
            return [c for c in cols if c.pk in wanted]

        chosen: List[CollectionInfo] = []
        for col in cols:
            if col.is_all_saved:
                if self.cfg.sync_all_saved:
                    chosen.append(col)
            elif col.is_liked:
                if self.cfg.sync_liked:
                    chosen.append(col)
            elif self.cfg.sync_all_collections or col.pk in set(self.cfg.enabled_collections):
                chosen.append(col)
        return chosen

    # ------------------------------------------------- фільтр пролайканого
    def _judge(self, media, col: CollectionInfo):
        """Класифікує пост. Повертає None там, де фільтр не застосовується.

        Свідомо працює лише для пролайканого: збережене користувач курує сам,
        і відсівати там щось автоматично було б зухвало.
        """
        if not col.is_liked or not self.cfg.classify_liked:
            return None

        verdict = classifier.classify(media, self.rules)
        if verdict.decision != REVIEW:
            return verdict

        # Сумнівне — і лише сумнівне — показуємо візуальній моделі.
        if self._vision is not None and self._ask_vision(media, verdict):
            return verdict

        action = self.cfg.uncertain_action
        if action == "download":
            verdict.decision = DOWNLOAD
        elif action == "skip":
            verdict.decision = SKIP
        return verdict

    def _setup_vision(self):
        """Готує клієнта LM Studio. Недоступна модель не має нічого ламати."""
        if not self.cfg.vision_enabled:
            return None
        # Свідомо НЕ дивимось на sync_liked: підбірку можна обрати галочкою у
        # списку, і тоді цей прапорець лишається False, хоча пролайкане
        # синхронізується. Прив'язка до нього мовчки вимикала модель.
        if not self.cfg.classify_liked and not self.cfg.vision_describe_downloads:
            self.log("Візуальна модель не потрібна: фільтр мемів і опис вимкнено.")
            return None
        client = vision.client_for(self.cfg)
        try:
            model = client.resolve_model()
        except vision.VisionError as exc:
            self.log(f"Візуальна модель пропущена: {exc} Працюю за правилами.")
            return None
        self._vision_model = model
        want = max(1, int(self.cfg.vision_frames or 1))
        if want > 1 and not framegrab.available():
            self.log(
                "Візуальна модель: opencv не встановлено — бачитиме лише обкладинку. "
                "Перезапусти install.bat, щоб модель дивилась відео цілком."
            )
        self.log(f"Візуальна модель: {model} ({want} кадр(ів) з ролика)")
        return client

    # -------------------------------------------------- кадри для моделі
    def _frames_for(self, media) -> tuple[List[bytes], str]:
        """Кадри одного поста плюс те, чим цей пост є для інструкції.

        Відео завантажується наперед у _cache: інакше кадрів не дістати, а
        качати ролик двічі — марно витрачений трафік.
        """
        want = max(1, min(vision.MAX_FRAMES, int(self.cfg.vision_frames or 1)))
        mtype = int(getattr(media, "media_type", 0) or 0)
        kind = label_for(media)

        if mtype == 2 and want > 1 and framegrab.available():
            path = self._prefetch_video(media)
            if path is not None:
                shots = framegrab.extract(path, want)
                if shots:
                    if want > vision.SAFE_FRAMES:
                        # Про здрібнення кадрів варто сказати вголос: інакше
                        # незрозуміло, чому модель раптом гірше читає дрібний текст.
                        self.log(
                            f"   ⤓ {len(shots)} кадр(ів) по {framegrab.side_for(want)} px"
                        )
                    return shots, kind
                self.log("   ⤼ кадри з ролика не дістались — дивлюсь обкладинку")

        if mtype == 8:
            shots = []
            side = framegrab.side_for(want)
            for url in vision.slide_urls(media, want):
                data = vision.fetch_image(url, self.cfg.request_timeout, self.cfg.proxy)
                if data:
                    shots.append(framegrab.shrink_image(data, side))
            if shots:
                return shots, kind

        cover = vision.fetch_image(
            vision.thumbnail_url(media), self.cfg.request_timeout, self.cfg.proxy
        )
        return ([framegrab.shrink_image(cover)] if cover else []), kind

    def _prefetch_video(self, media) -> Optional[Path]:
        url = str(getattr(media, "video_url", "") or "")
        pk = str(getattr(media, "pk", "") or "")
        if not url or not pk:
            return None
        if not self.cfg.download_videos:
            # Відео все одно не потрібне — качати цілий ролик заради кадрів
            # було б витратою трафіку. Обійдемось обкладинкою.
            return None
        dest = self.cfg.cache_dir / f"{pk}.mp4"
        if dest.exists() and dest.stat().st_size > 0:
            self._prefetch[url] = dest
            return dest
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.dl.fetch(url, dest)
        except TooLarge as exc:
            self.log(f"   ⤼ ролик завеликий для перегляду моделлю — {exc}")
            return None
        except Exception as exc:  # noqa: BLE001 — не змогли подивитись, не біда
            self.log(f"   ⤼ ролик не завантажився для перегляду: {exc}")
            return None
        if not dest.exists() or dest.stat().st_size == 0:
            return None
        self._prefetch[url] = dest
        return dest

    def _drop_prefetch(self, pk: str) -> None:
        """Пост відсіяно — тимчасовий ролик більше не потрібен."""
        path = self.cfg.cache_dir / f"{str(pk)}.mp4"
        for url, cached in list(self._prefetch.items()):
            if Path(cached) == path:
                self._prefetch.pop(url, None)
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass

    def _clear_cache(self) -> None:
        for url, path in list(self._prefetch.items()):
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            self._prefetch.pop(url, None)
        try:
            cache = self.cfg.cache_dir
            if cache.exists() and not any(cache.iterdir()):
                cache.rmdir()
        except OSError:
            pass

    # ------------------------------------------------------ запит до моделі
    def _ask_vision(self, media, verdict) -> bool:
        """Показує моделі кадри поста. True — якщо рішення прийнято нею."""
        shots, kind = self._frames_for(media)
        if not shots:
            return False

        user = getattr(media, "user", None)
        answer = self._vision.classify(
            shots,
            caption=getattr(media, "caption_text", "") or "",
            username=(getattr(user, "username", "") if user else "") or "",
            kind=kind, mode=taxonomy.mode_for(kind),
        )
        # Опис і теги зберігаємо навіть тоді, коли категорія не переконала:
        # вони однаково поїдуть у метадані файлу й у Eagle.
        self._save_ai(media, answer)

        if not answer.ok:
            if answer.error:
                self.log(f"   ⤼ модель не відповіла: {answer.error}")
            return False

        seen = f" ({answer.frames} кадр.)" if answer.frames > 1 else ""
        if answer.confidence < self.cfg.vision_min_confidence:
            verdict.reasons.append(
                f"модель вагається: {answer.label} ({answer.confidence:.2f}){seen}"
            )
            if answer.description:
                verdict.reasons.append(answer.short_description())
            return False

        verdict.reasons.append(
            f"модель{seen}: {answer.label} ({answer.confidence:.2f})"
            + (f" — {answer.why}" if answer.why else "")
        )
        if answer.category in set(self.cfg.vision_skip_categories):
            verdict.decision = SKIP
        elif answer.category in (vision.ART, vision.AD):
            verdict.decision = DOWNLOAD
        else:
            if answer.description:
                verdict.reasons.append(answer.short_description())
            return False        # «інше» лишаємо тобі на ревʼю
        if answer.description:
            verdict.reasons.append(answer.short_description())
        verdict.by_model = True
        return True

    def _glanceable(self, verdict) -> bool:
        """Чи має рішення моделі спершу пройти перед очима."""
        return (
            verdict is not None
            and verdict.by_model
            and self.cfg.model_needs_glance
        )

    def _save_ai(self, media, answer, idx: int = 0, label: str = "") -> None:
        # Відкинуті теги рахуємо завжди — навіть коли решту відповіді не беремо.
        if answer.dropped:
            self.state.note_tag_candidates(answer.dropped)
        if not (answer.description or answer.tags or answer.ok):
            return
        pk = str(getattr(media, "pk", "") or "")
        if not pk:
            return
        self.state.set_ai_meta(
            pk, answer.category if answer.ok else "", answer.confidence,
            answer.description, answer.tags, self._vision_model, answer.frames,
            idx=idx,
        )
        prefix = f"[{label}] " if label else ""
        if answer.description:
            self.log(f"   ✎ {prefix}{answer.short_description()}")
        if answer.tags:
            self.log(f"   # {prefix}{', '.join(answer.tags[:10])}")

    def _describe(self, media, pk: str, files: List[tuple]) -> None:
        """Опис і теги для вже завантаженого поста — без жодного рішення.

        Файли вже на диску, тож кадри беремо з них: ні повторного завантаження,
        ні тимчасової копії.

        Кожен файл описується окремо. Для ролика це один запит на кілька його
        кадрів, а от карусель — це різні картинки, і спільний опис на всі був би
        неправдою про кожну з них.
        """
        if self._vision is None:
            return
        want = max(1, min(vision.MAX_FRAMES, int(self.cfg.vision_frames or 1)))
        user = getattr(media, "user", None)
        caption = getattr(media, "caption_text", "") or ""
        username = (getattr(user, "username", "") if user else "") or ""
        kind = label_for(media)
        multi = len(files) > 1

        for path, idx in files:
            if self.should_stop():
                return
            if self.state.has_ai_meta(pk, idx):
                continue
            shots = framegrab.shots_from_file(path, want)
            if not shots:
                self.log(f"   ⤼ нема з чого описати {path.name}")
                continue
            answer = self._vision.classify(
                shots, caption=caption, username=username,
                kind="photo" if (multi and path.suffix.lower() not in
                                 framegrab.VIDEO_EXT) else kind,
                mode=taxonomy.mode_for(path.name),
            )
            if answer.error and not answer.has_text:
                self.log(f"   ⤼ опис не склався: {answer.error}")
                continue
            if not answer.has_text:
                # Модель відповіла, але нічого не написала. Мовчки збережений
                # порожній рядок назавжди заблокував би повторну спробу.
                self.log("   ⤼ модель не написала ні опису, ні тегів")
                continue
            self._save_ai(media, answer, idx=idx,
                          label=path.name if multi else "")

    def _record_skip(self, media, verdict) -> None:
        pk = str(getattr(media, "pk", ""))
        user = getattr(media, "user", None)
        username = (getattr(user, "username", "") if user else "") or "?"
        self.state.record_media(
            pk, getattr(media, "code", "") or "", username,
            getattr(media, "taken_at", None), int(getattr(media, "media_type", 0) or 0),
            getattr(media, "product_type", "") or "",
            getattr(media, "caption_text", "") or "", media_url(getattr(media, "code", "")),
            status="skipped",
        )
        self.state.mark_skipped(pk, verdict.why())
        self._drop_prefetch(pk)
        self.stats.filtered += 1
        self.log(f"   ⨯ пропущено @{username}: {verdict.why()}")

    # ----------------------------------------------------- обхід однієї підбірки
    def _sync_collection(self, col: CollectionInfo) -> None:
        known_streak = 0
        seen = 0
        # Для пролайканого рахуємо саме переглянуті пости: відсіяні меми не
        # збільшують ліміт завантажень, тож без цього обхід не має кінця.
        scan_limit = self.cfg.liked_scan_limit if col.is_liked else self.cfg.scan_limit
        total_hint = min(col.media_count, scan_limit) if (col.media_count and scan_limit) \
            else (scan_limit or col.media_count or 0)

        def on_page(page: int, count: int) -> None:
            self.log(f"   сторінка {page}: {count} постів")

        try:
            for media in self.ig.iter_media(col.pk, self.should_stop, on_page):
                if self.should_stop():
                    return
                if scan_limit and seen >= scan_limit:
                    self.log(f"   переглянуто {seen} найсвіжіших — ліміт вичерпано")
                    return
                seen += 1
                self.stats.scanned += 1
                self.progress(f"{col.display}: {seen}", seen, total_hint)

                pk = str(getattr(media, "pk", "") or "")
                if not pk:
                    continue
                self.state.add_membership(pk, col.pk, col.display)

                # Пости, які раніше відсіяли як меми, не оцінюємо повторно.
                if self.state.is_skipped(pk):
                    self.stats.skipped += 1
                    known_streak += 1
                    if self.cfg.incremental and self.cfg.stop_after_known and \
                            known_streak >= self.cfg.stop_after_known:
                        return
                    continue

                already = self.state.is_known(pk)
                if already:
                    self.stats.skipped += 1
                    known_streak += 1
                    # Раніше завантажене, але ще не імпортоване в цю папку Eagle.
                    self._queue_eagle_existing(media, col)
                    if self.cfg.incremental and self.cfg.stop_after_known and \
                            known_streak >= self.cfg.stop_after_known:
                        self.log(
                            f"   зупинка: {known_streak} відомих постів поспіль "
                            f"(режим «лише нове»)"
                        )
                        return
                    continue

                known_streak = 0
                verdict = self._judge(media, col)
                if verdict is not None and verdict.decision == SKIP \
                        and not self._glanceable(verdict):
                    self._record_skip(media, verdict)
                    continue
                # Відсіяне САМОЮ моделлю не викидаємо мовчки: ролик уже лежить
                # у _cache після перегляду кадрів, тож покласти його у теку
                # ревʼю нічого не коштує — зате помилку видно й можна скасувати.

                try:
                    self._process_media(media, col, verdict)
                except Exception as exc:  # noqa: BLE001
                    self.stats.failed += 1
                    message = f"{pk}: {exc}"
                    self.stats.errors.append(message)
                    self.log(f"   ✖ {message}")

                if self.cfg.max_items_per_run and self.stats.downloaded >= self.cfg.max_items_per_run:
                    self.log(f"   ліміт {self.cfg.max_items_per_run} нових постів за запуск досягнуто")
                    return
        except InstagramError as exc:
            self.stats.failed += 1
            self.stats.errors.append(str(exc))
            self.log(f"   ✖ {exc}")

    # ------------------------------------------------------- один пост
    def _process_media(self, media, col: CollectionInfo, verdict=None) -> None:
        mtype = int(getattr(media, "media_type", 0) or 0)
        if not self.cfg.wants(mtype):
            self.stats.skipped += 1
            return

        # Три різні стани, і плутати їх не можна:
        #   uncertain — правила не впорались, вирішувати людині;
        #   glance    — модель вирішила сама, людині лишається глянути;
        #   решта     — рішення остаточне, шлях прямий.
        uncertain = verdict is not None and verdict.decision == REVIEW
        glance = self._glanceable(verdict)
        rejected = glance and verdict.decision == SKIP
        held = uncertain or glance          # у Eagle поки не йде
        in_review_folder = uncertain or rejected

        assets = collect_assets(
            media,
            want_videos=self.cfg.download_videos,
            want_photos=self.cfg.download_photos,
            # для ревʼю прев'ю тягнемо завжди — інакше у вікні нічого показати
            want_thumbs=self.cfg.download_thumbnails or held,
        )
        if not assets:
            self.stats.skipped += 1
            return

        pk = str(getattr(media, "pk", ""))
        code = getattr(media, "code", "") or ""
        user = getattr(media, "user", None)
        username = (getattr(user, "username", "") if user else "") or "unknown"
        taken_at: Optional[datetime] = getattr(media, "taken_at", None)
        caption = getattr(media, "caption_text", "") or ""

        self.state.record_media(
            pk, code, username, taken_at, mtype,
            getattr(media, "product_type", "") or "", caption, media_url(code),
        )

        base = render_template(
            self.cfg.filename_template,
            taken_at, username, code,
            kind=label_for(media),
            media_id=pk,
            collection=col.display,
            caption=caption,
        )
        folder = self.cfg.review_dir if in_review_folder else self.cfg.target_dir(col.display)
        folder.mkdir(parents=True, exist_ok=True)

        media_paths: List[Path] = []
        # (файл, номер слайда) — номер потрібен, щоб опис слайда каруселі
        # потрапив саме в його файл, а не в сусідній.
        indexed: List[tuple] = []
        thumb_path: Optional[Path] = None
        for asset in assets:
            if self.should_stop():
                return
            ext = ext_from_url(asset.url, ".mp4" if asset.kind == "video" else ".jpg")
            if asset.kind == "thumb":
                dest = self.cfg.thumbs_dir / asset_name(base, asset.index, ".jpg")
            else:
                dest = folder / asset_name(base, asset.index, ext)
            dest = self._free_path(dest, pk)

            try:
                result = self._download_or_reuse(asset.url, dest, pk, asset.kind)
            except TooLarge as exc:
                self.log(f"   ⤼ пропущено (завеликий файл): {dest.name} — {exc}")
                continue
            if result is None:
                continue
            self.state.add_file(str(dest), pk, asset.kind, asset.index or 0, result)
            if asset.kind == "thumb" and thumb_path is None:
                thumb_path = dest
            if asset.kind in ("video", "photo"):
                media_paths.append(dest)
                indexed.append((dest, int(asset.index or 0)))
                self.stats.files += 1
                self.stats.bytes += result

        if not media_paths:
            raise RuntimeError("жоден файл не завантажився")

        # Опис пишемо до вшивання тегів — інакше він не потрапить у файл.
        if self.cfg.vision_describe_downloads:
            self._describe(media, pk, indexed)
        if self.cfg.embed_metadata:
            for path, idx in indexed:
                self._embed(path, media, col, idx)

        if self.cfg.write_metadata:
            self._write_metadata(media, base, pk)

        self.state.mark_done(pk)
        self.stats.downloaded += 1
        kind = label_for(media)

        if held:
            self.stats.to_review += 1
            preview = thumb_path or (media_paths[0] if media_paths else None)
            self.state.add_review(
                pk, str(media_paths[0]), str(preview or ""), username, caption,
                media_url(code), verdict.why(), verdict.decision,
                source=REVIEW_MODEL if glance else REVIEW_RULES,
            )
            mark = "≈" if glance else "?"
            where = "рішення моделі" if glance else "ревʼю"
            self.log(f"   {mark} {base} [{kind}] → {where}: {verdict.why()}")
            # У Eagle свідомо не відправляємо: бібліотека має отримувати лише
            # те, що ти бачив. Імпорт станеться після кнопки у вкладці.
        else:
            why = f" — {verdict.why()}" if verdict is not None else ""
            self.log(f"   ✓ {base} [{kind}]{why}")
            self._queue_eagle(media, col, media_paths)

    def _free_path(self, dest: Path, pk: str) -> Path:
        """Читабельні назви можуть збігтися у двох різних постів — тоді беремо
        сусіднє вільне ім'я замість того, щоб мовчки затерти чужий файл."""
        owner = self.state.path_owner(str(dest))
        if owner is not None and str(owner) != str(pk):
            return unique_path(dest)
        if owner is None and dest.exists() and dest.stat().st_size > 0:
            return unique_path(dest)
        return dest

    def _embed(self, path: Path, media, col: CollectionInfo, idx: int = 0) -> None:
        """Кладе опис, автора й посилання всередину самого файлу."""
        user = getattr(media, "user", None)
        username = (getattr(user, "username", "") if user else "") or "unknown"
        caption = getattr(media, "caption_text", "") or ""
        code = getattr(media, "code", "") or ""
        pk = str(getattr(media, "pk", ""))
        ai = self.state.ai_meta(pk, idx) or {}

        tags = MediaTags(
            title=caption_slug(caption) or code,
            author=username,
            author_full=(getattr(user, "full_name", "") if user else "") or "",
            caption=caption,
            url=media_url(code),
            taken_at=getattr(media, "taken_at", None),
            kind=label_for(media),
            collections=self.state.collection_names_for(pk) or [col.display],
            hashtags=hashtags(caption),
            description=ai.get("description", ""),
            ai_tags=ai.get("tags", []),
        )
        ok, problem = tagging.apply(path, tags)
        if not ok and problem:
            self.log(f"   ⤼ {path.name}: {problem}")

    def _download_or_reuse(self, url: str, dest: Path, pk: str, kind: str) -> Optional[int]:
        """Якщо такий самий файл уже є в іншій папці — копіюємо, а не тягнемо знову."""
        if dest.exists() and dest.stat().st_size > 0:
            return dest.stat().st_size
        cached = self._prefetch.pop(str(url), None)
        if cached is not None and Path(cached).exists() and Path(cached).stat().st_size > 0:
            # Ролик уже качали, щоб показати моделі — переносимо, а не тягнемо вдруге.
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                Path(cached).replace(dest)
            except OSError:
                shutil.move(str(cached), str(dest))
            return dest.stat().st_size
        if self.cfg.structure == STRUCTURE_PER_COLLECTION:
            for known in self.state.media_files(pk, ("video", "photo")):
                candidate = Path(known)
                if candidate.name == dest.name and candidate.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(candidate, dest)
                    return dest.stat().st_size
        result = self.dl.fetch(url, dest)
        return result.size

    def _write_metadata(self, media, base: str, pk: str) -> None:
        payload = media_to_dict(media, self.state.collection_names_for(pk))
        path = self.cfg.meta_dir / f"{base}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # =============================================================== Eagle
    def _setup_eagle(self) -> None:
        if not self.cfg.eagle_enabled:
            # Eagle запущений, а імпорт вимкнено — майже напевно недогляд.
            try:
                EagleClient(self.cfg.eagle_url, self.cfg.eagle_token).ping()
            except EagleError:
                return
            self.log(
                "⚠ Eagle запущений, але імпорт вимкнено — файли підуть лише на диск. "
                "Постав галочку «Імпортувати завантажене в Eagle» у налаштуваннях."
            )
            return
        client = EagleClient(self.cfg.eagle_url, self.cfg.eagle_token)
        try:
            info = client.ping()
        except EagleError as exc:
            self.log(f"Eagle пропущено: {exc}")
            return
        version = info.get("version") if isinstance(info, dict) else ""
        self.log(f"Eagle підключено{f' (версія {version})' if version else ''}.")
        try:
            self.eagle_root = client.ensure_folder(self.cfg.eagle_root_folder)
        except EagleError as exc:
            self.log(f"Eagle: не вдалось створити папку — {exc}")
            return
        self.eagle = client

    def _eagle_folder(self, col: CollectionInfo) -> Optional[str]:
        if not self.eagle:
            return None
        if not self.cfg.eagle_folder_per_collection:
            return self.eagle_root
        try:
            return self.eagle.ensure_folder(safe_component(col.display, 60), self.eagle_root)
        except EagleError as exc:
            self.log(f"Eagle: {exc}")
            return self.eagle_root

    def _build_item(self, media, col: CollectionInfo, path: Path,
                    idx: int = 0) -> EagleItem:
        user = getattr(media, "user", None)
        username = (getattr(user, "username", "") if user else "") or "unknown"
        caption = getattr(media, "caption_text", "") or ""
        code = getattr(media, "code", "") or ""

        ai = self.state.ai_meta(str(getattr(media, "pk", "")), idx) or {}

        tags = list(self.cfg.eagle_extra_tags)
        if self.cfg.eagle_tag_author:
            tags.append(f"@{username}")
        tags.append(label_for(media))
        if self.cfg.eagle_tag_collection and col.display:
            tags.append(col.display)
        if self.cfg.eagle_tags_from_hashtags:
            tags.extend(hashtags(caption))
        tags.extend(ai.get("tags", []))
        # прибрати дублікати, зберігши порядок
        seen, unique = set(), []
        for tag in tags:
            key = tag.lower()
            if tag and key not in seen:
                seen.add(key)
                unique.append(tag)

        return EagleItem(
            path=str(path),
            name=short_title(caption, username, code),
            website=media_url(code),
            annotation=annotation(caption, ai.get("description", "")),
            tags=unique,
        )

    def _already_in_eagle(self, pk: str, collection_pk: str) -> bool:
        """Один пост — один елемент, якщо не сказано інакше.

        Eagle копіює файл на кожен імпорт, тож «додати той самий ролик ще й у
        папку лайків» означає другу копію в бібліотеці, а не другу полицю.
        """
        if self.cfg.eagle_one_item_per_post:
            return self.state.is_in_eagle(pk)
        return self.state.is_in_eagle(pk, collection_pk)

    def _queue_eagle(self, media, col: CollectionInfo, paths: List[Path]) -> None:
        if not self.eagle:
            return
        folder_id = self._eagle_folder(col)
        pk = str(getattr(media, "pk", ""))
        if self._already_in_eagle(pk, col.pk):
            return
        # Пост міг потрапити в чергу двічі за один прохід — по підбірці й по
        # лайках. Позначаємо одразу, а не після відправки.
        if any(item[2] == pk for item in self._eagle_queue):
            return
        # Слайд каруселі має свій опис — беремо його за номером файлу в базі.
        for path in paths:
            idx = self.state.file_index(str(path))
            self._eagle_queue.append(
                (folder_id, self._build_item(media, col, path, idx), pk, col.pk))
        if self.cfg.eagle_import_thumbnails:
            pass  # прев'ю навмисно не імпортуємо: Eagle робить власні
        if len(self._eagle_queue) >= EAGLE_BATCH:
            self._flush_eagle()

    def _queue_eagle_existing(self, media, col: CollectionInfo) -> None:
        """Пост уже на диску, але ще не в цій папці Eagle — доімпортувати."""
        if not self.eagle:
            return
        pk = str(getattr(media, "pk", ""))
        if self._already_in_eagle(pk, col.pk) or self.state.is_pending_review(pk):
            return
        paths = [Path(p) for p in self.state.media_files(pk) if Path(p).exists()]
        if paths:
            self._queue_eagle(media, col, paths)

    def _flush_eagle(self) -> None:
        if not self.eagle or not self._eagle_queue:
            return
        grouped: Dict[Optional[str], List[EagleItem]] = {}
        marks: List[tuple] = []
        for folder_id, item, pk, col_pk in self._eagle_queue:
            grouped.setdefault(folder_id, []).append(item)
            marks.append((pk, col_pk, folder_id))
        self._eagle_queue = []

        for folder_id, items in grouped.items():
            try:
                sent = self.eagle.add_items(items, folder_id)
                self.stats.eagle_added += sent
                self.log(f"   → Eagle: додано {sent} файл(ів)")
            except EagleError as exc:
                self.log(f"   ✖ Eagle: {exc}")
                return
        for pk, col_pk, folder_id in marks:
            self.state.mark_in_eagle(pk, col_pk, folder_id or "")
