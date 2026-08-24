"""Попереднє ревʼю візуальною моделлю через LM Studio.

LM Studio піднімає OpenAI-сумісний сервер (типово http://localhost:1234/v1),
тож нових залежностей не треба — лише HTTP.

Модель бачить не одну обкладинку, а кілька кадрів, рівномірно знятих із ролика,
і повертає три речі: категорію, опис і теги. Обкладинка reels — це зазвичай
перший кадр, часто чорний або з титром, і судити за ним про весь ролик було
головним джерелом помилок.

Якщо сервер не відповідає або модель не завантажена — мовчки повертаємось до
правил. Недоступна модель не має ламати синхронізацію.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import requests

# Категорії, якими оперує модель. Свідомо короткі й непересічні.
MEME = "meme"
ART = "art"
AD = "ad"
GAME = "game"
OTHER = "other"

CATEGORIES = [MEME, ART, AD, GAME, OTHER]

CATEGORY_LABELS = {
    MEME: "мем",
    ART: "арт",
    AD: "реклама",
    GAME: "гра",
    OTHER: "інше",
}

DEFAULT_URL = "http://localhost:1234/v1"

# Стеля кількості кадрів в одному запиті. Висока свідомо: скільки саме витягне
# конкретна модель — питання її контексту й памʼяті, а не наше. Але кожен кадр
# коштує сотні токенів, тож вище SAFE_FRAMES застосунок попереджає, що
# відповіді можуть почати ламатись.
MAX_FRAMES = 60
SAFE_FRAMES = 12
MAX_TAGS = 15


def normalize_url(url: str) -> str:
    """Дописує /v1, якщо його немає.

    LM Studio показує в себе адресу як http://localhost:1234, і саме її люди
    вставляють. Але OpenAI-сумісні ендпоінти живуть під /v1 — без нього сервер
    відповідає 200 із «Unexpected endpoint», і виглядає це так, наче моделей
    не завантажено. Тому додаємо самі, але лише коли шляху взагалі немає:
    у кого вказано /api/v0 чи проксі — не чіпаємо.
    """
    from urllib.parse import urlsplit

    text = (url or "").strip().rstrip("/")
    if not text:
        return DEFAULT_URL
    if "://" not in text:
        text = "http://" + text
    parts = urlsplit(text)
    if parts.path in ("", "/"):
        return f"{parts.scheme}://{parts.netloc}/v1"
    return text


# Плейсхолдери підставляються простою заміною, а не str.format: інструкцію
# редагує людина, і зламані фігурні дужки не мають валити синхронізацію.
PLACEHOLDERS = {
    "{frames}": "скільки кадрів надіслано",
    "{kind}": "reel / video / carousel / photo",
    "{mode}": "VIDEO або IMAGE",
    "{taxonomy}": "списки дозволених тегів зі словника",
}

DEFAULT_PROMPT = """You are tagging a visual reference library for a 3D generalist and
art director. It holds Instagram references: advertising, fashion, automotive,
CGI and AI work, motion graphics, photography.

You are looking at {frames} frame(s) from ONE Instagram {kind}, in chronological
order. They are the same post, not different posts — judge it as a whole.
Media mode: {mode}.

CORE PRINCIPLE: precision over coverage. A wrong tag is worse than a missing one.

Return four things.

1. CATEGORY — exactly one of: meme, art, ad, game, other.
   - meme: humour, joke, funny clip, reaction, entertainment
   - art: design, 3D, motion graphics, illustration, photography, architecture,
     interior, typography, animation, VFX — visual work worth keeping as reference
   - ad: commercial, product promo, branded content
   - game: gameplay, game trailer, game UI, esports
   - other: anything else (talking head, news, cooking, travel vlog, pets, haul)
   This is a filing decision, separate from the tags below.

2. CONFIDENCE — 0.0 to 1.0 for that category.

3. DESCRIPTION — one paragraph in ENGLISH, under 80 words, written like a
   director's note. Cover only: the main subject and what it does; the setting;
   notable lighting (direction and source when both are clear) and colour
   treatment; the production technique when clearly identifiable.
   - Describe only what you see. Do not infer story, intent or meaning.
   - Do not invent characters, locations or brands. Do not retell the caption.
   - Do not open with "this video shows" / "this image depicts" — describe directly.
   - For a still, describe it as a still: "A man stands in a neon-lit alley…",
     not "A man walks…".
   - If techniques are mixed (live-action plus a CGI element), name both.

4. TAGS — pick ONLY from the lists below, word for word. Anything not in a list
   is discarded, so inventing tags loses information.
   - Every tag lowercase, hyphens instead of spaces, no "#".
   - Pick a tag only if a specific frame proves it. Skip a whole category when
     nothing fits — 5 correct tags beat 15 invented ones.
   - Never stack synonyms; pick the single most accurate one.
   - Never guess camera or software brands, production scale, or which AI tool
     was used unless a watermark is visible.
   - Skip indoor / outdoor / high-contrast / studio unless that IS the defining
     quality of the shot.
   - Aim for 8-18 tags; 5-7 is fine for a simple item.

ALLOWED TAGS
{taxonomy}

Answer with JSON only, nothing around it:
{"category": "<meme|art|ad|game|other>", "confidence": <0.0-1.0>,
 "description": "<one paragraph, English, under 80 words>",
 "tags": ["tag", "tag"], "why": "<max 8 words, English>"}"""

# Стара однокадрова інструкція лишається під рукою: за нею писані відповіді
# без опису й тегів, і вона ж — запасний варіант для дуже дрібних моделей.
PROMPT = DEFAULT_PROMPT


def build_prompt(template: str, frames: int, kind: str, mode: str = "video",
                 taxonomy=None) -> str:
    text = (template or "").strip() or DEFAULT_PROMPT
    if taxonomy is not None and "{taxonomy}" in text:
        text = text.replace("{taxonomy}", taxonomy.render(mode))
    else:
        # Своя інструкція без плейсхолдера — теги все одно перевіряються кодом,
        # просто модель не побачить списків і промахуватиметься частіше.
        text = text.replace("{taxonomy}", "")
    return (
        text.replace("{frames}", str(max(1, int(frames or 1))))
        .replace("{kind}", kind or "post")
        .replace("{mode}", (mode or "video").upper())
    )


@dataclass
class VisionVerdict:
    category: str = OTHER
    confidence: float = 0.0
    why: str = ""
    error: str = ""
    description: str = ""
    tags: List[str] = field(default_factory=list)
    frames: int = 0
    # Теги, яких немає у словнику. Не мовчазна втрата, а матеріал для того,
    # щоб словник ріс по реальному контенту.
    dropped: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.error and self.category in CATEGORIES

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def has_text(self) -> bool:
        """Чи є що покласти у файл, навіть якщо категорія не переконала."""
        return bool(self.description or self.tags)

    def short_description(self, limit: int = 140) -> str:
        text = " ".join(self.description.split())
        return text[:limit] + ("…" if len(text) > limit else "")


class VisionError(RuntimeError):
    pass


def client_for(cfg, taxonomy=None) -> "VisionClient":
    """Клієнт за налаштуваннями. Одне місце — щоб синхронізація й
    обслуговування питали ту саму модель тією самою інструкцією."""
    if taxonomy is None and getattr(cfg, "taxonomy_enabled", True):
        from .taxonomy import Taxonomy

        taxonomy = Taxonomy.load()
    return VisionClient(
        cfg.vision_url, cfg.vision_model, cfg.vision_timeout,
        cfg.vision_min_confidence, prompt=cfg.vision_prompt, taxonomy=taxonomy,
    )


class VisionClient:
    def __init__(self, base_url: str = DEFAULT_URL, model: str = "",
                 timeout: int = 60, min_confidence: float = 0.55,
                 prompt: str = "", taxonomy=None):
        self.base_url = normalize_url(base_url)
        self.model = (model or "").strip()
        self.timeout = timeout
        self.min_confidence = min_confidence
        self.prompt = prompt or ""
        self.taxonomy = taxonomy
        self.session = requests.Session()

    # ------------------------------------------------------------ перевірка
    def list_models(self) -> List[str]:
        """Моделі, завантажені в LM Studio зараз."""
        try:
            resp = self.session.get(f"{self.base_url}/models", timeout=10)
            resp.raise_for_status()
            body = resp.json()
        except requests.ConnectionError as exc:
            raise VisionError("LM Studio не відповідає — сервер не запущено.") from exc
        except requests.Timeout as exc:
            raise VisionError("LM Studio не встиг відповісти.") from exc
        except requests.RequestException as exc:
            raise VisionError(f"LM Studio: {_short(exc)}") from exc
        except ValueError as exc:
            raise VisionError("LM Studio повернув не-JSON відповідь.") from exc
        items = body.get("data") if isinstance(body, dict) else None
        return [str(item.get("id")) for item in (items or []) if item.get("id")]

    def resolve_model(self) -> str:
        """Якщо модель не вказана — беремо першу завантажену."""
        if self.model:
            return self.model
        models = self.list_models()
        if not models:
            raise VisionError("У LM Studio не завантажено жодної моделі.")
        self.model = models[0]
        return self.model

    # ---------------------------------------------------------- класифікація
    def classify(self, images: Sequence[bytes], caption: str = "",
                 username: str = "", kind: str = "post",
                 mode: str = "") -> VisionVerdict:
        """Показує моделі кадри одного поста. Помилки повертає, а не кидає."""
        shots = [img for img in (images or []) if img][:MAX_FRAMES]
        if not shots:
            return VisionVerdict(error="немає зображення")
        try:
            model = self.resolve_model()
        except VisionError as exc:
            return VisionVerdict(error=str(exc))

        mode = mode or ("video" if len(shots) > 1 or kind in ("reel", "video")
                        else "image")
        text = build_prompt(self.prompt, len(shots), kind, mode, self.taxonomy)
        context = []
        if username:
            context.append(f"Account: @{username}")
        if caption.strip():
            context.append(f"Caption: {caption.strip()[:400]}")
        if context:
            text += "\n\n" + "\n".join(context)

        content = [{"type": "text", "text": text}]
        for shot in shots:
            content.append({"type": "image_url", "image_url": {
                "url": "data:image/jpeg;base64," + base64.b64encode(shot).decode()
            }})

        payload = {
            "model": model,
            "temperature": 0,
            # опис і теги не влазять у 120 токенів, які вистачало на саму категорію
            "max_tokens": 600,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            resp = self.session.post(
                f"{self.base_url}/chat/completions", json=payload, timeout=self.timeout
            )
            resp.raise_for_status()
            body = resp.json()
        except requests.ConnectionError:
            return VisionVerdict(error="LM Studio не відповідає")
        except requests.Timeout:
            return VisionVerdict(error="модель не встигла відповісти")
        except requests.RequestException as exc:
            return VisionVerdict(error=f"LM Studio: {_short(exc)}")
        except ValueError:
            return VisionVerdict(error="LM Studio повернув не-JSON відповідь")

        try:
            answer = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return VisionVerdict(error="несподівана відповідь моделі")
        if isinstance(answer, list):  # деякі збірки віддають список частин
            answer = " ".join(
                part.get("text", "") for part in answer if isinstance(part, dict)
            )
        verdict = parse_answer(str(answer))
        verdict.frames = len(shots)
        if self.taxonomy is not None:
            # Інструкцію модель порушує, цю перевірку — ні.
            verdict.tags, verdict.dropped = self.taxonomy.normalize(verdict.tags, mode)
        return verdict

    def classify_image(self, image: bytes, caption: str = "",
                       username: str = "") -> VisionVerdict:
        """Один кадр — окремий випадок кількох."""
        return self.classify([image], caption=caption, username=username, kind="photo")


def parse_answer(text: str) -> VisionVerdict:
    """Витягує JSON із відповіді — моделі люблять обгортати його в ```json."""
    raw = (text or "").strip()
    chunk = _first_json_object(raw)
    if chunk is None:
        # інколи модель просто називає категорію словом
        for category in CATEGORIES:
            if re.search(rf"\b{category}\b", raw, re.IGNORECASE):
                return VisionVerdict(category=category, confidence=0.5, why="без JSON")
        return VisionVerdict(error="не вдалось розібрати відповідь")

    try:
        data = json.loads(chunk)
    except json.JSONDecodeError:
        return VisionVerdict(error="зіпсований JSON у відповіді")
    if not isinstance(data, dict):
        return VisionVerdict(error="несподівана відповідь моделі")

    description = _clean_text(data.get("description") or data.get("summary") or "")
    tags = clean_tags(data.get("tags"))

    category = str(data.get("category", "")).strip().lower()
    if category not in CATEGORIES:
        # Опис і теги вже є — віддаємо їх разом із помилкою, вони не винні.
        return VisionVerdict(
            error=f"невідома категорія «{category}»" if category else "модель не назвала категорію",
            description=description, tags=tags,
        )
    try:
        confidence = float(data.get("confidence", 0) or 0)
    except (TypeError, ValueError):
        confidence = 0.0
    return VisionVerdict(
        category=category,
        confidence=max(0.0, min(1.0, confidence)),
        why=str(data.get("why", ""))[:120],
        description=description,
        tags=tags,
    )


def _first_json_object(raw: str) -> Optional[str]:
    """Перший збалансований {...}, не плутаючись у дужках усередині рядків.

    Простий регекс тут уже не годиться: опис — вільний текст, у ньому цілком
    може трапитись фігурна дужка або лапки.
    """
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start:index + 1]
    return None


def clean_tags(value) -> List[str]:
    """Теги від моделі бувають списком, рядком через кому або з ґратками."""
    if isinstance(value, str):
        items = re.split(r"[,;\n]+", value)
    elif isinstance(value, (list, tuple)):
        items = [str(item) for item in value]
    else:
        return []

    seen, result = set(), []
    for item in items:
        tag = str(item).strip().strip("#").strip().lower()
        tag = re.sub(r"\s+", "-", tag)
        tag = re.sub(r"[^\w\-Ѐ-ӿ]", "", tag).strip("-")
        if not tag or len(tag) > 30:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        result.append(tag)
        if len(result) >= MAX_TAGS:
            break
    return result


def _clean_text(value) -> str:
    text = " ".join(str(value or "").split())
    return text[:1200]


# ------------------------------------------------------------------- дрібне
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def fetch_image(url: str, timeout: int = 30, proxy: str = "") -> Optional[bytes]:
    """Тягне обкладинку в памʼять — файл на диск не пишемо, бо пост ще може
    виявитись мемом і не заслужити місця в папці."""
    if not url:
        return None
    try:
        proxies = {"http": proxy, "https": proxy} if proxy else None
        resp = requests.get(
            str(url), timeout=timeout, proxies=proxies,
            headers={"User-Agent": UA, "Referer": "https://www.instagram.com/"},
        )
        resp.raise_for_status()
        return resp.content or None
    except requests.RequestException:
        return None


def thumbnail_url(media) -> str:
    """Обкладинка поста: для каруселі — перший слайд."""
    direct = getattr(media, "thumbnail_url", None)
    if direct:
        return str(direct)
    for resource in getattr(media, "resources", []) or []:
        thumb = getattr(resource, "thumbnail_url", None)
        if thumb:
            return str(thumb)
    return ""


def slide_urls(media, limit: int = MAX_FRAMES) -> List[str]:
    """Обкладинки слайдів каруселі: судити про неї за першим — те саме, що
    судити про ролик за титром."""
    urls: List[str] = []
    for resource in getattr(media, "resources", []) or []:
        thumb = getattr(resource, "thumbnail_url", None)
        if thumb and str(thumb) not in urls:
            urls.append(str(thumb))
        if len(urls) >= max(1, limit):
            break
    if not urls:
        cover = thumbnail_url(media)
        if cover:
            urls.append(cover)
    return urls


def _short(exc: Exception, limit: int = 90) -> str:
    text = str(exc).strip().replace("\n", " ") or exc.__class__.__name__
    return text[:limit] + ("…" if len(text) > limit else "")
