"""Локальна база (SQLite): що вже завантажено, у яких підбірках, що вже в Eagle."""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

# Хто поставив пост у чергу: правила (людина має вирішити) чи модель
# (вона вже вирішила, людині лишається глянути й за потреби скасувати).
REVIEW_RULES = "rules"
REVIEW_MODEL = "model"

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    pk            TEXT PRIMARY KEY,
    code          TEXT,
    username      TEXT,
    taken_at      TEXT,
    media_type    INTEGER,
    product_type  TEXT,
    caption       TEXT,
    url           TEXT,
    first_seen    TEXT,
    downloaded_at TEXT,
    status        TEXT
);
CREATE TABLE IF NOT EXISTS files (
    path       TEXT PRIMARY KEY,
    media_pk   TEXT,
    kind       TEXT,
    idx        INTEGER,
    bytes      INTEGER,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_files_media ON files(media_pk);
CREATE TABLE IF NOT EXISTS membership (
    media_pk        TEXT,
    collection_pk   TEXT,
    collection_name TEXT,
    seen_at         TEXT,
    PRIMARY KEY (media_pk, collection_pk)
);
CREATE TABLE IF NOT EXISTS eagle_items (
    media_pk      TEXT,
    collection_pk TEXT,
    folder_id     TEXT,
    imported_at   TEXT,
    PRIMARY KEY (media_pk, collection_pk)
);
CREATE TABLE IF NOT EXISTS collections (
    pk          TEXT PRIMARY KEY,
    name        TEXT,
    media_count INTEGER,
    last_sync   TEXT
);
CREATE TABLE IF NOT EXISTS review (
    media_pk  TEXT PRIMARY KEY,
    path      TEXT,
    thumb     TEXT,
    username  TEXT,
    caption   TEXT,
    url       TEXT,
    reason    TEXT,
    verdict   TEXT,
    added_at  TEXT,
    decided   TEXT,
    source    TEXT DEFAULT 'rules'
);
CREATE TABLE IF NOT EXISTS ai_meta (
    media_pk    TEXT,
    idx         INTEGER DEFAULT 0,
    category    TEXT,
    confidence  REAL,
    description TEXT,
    tags        TEXT,
    model       TEXT,
    frames      INTEGER,
    created_at  TEXT,
    PRIMARY KEY (media_pk, idx)
);
CREATE TABLE IF NOT EXISTS tag_candidates (
    tag        TEXT PRIMARY KEY,
    hits       INTEGER DEFAULT 0,
    first_seen TEXT,
    last_seen  TEXT,
    state      TEXT DEFAULT 'new'
);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT,
    finished_at TEXT,
    scanned     INTEGER DEFAULT 0,
    downloaded  INTEGER DEFAULT 0,
    skipped     INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    note        TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ai_row(row) -> dict:
    return {
        "idx": int(row["idx"] or 0) if "idx" in row.keys() else 0,
        "category": row["category"] or "",
        "confidence": float(row["confidence"] or 0.0),
        "description": row["description"] or "",
        "tags": [t for t in (row["tags"] or "").split("\n") if t],
        "model": row["model"] or "",
        "frames": int(row["frames"] or 0),
    }


class State:
    """Тонка обгортка над SQLite. Потокобезпечна через один лок."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        with self._lock:
            self.db.executescript(SCHEMA)
            self.db.execute("PRAGMA journal_mode=WAL")
            self._add_missing_columns()
            self.db.commit()

    def _add_missing_columns(self) -> None:
        """CREATE TABLE IF NOT EXISTS не змінює вже створену таблицю, тож нові
        стовпці доводиться доливати руками — інакше стара база валить запити."""
        for table, column, definition in (
            ("review", "source", "TEXT DEFAULT 'rules'"),
        ):
            if column not in self._columns(table):
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

        # ai_meta переїхала з ключа (media_pk) на (media_pk, idx): у каруселі
        # кожен слайд — окрема картинка й заслуговує власного опису. ALTER
        # первинний ключ не міняє, тож таблицю доводиться перезбирати.
        columns = self._columns("ai_meta")
        if columns and "idx" not in columns:
            self.db.executescript(
                """
                ALTER TABLE ai_meta RENAME TO ai_meta_old;
                CREATE TABLE ai_meta (
                    media_pk    TEXT,
                    idx         INTEGER DEFAULT 0,
                    category    TEXT,
                    confidence  REAL,
                    description TEXT,
                    tags        TEXT,
                    model       TEXT,
                    frames      INTEGER,
                    created_at  TEXT,
                    PRIMARY KEY (media_pk, idx)
                );
                INSERT INTO ai_meta (media_pk, idx, category, confidence, description,
                                     tags, model, frames, created_at)
                    SELECT media_pk, 0, category, confidence, description,
                           tags, model, frames, created_at FROM ai_meta_old;
                DROP TABLE ai_meta_old;
                """
            )

    def _columns(self, table: str) -> set:
        return {
            row["name"]
            for row in self.db.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def close(self) -> None:
        with self._lock:
            self.db.close()

    # ------------------------------------------------------------- медіа
    def is_downloaded(self, pk: str) -> bool:
        with self._lock:
            row = self.db.execute(
                "SELECT status FROM media WHERE pk = ?", (str(pk),)
            ).fetchone()
        return bool(row and row["status"] == "done")

    def is_known(self, pk: str) -> bool:
        """Чи вважати пост уже опрацьованим.

        Тонкість: для звичайного «done» ми ще й перевіряємо файли на диску —
        якщо користувач випадково стер файл, хай скачається знову. А от статус
        «archived» ставиться свідомою чисткою папки: файлів навмисно немає,
        і качати їх повторно не треба.
        """
        with self._lock:
            row = self.db.execute(
                "SELECT status FROM media WHERE pk = ?", (str(pk),)
            ).fetchone()
        if not row:
            return False
        if row["status"] == "archived":
            return True
        return row["status"] == "done" and self.files_exist(pk)

    def mark_archived(self, pk: str) -> None:
        """Файли прибрано вручну, але пост лишається в памʼяті як завантажений."""
        with self._lock:
            self.db.execute(
                "UPDATE media SET status = 'archived' WHERE pk = ? AND status = 'done'",
                (str(pk),),
            )
            self.db.commit()

    def tracked_files(self) -> list[sqlite3.Row]:
        """Усі файли, які створив саме застосунок — чуже не чіпаємо."""
        with self._lock:
            return self.db.execute(
                "SELECT path, media_pk, kind, bytes FROM files"
            ).fetchall()

    def files_exist(self, pk: str) -> bool:
        """Перевіряє, що записані файли досі на диску (користувач міг їх прибрати)."""
        with self._lock:
            rows = self.db.execute(
                "SELECT path FROM files WHERE media_pk = ? AND kind IN ('video','photo')",
                (str(pk),),
            ).fetchall()
        return bool(rows) and all(Path(r["path"]).exists() for r in rows)

    def record_media(
        self,
        pk: str,
        code: str,
        username: str,
        taken_at: Optional[datetime],
        media_type: int,
        product_type: str,
        caption: str,
        url: str,
        status: str = "pending",
    ) -> None:
        with self._lock:
            self.db.execute(
                """
                INSERT INTO media (pk, code, username, taken_at, media_type, product_type,
                                   caption, url, first_seen, downloaded_at, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(pk) DO UPDATE SET
                    code=excluded.code, username=excluded.username,
                    taken_at=excluded.taken_at, media_type=excluded.media_type,
                    product_type=excluded.product_type, caption=excluded.caption,
                    url=excluded.url
                """,
                (
                    str(pk), code, username,
                    taken_at.isoformat() if taken_at else None,
                    media_type, product_type, caption, url, _now(),
                    _now() if status == "done" else None, status,
                ),
            )
            self.db.commit()

    def mark_done(self, pk: str, status: str = "done") -> None:
        with self._lock:
            self.db.execute(
                "UPDATE media SET status = ?, downloaded_at = ? WHERE pk = ?",
                (status, _now(), str(pk)),
            )
            self.db.commit()

    def add_file(self, path: str, media_pk: str, kind: str, idx: int, size: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO files (path, media_pk, kind, idx, bytes, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (str(path), str(media_pk), kind, idx, size, _now()),
            )
            self.db.commit()

    def file_index(self, path: str) -> int:
        """Номер слайда, під яким файл записаний. 0 — пост із одного файлу."""
        with self._lock:
            row = self.db.execute(
                "SELECT idx FROM files WHERE path = ?", (str(path),)
            ).fetchone()
        return int(row["idx"] or 0) if row else 0

    def path_owner(self, path: str) -> Optional[str]:
        """Якому посту належить цей файл. Потрібно, щоб читабельні назви
        двох різних постів не злиплися в один файл."""
        with self._lock:
            row = self.db.execute(
                "SELECT media_pk FROM files WHERE path = ?", (str(path),)
            ).fetchone()
        return row["media_pk"] if row else None

    def files_by_stem(self) -> dict:
        """Ім'я файлу без розширення → (пост, номер слайда).

        Eagle показує елемент саме під цим ім'ям, і це єдиний місток назад до
        нашої бази: своїх ідентифікаторів ми в бібліотеці не лишаємо.
        """
        with self._lock:
            rows = self.db.execute(
                "SELECT path, media_pk, idx FROM files WHERE kind IN ('video','photo')"
            ).fetchall()
        result = {}
        for row in rows:
            stem = Path(row["path"]).stem
            if stem:
                result[stem] = (str(row["media_pk"]), int(row["idx"] or 0))
        return result

    def media_files(self, pk: str, kinds: Iterable[str] = ("video", "photo")) -> list[str]:
        placeholders = ",".join("?" for _ in kinds)
        with self._lock:
            rows = self.db.execute(
                f"SELECT path FROM files WHERE media_pk = ? AND kind IN ({placeholders})"
                " ORDER BY idx",
                (str(pk), *kinds),
            ).fetchall()
        return [r["path"] for r in rows]

    # -------------------------------------------------------- членство
    def add_membership(self, media_pk: str, collection_pk: str, collection_name: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO membership (media_pk, collection_pk, collection_name, seen_at)"
                " VALUES (?,?,?,?)",
                (str(media_pk), str(collection_pk), collection_name, _now()),
            )
            self.db.commit()

    def collection_pks_for(self, media_pk: str) -> list[str]:
        """Саме pk. Синхронізація позначає імпорт у Eagle за pk підбірки, і
        обслуговування мусить питати тим самим ключем — інакше воно вважає пост
        неімпортованим і кладе в бібліотеку другу копію."""
        with self._lock:
            rows = self.db.execute(
                "SELECT collection_pk FROM membership WHERE media_pk = ?", (str(media_pk),)
            ).fetchall()
        return [str(r["collection_pk"]) for r in rows if r["collection_pk"]]

    def collection_names_for(self, media_pk: str) -> list[str]:
        with self._lock:
            rows = self.db.execute(
                "SELECT collection_name FROM membership WHERE media_pk = ?", (str(media_pk),)
            ).fetchall()
        return [r["collection_name"] for r in rows if r["collection_name"]]

    # ----------------------------------------------------------- Eagle
    def is_in_eagle(self, media_pk: str, collection_pk: Optional[str] = None) -> bool:
        """Чи цей пост уже в Eagle.

        collection_pk=None означає «будь-де». Саме ця перевірка потрібна майже
        завжди: Eagle на кожен addFromPaths КОПІЮЄ файл, тож той самий ролик,
        доданий від імені двох підбірок, стає двома окремими елементами.
        """
        query = "SELECT 1 FROM eagle_items WHERE media_pk = ?"
        params = [str(media_pk)]
        if collection_pk is not None:
            query += " AND collection_pk = ?"
            params.append(str(collection_pk))
        with self._lock:
            row = self.db.execute(query, params).fetchone()
        return row is not None

    def eagle_duplicates(self) -> list[sqlite3.Row]:
        """Пости, заведені в Eagle більше одного разу."""
        with self._lock:
            return self.db.execute(
                "SELECT media_pk, COUNT(*) AS copies FROM eagle_items"
                " GROUP BY media_pk HAVING copies > 1 ORDER BY copies DESC"
            ).fetchall()

    def mark_in_eagle(self, media_pk: str, collection_pk: str, folder_id: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO eagle_items (media_pk, collection_pk, folder_id, imported_at)"
                " VALUES (?,?,?,?)",
                (str(media_pk), str(collection_pk), folder_id, _now()),
            )
            self.db.commit()

    # ------------------------------------------------- опис від моделі
    def set_ai_meta(self, media_pk: str, category: str, confidence: float,
                    description: str, tags: Iterable[str], model: str = "",
                    frames: int = 0, idx: int = 0) -> None:
        """Те, що модель написала про пост. Живе окремо від media, бо
        зʼявляється ще до того, як пост вирішено качати.

        idx=0 — про пост загалом, 1..N — про конкретний слайд каруселі.
        """
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO ai_meta (media_pk, idx, category, confidence,"
                " description, tags, model, frames, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (str(media_pk), int(idx or 0), category or "", float(confidence or 0.0),
                 description or "", "\n".join(str(t) for t in (tags or []) if t),
                 model or "", int(frames or 0), _now()),
            )
            self.db.commit()

    def ai_meta(self, media_pk: str, idx: int = 0) -> Optional[dict]:
        """Опис для конкретного файлу. Якщо для слайда свого немає — береться
        опис поста загалом: краще спільний текст, ніж порожнеча."""
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM ai_meta WHERE media_pk = ? AND idx = ?",
                (str(media_pk), int(idx or 0)),
            ).fetchone()
            if row is None and idx:
                row = self.db.execute(
                    "SELECT * FROM ai_meta WHERE media_pk = ? AND idx = 0",
                    (str(media_pk),),
                ).fetchone()
        return _ai_row(row) if row else None

    def has_ai_meta(self, media_pk: str, idx: int = 0) -> bool:
        """Саме для цього idx — без підміни описом поста, інакше карусель
        зупинилась би на першому ж слайді."""
        with self._lock:
            row = self.db.execute(
                "SELECT 1 FROM ai_meta WHERE media_pk = ? AND idx = ?"
                " AND COALESCE(description, '') <> ''",
                (str(media_pk), int(idx or 0)),
            ).fetchone()
        return row is not None

    def all_ai_meta(self) -> dict:
        """Одним запитом — щоб дозалив у Eagle не бив базу по посту.

        Ключ — (pk, idx); для зручності опис поста продубльовано під ключем pk.
        """
        with self._lock:
            rows = self.db.execute("SELECT * FROM ai_meta").fetchall()
        result: dict = {}
        for row in rows:
            pk, idx = str(row["media_pk"]), int(row["idx"] or 0)
            parsed = _ai_row(row)
            result[(pk, idx)] = parsed
            if idx == 0:
                result[pk] = parsed
        return result

    def ai_meta_count(self) -> int:
        with self._lock:
            row = self.db.execute("SELECT COUNT(*) AS c FROM ai_meta").fetchone()
        return int(row["c"]) if row else 0

    # ------------------------------------------- кандидати у словник тегів
    def note_tag_candidates(self, tags: Iterable[str]) -> None:
        """Рахує теги, які модель запропонувала, а словник не прийняв.

        Відкинутий тег — не сміття, а сигнал: якщо модель наполягає на ньому
        раз за разом, словнику його бракує.
        """
        with self._lock:
            for tag in tags or []:
                tag = str(tag).strip().lower()
                if not tag:
                    continue
                self.db.execute(
                    "INSERT INTO tag_candidates (tag, hits, first_seen, last_seen, state)"
                    " VALUES (?,1,?,?,'new')"
                    " ON CONFLICT(tag) DO UPDATE SET hits = hits + 1, last_seen = ?",
                    (tag, _now(), _now(), _now()),
                )
            self.db.commit()

    def tag_candidates(self, min_hits: int = 1, limit: int = 60) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT * FROM tag_candidates WHERE state = 'new' AND hits >= ?"
                " ORDER BY hits DESC, last_seen DESC LIMIT ?",
                (int(min_hits), int(limit)),
            ).fetchall()

    def tag_candidate_count(self, min_hits: int = 1) -> int:
        with self._lock:
            row = self.db.execute(
                "SELECT COUNT(*) AS c FROM tag_candidates WHERE state = 'new' AND hits >= ?",
                (int(min_hits),),
            ).fetchone()
        return int(row["c"]) if row else 0

    def resolve_tag_candidate(self, tag: str, state: str) -> None:
        """state: added — тег пішов у словник; ignored — більше не пропонувати."""
        with self._lock:
            self.db.execute(
                "UPDATE tag_candidates SET state = ? WHERE tag = ?",
                (state, str(tag).strip().lower()),
            )
            self.db.commit()

    def media_by_url(self) -> dict:
        """Посилання на пост → його pk. Потрібно, щоб упізнати елемент Eagle:
        своїх ідентифікаторів ми там не лишаємо, а адреса поста лишається."""
        with self._lock:
            rows = self.db.execute(
                "SELECT pk, url FROM media WHERE COALESCE(url, '') <> ''"
            ).fetchall()
        return {str(row["url"]).rstrip("/"): str(row["pk"]) for row in rows}

    # -------------------------------------------------------- ревʼю
    def add_review(self, media_pk: str, path: str, thumb: str, username: str,
                   caption: str, url: str, reason: str, verdict: str,
                   source: str = REVIEW_RULES) -> None:
        with self._lock:
            self.db.execute(
                "INSERT OR REPLACE INTO review (media_pk, path, thumb, username, caption,"
                " url, reason, verdict, added_at, decided, source)"
                " VALUES (?,?,?,?,?,?,?,?,?,NULL,?)",
                (str(media_pk), str(path), str(thumb or ""), username, caption,
                 url, reason, verdict, _now(), source or REVIEW_RULES),
            )
            self.db.commit()

    def pending_review(self, source: Optional[str] = None) -> list[sqlite3.Row]:
        """Черга на рішення. source=None — уся, інакше лише свій різновид.

        Порядок для черги моделі свідомо інший: спершу те, що вона зібралась
        викинути. Помилкове «мем» коштує втраченого поста, помилкове «арт» —
        зайвого файлу, тож рятувати треба перше.
        """
        query = "SELECT * FROM review WHERE decided IS NULL"
        params: list = []
        if source:
            query += " AND COALESCE(source, ?) = ?"
            params += [REVIEW_RULES, source]
        query += " ORDER BY (verdict = 'skip') DESC, added_at DESC"
        with self._lock:
            return self.db.execute(query, params).fetchall()

    def is_pending_review(self, media_pk: str) -> bool:
        """Пост чекає на рішення — у Eagle йому поки не місце."""
        with self._lock:
            row = self.db.execute(
                "SELECT 1 FROM review WHERE media_pk = ? AND decided IS NULL",
                (str(media_pk),),
            ).fetchone()
        return row is not None

    def review_count(self, source: Optional[str] = None) -> int:
        query = "SELECT COUNT(*) AS c FROM review WHERE decided IS NULL"
        params: list = []
        if source:
            query += " AND COALESCE(source, ?) = ?"
            params += [REVIEW_RULES, source]
        with self._lock:
            row = self.db.execute(query, params).fetchone()
        return int(row["c"]) if row else 0

    def decide_review(self, media_pk: str, decision: str) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE review SET decided = ? WHERE media_pk = ?", (decision, str(media_pk))
            )
            self.db.commit()

    def clear_review(self, decision: str = "cleared",
                     source: Optional[str] = None) -> int:
        """Закриває чергу ревʼю. Повертає, скільки записів закрито."""
        query = "UPDATE review SET decided = ? WHERE decided IS NULL"
        params: list = [decision]
        if source:
            query += " AND COALESCE(source, ?) = ?"
            params += [REVIEW_RULES, source]
        with self._lock:
            cursor = self.db.execute(query, params)
            self.db.commit()
            return int(cursor.rowcount or 0)

    def update_file_path(self, old: str, new: str) -> None:
        with self._lock:
            self.db.execute("UPDATE files SET path = ? WHERE path = ?", (str(new), str(old)))
            self.db.execute("UPDATE review SET path = ? WHERE path = ?", (str(new), str(old)))
            self.db.commit()

    def forget_media(self, media_pk: str) -> None:
        """Прибирає пост із бази цілком — щоб не вважався завантаженим."""
        with self._lock:
            for table in ("files", "membership", "eagle_items", "review", "ai_meta"):
                self.db.execute(f"DELETE FROM {table} WHERE media_pk = ?", (str(media_pk),))
            self.db.execute("DELETE FROM media WHERE pk = ?", (str(media_pk),))
            self.db.commit()

    def mark_skipped(self, pk: str, reason: str) -> None:
        """Пост свідомо не качаємо — але памʼятаємо, щоб не оцінювати щоразу."""
        with self._lock:
            self.db.execute(
                "UPDATE media SET status = 'skipped', downloaded_at = ? WHERE pk = ?",
                (_now(), str(pk)),
            )
            self.db.commit()

    def is_skipped(self, pk: str) -> bool:
        with self._lock:
            row = self.db.execute(
                "SELECT status FROM media WHERE pk = ?", (str(pk),)
            ).fetchone()
        return bool(row and row["status"] == "skipped")

    # ------------------------------------------------------- підбірки
    def upsert_collection(self, pk: str, name: str, media_count: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO collections (pk, name, media_count) VALUES (?,?,?)"
                " ON CONFLICT(pk) DO UPDATE SET name=excluded.name, media_count=excluded.media_count",
                (str(pk), name, int(media_count or 0)),
            )
            self.db.commit()

    def touch_collection(self, pk: str) -> None:
        with self._lock:
            self.db.execute("UPDATE collections SET last_sync = ? WHERE pk = ?", (_now(), str(pk)))
            self.db.commit()

    def collection_downloaded_count(self, pk: str) -> int:
        with self._lock:
            row = self.db.execute(
                "SELECT COUNT(*) AS c FROM membership m JOIN media md ON md.pk = m.media_pk"
                " WHERE m.collection_pk = ? AND md.status = 'done'",
                (str(pk),),
            ).fetchone()
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------ run
    def start_run(self) -> int:
        with self._lock:
            cur = self.db.execute("INSERT INTO runs (started_at) VALUES (?)", (_now(),))
            self.db.commit()
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, scanned: int, downloaded: int, skipped: int,
                   failed: int, note: str = "") -> None:
        with self._lock:
            self.db.execute(
                "UPDATE runs SET finished_at=?, scanned=?, downloaded=?, skipped=?, failed=?, note=?"
                " WHERE id=?",
                (_now(), scanned, downloaded, skipped, failed, note, run_id),
            )
            self.db.commit()

    def last_finished_at(self) -> Optional[datetime]:
        """Коли завершився попередній прохід. Потрібно, щоб не бити API частіше,
        ніж дозволено: саме серії проходів з різницею у хвилини Instagram
        сприймає як автоматизацію."""
        with self._lock:
            row = self.db.execute(
                "SELECT finished_at FROM runs WHERE finished_at IS NOT NULL"
                " ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row or not row["finished_at"]:
            return None
        try:
            return datetime.fromisoformat(str(row["finished_at"]))
        except ValueError:
            return None

    def hours_since_last_run(self) -> Optional[float]:
        last = self.last_finished_at()
        if last is None:
            return None
        now = datetime.now(timezone.utc)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        return max(0.0, (now - last).total_seconds() / 3600.0)

    def last_runs(self, limit: int = 10) -> list[sqlite3.Row]:
        with self._lock:
            return self.db.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    def totals(self) -> dict:
        with self._lock:
            # «archived» — це теж завантажені пости, просто файли прибрані
            # чисткою папки. Не рахувати їх означало б показувати 1 пост
            # на дві тисячі файлів.
            media = self.db.execute(
                "SELECT COUNT(*) AS c FROM media WHERE status IN ('done','archived')"
            ).fetchone()["c"]
            files = self.db.execute("SELECT COUNT(*) AS c, COALESCE(SUM(bytes),0) AS b FROM files").fetchone()
        return {"media": int(media), "files": int(files["c"]), "bytes": int(files["b"])}
