"""Вшивання опису, автора й посилання в самі медіафайли.

Відео (.mp4) — теги у стилі iTunes через mutagen, без ffmpeg.
Фото (.jpg) — EXIF через piexif, разом із XP-полями, які показує провідник Windows.

Жодна помилка тут не має валити завантаження: у найгіршому разі файл просто
залишиться без тегів, і про це буде рядок у журналі.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional

VIDEO_EXT = {".mp4", ".m4v", ".mov"}
JPEG_EXT = {".jpg", ".jpeg"}

COMMENT_LIMIT = 2000

# Заголовок нашої частини нотатки. Англійський, бо й опис тепер англійський,
# і за ним же впізнаємо вже описане при повторному проході.
SUMMARY_LABEL = "Visual summary:"
SCREEN_LABEL = "On screen:"
TRANSCRIPT_LABEL = "Voice-over:"


@dataclass
class MediaTags:
    """Те, що ми знаємо про пост і хочемо покласти всередину файлу."""

    title: str = ""
    author: str = ""            # username без @
    author_full: str = ""
    caption: str = ""
    url: str = ""
    taken_at: Optional[datetime] = None
    kind: str = ""              # reel / video / photo / carousel
    collections: List[str] = field(default_factory=list)
    hashtags: List[str] = field(default_factory=list)
    # Те, що написала візуальна модель, подивившись кадри.
    description: str = ""
    ai_tags: List[str] = field(default_factory=list)

    # ------------------------------------------------------------- готові рядки
    @property
    def artist(self) -> str:
        return f"@{self.author}" if self.author else ""

    @property
    def year(self) -> str:
        return self.taken_at.strftime("%Y-%m-%d") if self.taken_at else ""

    def summary(self) -> str:
        """Короткий опис для полів, куди довгий коментар не влізе."""
        text = " ".join((self.description or "").split())
        if not text:
            return self.title or self.artist
        return text[:250]

    def comment(self) -> str:
        """Підпис + опис + автор + посилання — те, що видно як «Коментар»."""
        blocks = []
        head = self.caption.strip()[:COMMENT_LIMIT]
        if head:
            blocks.append(head)
        description = " ".join((self.description or "").split())
        if description:
            # Свідомо після підпису: підпис — слова автора, опис — наші.
            blocks.append(f"{SUMMARY_LABEL} {description[:COMMENT_LIMIT]}")
        tail = []
        if self.author:
            tail.append(f"Автор: @{self.author}")
        if self.url:
            tail.append(f"Посилання: {self.url}")
        if tail:
            blocks.append("\n".join(tail))
        return "\n\n".join(blocks)

    def keywords(self) -> str:
        tags = list(self.hashtags) + list(self.ai_tags) + list(self.collections)
        seen, unique = set(), []
        for tag in tags:
            key = tag.lower()
            if tag and key not in seen:
                seen.add(key)
                unique.append(tag)
        return "; ".join(unique[:30])


def annotation(caption: str, description: str = "", screen_text: str = "",
               transcript: str = "") -> str:
    """Текст нотатки для Eagle: підпис автора плюс те, що побачила модель.

    Текст з екрана — окремим рядком: у туторіалах саме там назви плагінів і
    кроки, і саме за ними потім шукають.
    """
    blocks = [text for text in ((caption or "").strip(),) if text]
    description = " ".join((description or "").split())
    if description:
        blocks.append(f"{SUMMARY_LABEL} {description}")
    screen_text = " ".join((screen_text or "").split())
    if screen_text:
        blocks.append(f"{SCREEN_LABEL} {screen_text}")
    transcript = " ".join((transcript or "").split())
    if transcript:
        blocks.append(f"{TRANSCRIPT_LABEL} {transcript[:800]}")
    return "\n\n".join(blocks)


def apply(path: Path, tags: MediaTags) -> tuple[bool, str]:
    """Записує теги у файл. Повертає (успіх, повідомлення для журналу)."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in VIDEO_EXT:
        return _tag_video(path, tags)
    if suffix in JPEG_EXT:
        return _tag_jpeg(path, tags)
    return False, ""  # png/webp тощо — мовчки пропускаємо


# --------------------------------------------------------------------- відео
def _tag_video(path: Path, tags: MediaTags) -> tuple[bool, str]:
    try:
        from mutagen.mp4 import MP4, MP4FreeForm
    except ImportError:
        return False, "mutagen не встановлено — відео без тегів"

    try:
        handle = MP4(str(path))
        if handle.tags is None:
            handle.add_tags()

        def put(atom: str, value: str) -> None:
            if value:
                handle.tags[atom] = [value]

        put("\xa9nam", tags.title)              # Назва
        put("\xa9ART", tags.artist)             # Виконавець / автор
        put("aART", tags.author_full or tags.artist)
        put("\xa9cmt", tags.comment())          # Коментар
        put("desc", tags.summary())             # Короткий опис
        put("ldes", tags.comment())             # Довгий опис
        put("\xa9day", tags.year)
        put("\xa9gen", f"Instagram {tags.kind}".strip())
        keywords = tags.keywords()
        if keywords:
            put("keyw", keywords)
        if tags.collections:
            put("\xa9alb", tags.collections[0])
        if tags.url:
            # Кілька місць одразу: різні програми читають різні поля.
            put("purl", tags.url)                    # деякі плеєри показують як «джерело»
            handle.tags["----:com.apple.iTunes:URL"] = [
                MP4FreeForm(tags.url.encode("utf-8"))
            ]
            handle.tags["----:com.apple.iTunes:Instagram"] = [
                MP4FreeForm(tags.url.encode("utf-8"))
            ]
        handle.save()
        return True, ""
    except Exception as exc:  # noqa: BLE001 — теги не варті зламаного завантаження
        return False, f"теги не записались ({_short(exc)})"


# ---------------------------------------------------------------------- фото
def _tag_jpeg(path: Path, tags: MediaTags) -> tuple[bool, str]:
    try:
        import piexif
    except ImportError:
        return False, "piexif не встановлено — фото без тегів"

    try:
        try:
            exif = piexif.load(str(path))
        except Exception:  # noqa: BLE001 — файл без EXIF, починаємо з чистого
            exif = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
        exif.setdefault("0th", {})
        exif.setdefault("Exif", {})
        exif["thumbnail"] = None  # інакше piexif інколи спотикається на чужих прев'ю

        zeroth, sub = exif["0th"], exif["Exif"]
        comment = tags.comment()

        if comment:
            zeroth[piexif.ImageIFD.ImageDescription] = _ascii(comment)
            zeroth[piexif.ImageIFD.XPComment] = _utf16(comment)
        if tags.artist:
            zeroth[piexif.ImageIFD.Artist] = _ascii(tags.artist)
            zeroth[piexif.ImageIFD.XPAuthor] = _utf16(tags.artist)
        if tags.title:
            zeroth[piexif.ImageIFD.XPTitle] = _utf16(tags.title)
        subject = tags.summary()
        if subject:
            zeroth[piexif.ImageIFD.XPSubject] = _utf16(subject)
        keywords = tags.keywords()
        if keywords:
            zeroth[piexif.ImageIFD.XPKeywords] = _utf16(keywords)
        if tags.url:
            zeroth[piexif.ImageIFD.Copyright] = _ascii(tags.url)
        zeroth[piexif.ImageIFD.Software] = b"InstRef"

        if tags.taken_at:
            stamp = tags.taken_at.strftime("%Y:%m:%d %H:%M:%S").encode("ascii")
            zeroth[piexif.ImageIFD.DateTime] = stamp
            sub[piexif.ExifIFD.DateTimeOriginal] = stamp
            sub[piexif.ExifIFD.DateTimeDigitized] = stamp
        if comment:
            # UserComment вимагає 8-байтового префікса кодування
            sub[piexif.ExifIFD.UserComment] = b"UNICODE\x00" + comment.encode("utf-16-le")

        piexif.insert(piexif.dump(exif), str(path))
        return True, ""
    except Exception as exc:  # noqa: BLE001
        return False, f"EXIF не записався ({_short(exc)})"


# -------------------------------------------------------------------- дрібне
def _utf16(text: str) -> bytes:
    """Windows читає XP-теги як UTF-16LE із нульовим завершенням."""
    return text.encode("utf-16-le") + b"\x00\x00"


def _ascii(text: str) -> bytes:
    """Поля EXIF типу ASCII: кирилицю зберігаємо як UTF-8 — так роблять і інші
    інструменти, а Windows усе одно бере текст із XP-полів."""
    return text.encode("utf-8", errors="replace")


def _short(exc: Exception, limit: int = 90) -> str:
    text = str(exc).strip().replace("\n", " ") or exc.__class__.__name__
    return text[:limit] + ("…" if len(text) > limit else "")
