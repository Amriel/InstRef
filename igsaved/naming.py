"""Безпечні імена файлів для Windows + правила іменування завантажень."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_MULTISPACE = re.compile(r"\s+")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_component(name: str, max_len: int = 80) -> str:
    """Перетворює довільний рядок на коректну назву файлу/папки у Windows."""
    name = unicodedata.normalize("NFC", str(name or "")).strip()
    name = _ILLEGAL.sub("_", name)
    name = _MULTISPACE.sub(" ", name).strip(" .")
    if not name:
        name = "unnamed"
    if name.upper().split(".")[0] in _RESERVED:
        name = f"_{name}"
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "unnamed"


def base_name(taken_at: datetime | None, username: str, code: str) -> str:
    """Базове ім'я без розширення: 2026-08-20_username_ABCdef123."""
    stamp = taken_at.strftime("%Y-%m-%d") if taken_at else "0000-00-00"
    user = safe_component(username or "unknown", 40)
    return f"{stamp}_{user}_{safe_component(code or 'nocode', 30)}"


def render_template(
    template: str,
    taken_at: datetime | None,
    username: str,
    code: str,
    kind: str = "",
    media_id: str = "",
    collection: str = "",
    caption: str = "",
) -> str:
    """Підставляє токени в шаблон імені файлу; невідомі токени лишаються як є."""
    title = caption_slug(caption)
    values = {
        "{date}": taken_at.strftime("%Y-%m-%d") if taken_at else "0000-00-00",
        "{time}": taken_at.strftime("%H-%M") if taken_at else "00-00",
        "{user}": username or "unknown",
        "{code}": code or "nocode",
        "{type}": kind or "post",
        "{id}": str(media_id or ""),
        "{collection}": collection or "",
        # Пости без тексту трапляються часто — тоді замість порожнечі код поста,
        # інакше всі такі файли злиплися б в одну назву.
        "{title}": title or (code or "nocode"),
    }
    result = template or ""
    for token, value in values.items():
        result = result.replace(token, str(value))
    result = _tidy_separators(result)
    result = safe_component(result, 120)
    if not result or result == "unnamed":
        return base_name(taken_at, username, code)
    return result


def _tidy_separators(value: str) -> str:
    """Прибирає подвійні розділювачі, які лишаються від порожніх токенів."""
    value = re.sub(r"[ _-]*[_-][ _-]*", lambda m: "_" if "_" in m.group() else "-", value)
    return value.strip(" _-")


def asset_name(base: str, index: int | None, ext: str) -> str:
    """Ім'я файлу для одного ассета; index задається лише для каруселей."""
    ext = ext if ext.startswith(".") else f".{ext}"
    if index is None:
        return f"{base}{ext}"
    return f"{base}_{index:02d}{ext}"


def unique_path(path: Path) -> Path:
    """Якщо файл існує — додає _2, _3 ... Використовується лише як запобіжник."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for i in range(2, 1000):
        candidate = parent / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem}_{int(datetime.now().timestamp())}{suffix}"


_HASHTAG = re.compile(r"#([0-9A-Za-z_Ѐ-ӿÀ-ɏ]{2,40})")

# Емодзі, піктограми, службові символи — у назві файлу від них лише шкода.
_PICTOGRAMS = re.compile(
    "["
    "\U0001F000-\U0001FAFF"   # емодзі та символи
    "\U00002600-\U000027BF"   # різні знаки, дінгбати
    "\U0000FE00-\U0000FE0F"   # варіаційні селектори
    "\U00002190-\U000021FF"   # стрілки
    "\U00002B00-\U00002BFF"
    "\U0000200B-\U0000200F"   # нульової ширини
    "]+",
    flags=re.UNICODE,
)
_TAG_OR_MENTION = re.compile(r"[#@][^\s#@]+")
_LINK = re.compile(r"https?://\S+")


def caption_slug(caption: str, limit: int = 60) -> str:
    """Робить із підпису поста короткий читабельний шматок для назви файлу.

    Бере перший змістовний рядок, викидає хештеги, згадки, посилання та емодзі,
    обрізає по межі слова. Якщо після чистки нічого не лишилось — повертає "".
    """
    text = str(caption or "")
    text = _LINK.sub(" ", text)

    # Перший рядок, у якому є щось, крім хештегів і смайлів.
    chosen = ""
    for line in text.splitlines():
        cleaned = _PICTOGRAMS.sub(" ", _TAG_OR_MENTION.sub(" ", line))
        if len(_MULTISPACE.sub(" ", cleaned).strip()) >= 3:
            chosen = cleaned
            break
    if not chosen:  # підпис суцільно з хештегів — беремо його весь, без ґраток
        chosen = _PICTOGRAMS.sub(" ", text.replace("#", " ").replace("@", " "))

    chosen = _MULTISPACE.sub(" ", chosen).strip(" -–—·•,.:;!?|/\\")
    if not chosen:
        return ""

    if len(chosen) > limit:
        cut = chosen[:limit]
        if " " in cut[limit // 2:]:  # ріжемо по слову, якщо це не зробить огризок
            cut = cut.rsplit(" ", 1)[0]
        chosen = cut.rstrip(" -–—·•,.:;!?")
    return safe_component(chosen, limit)


def hashtags(caption: str, limit: int = 15) -> list[str]:
    """Витягує хештеги з підпису — зручно як теги в Eagle."""
    seen: list[str] = []
    for match in _HASHTAG.finditer(caption or ""):
        tag = match.group(1).lower()
        if tag not in seen:
            seen.append(tag)
        if len(seen) >= limit:
            break
    return seen


def short_title(caption: str, username: str, code: str, limit: int = 70) -> str:
    """Заголовок елемента для Eagle: перший рядок підпису або @автор."""
    line = (caption or "").strip().splitlines()
    first = line[0].strip() if line else ""
    first = _MULTISPACE.sub(" ", first)
    if len(first) > limit:
        first = first[: limit - 1].rstrip() + "…"
    if first:
        return first
    return f"@{username or 'unknown'} · {code}"


def ext_from_url(url: str, default: str = ".jpg") -> str:
    """Розширення з URL CDN (обрізає ?query)."""
    path = str(url or "").split("?", 1)[0].split("#", 1)[0]
    suffix = Path(path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mov", ".gif", ".heic"}:
        return ".jpg" if suffix == ".jpeg" else suffix
    return default
