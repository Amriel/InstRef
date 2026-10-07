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
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import requests

from .taxonomy import DEFAULT_ALIASES, profiles_for
from .useful import software_tags, useful_tags

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

# Стеля кількості кадрів в одному запиті. Низька свідомо: вимірювання на
# реальних файлах показали, що 6–12 великих кадрів (≈900 px) читають текст на
# екрані й дають чесніші теги, а десятки дрібних (≈400 px) модель лише
# перераховує («Later frames show…») і галюцинує теги зі словника.
MAX_FRAMES = 32
MAX_TAGS = 15

# Заперечення в «доказі» тегу: модель сама пише «no visible sneakers», але
# тег усе одно кладе у список. Такі теги відкидаємо кодом, а не проханням.
NEGATIVE_EVIDENCE = re.compile(
    r"\b(no|not|none|absent|without|unclear|cannot|can't|n/a)\b", re.IGNORECASE)


# Ознаки візуальної моделі в її назві. Текстова модель приймає запит з
# картинками мовчки і відповідає про те, чого не бачила, — порожній JSON або
# фантазія за підписом. Тому назву перевіряємо до, а не після.
VISUAL_MARKS = ("vl", "vision", "llava", "moondream", "pixtral", "minicpm-v", "idefics",
                "gemma-3", "gemma3", "florence", "internvl", "phi-3-vision", "phi-3.5-vision",
                "phi-4-multimodal", "smolvlm", "molmo", "qvq", "4o", "omni", "vlm")


def looks_visual(model_id: str, types: Optional[dict] = None) -> bool:
    """Чи візуальна модель.

    Якщо LM Studio сказала тип (`/api/v0/models` → vlm / llm / embeddings), вирішує
    він: назва бреше в обидва боки («qwen3-vl» без vision-частини, «gemma-3» без
    картинок у деяких квантах). Без типу лишається евристика за назвою.
    """
    name = (model_id or "").strip()
    if not name:
        return True   # невідомо — не лякаємо
    known = (types or {}).get(name)
    if known:
        return str(known).lower() == "vlm"
    lowered = name.lower()
    return any(mark in lowered for mark in VISUAL_MARKS)


def api_root(url: str) -> str:
    """Корінь сервера з адреси OpenAI-сумісного API.

    Рідні ендпоінти LM Studio (`/api/v1/...`) живуть поруч із `/v1`, а не
    всередині нього: http://localhost:1234/v1 → http://localhost:1234.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(normalize_url(url))
    path = parts.path.rstrip("/")
    for suffix in ("/v1", "/api/v0", "/api/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


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
    # Теги тепер окремим запитом (TAG_PROMPT), тож у цій інструкції
    # {taxonomy} завжди порожній рядок; працює лише в однозапитному режимі.
    "{taxonomy}": "порожньо (теги — окремим запитом; у режимі одного запиту — списки словника)",
    "{examples}": "зразки описів, які ти схвалив у перегляді",
}

_PROMPT_HEAD = """You are tagging a visual reference library for a 3D generalist and
art director. It holds Instagram references: advertising, fashion, automotive,
CGI and AI work, motion graphics, 2D animation and illustration, graphic design,
photography.

You are looking at {frames} frame(s) from ONE Instagram {kind}, in chronological
order. They are the same post, not different posts — judge it as a whole.
Media mode: {mode}.

CORE PRINCIPLE: precision over coverage. A wrong tag is worse than a missing one.

Return six things.

1. CATEGORY — exactly one of: meme, art, ad, game, other.
   - meme: humour, joke, funny clip, reaction, entertainment
   - art: design, 3D, motion graphics, illustration, photography, architecture,
     interior, typography, animation, VFX — visual work worth keeping as reference
   - ad: commercial, product promo, branded content
   - game: gameplay, game trailer, game UI, esports
   - other: anything else (talking head, news, cooking, travel vlog, pets, haul)
   This is a filing decision, separate from the tags.

2. CONFIDENCE — 0.0 to 1.0 for that category.

3. SUMMARY — ONE sentence in ENGLISH, under 20 words: what this post is as a
   reference — subject plus genre and technique. Example: "CGI car commercial
   with neon night lighting and slow orbit shots." No frame-by-frame, no caption retelling.

4. DESCRIPTION — one paragraph in ENGLISH, written like a director's note.
   Under 80 words for a single-scene post. When the frames show several
   distinct scenes or sections, cover the WHOLE post in order — what it opens
   with, what it moves through, how it ends — in up to 130 words, so that
   nothing shown only in the middle or at the end is lost. For each part:
   the subject and what it does; the setting; notable lighting (direction and
   source when both are clear) and colour treatment; the production technique
   when clearly identifiable.
   - Describe only what you see. Do not infer story, intent or meaning.
   - Do not invent characters, locations or brands. Do not retell the caption.
   - Do not open with "this video shows" / "this image depicts" — describe directly.
   - For a still, describe it as a still: "A man stands in a neon-lit alley…",
     not "A man walks…".
   - If techniques are mixed (live-action plus a CGI element), name both.

5. ON_SCREEN_TEXT — any readable text burned into the frames: captions,
   step titles, software or plugin names, settings, brand names, watermarks.
   Quote it as written, joined with " | ". Empty string if there is none.
   Do not include the Instagram caption here — only text visible in the frames.

6. MEDIUM — the ONE foundation this post is made of: live-action, 3d-render,
   2d-animation, motion-graphics, stop-motion, screen-recording, illustration,
   photograph, graphic-design, mixed-media. A still photo is photograph, a
   drawn or painted still is illustration, CGI of any kind is 3d-render.
"""

_TAG_RULES = """   - Every tag lowercase, hyphens instead of spaces, no "#".
   - Pick a tag only if a specific frame proves it. Skip a whole category when
     nothing fits — 5 correct tags beat 15 invented ones.
   - Never stack synonyms; pick the single most accurate one.
   - Never guess camera or software brands, production scale, or which AI tool
     was used unless a watermark is visible.
   - Skip generic tags (high-contrast, studio-lighting, a mood) unless that IS
     the defining quality of the shot.
   - Aim for 6-14 tags. For EACH tag give 3-6 words of visual evidence from a
     specific frame. A tag you cannot back with evidence must be omitted."""

# Перший запит двокрокового режиму: лише опис. Теги — окремим запитом, зі
# списків, що стосуються саме цього поста (TAG_PROMPT).
DESCRIBE_PROMPT = _PROMPT_HEAD + """{examples}
Answer with JSON only, nothing around it:
{"category": "<meme|art|ad|game|other>", "confidence": <0.0-1.0>,
 "summary": "<one sentence, under 20 words>",
 "description": "<one paragraph, English, 80 words; up to 130 for a multi-scene post>",
 "on_screen_text": "<text visible in frames, or empty string>",
 "medium": "<live-action|3d-render|2d-animation|motion-graphics|stop-motion|screen-recording|illustration|photograph|graphic-design|mixed-media>",
 "why": "<max 8 words, English>"}"""

# Один запит, як було до двокрокового режиму: `cfg.vision_two_pass = False` і
# клієнт.classify. Увесь словник у промпті — ціна цього режиму.
SINGLE_PASS_PROMPT = _PROMPT_HEAD.replace("six things", "seven things") + """
7. TAGS — pick ONLY from the lists below, word for word. Anything not in a list
   is discarded, so inventing tags loses information.
""" + _TAG_RULES + """

ALLOWED TAGS
{taxonomy}
{examples}
Answer with JSON only, nothing around it:
{"category": "<meme|art|ad|game|other>", "confidence": <0.0-1.0>,
 "summary": "<one sentence, under 20 words>",
 "description": "<one paragraph, English, 80 words; up to 130 for a multi-scene post>",
 "on_screen_text": "<text visible in frames, or empty string>",
 "medium": "<live-action|3d-render|2d-animation|motion-graphics|stop-motion|screen-recording|illustration|photograph|graphic-design|mixed-media>",
 "tags": [{"tag": "<tag>", "evidence": "<3-6 words>"}],
 "why": "<max 8 words, English>"}"""

# Другий запит: ті самі кадри, але вже з описом і лише з релевантними списками.
TAG_PROMPT = """You already described this post as: {summary} {description}
On screen: {ocr}

You are looking at the same {frames} frame(s) of that Instagram {kind}, in
chronological order. Media mode: {mode}.

Now pick tags ONLY from the lists below, word for word. Anything not in a list
is discarded, so inventing tags loses information. Precision over coverage.
""" + _TAG_RULES + """

ALLOWED TAGS
{taxonomy}

Answer with JSON only, nothing around it:
{"tags": [{"tag": "<tag>", "evidence": "<3-6 words>"}]}"""

# Стара назва: так інструкцію знає UI («Повернути типову»).
DEFAULT_PROMPT = DESCRIBE_PROMPT

EXAMPLES_HEADER = (
    "EXAMPLES of descriptions the owner approved — match their register, "
    "length and level of detail (do not copy their content):"
)
MAX_EXAMPLES = 3

# Підказка про підбірку. Пост із «Houdini» чи «Tut» — це туторіал, і описувати
# його треба як туторіал: яку техніку показано, а не яка картинка.
TUTORIAL_MARKS = ("tut", "tutorial", "howto", "how-to", "lesson", "breakdown",
                  "houdini", "blender", "c4d", "cinema4d", "unreal", "nuke",
                  "substance", "zbrush", "maya", "after-effects", "ae ")


def prompt_hash(template: str, model: str = "", side: int = 0,
                two_pass: bool = True) -> str:
    """Короткий відбиток інструкції, моделі й розміру кадру.

    Зберігається поруч з описом: після зміни інструкції інакше не дізнатись,
    які описи написані старою, а які — новою. Сторона кадру — теж частина
    політики: описи з кадрів по 400 px і по 900 px різної якості, і «лише
    застарілі» мають уміти їх розрізнити, навіть коли текст інструкції той самий.
    У двокроковому режимі відбиток рахується від ОБОХ текстів: зміна будь-якого
    з них робить старі описи застарілими.
    """
    text = (template or "").strip()
    if two_pass:
        key = (text or DESCRIBE_PROMPT) + "\n--tags--\n" + TAG_PROMPT
    else:
        key = text or SINGLE_PASS_PROMPT
    key += "\n" + (model or "")
    if side:
        key += f"\n{int(side)}px"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def render_examples(examples) -> str:
    lines = []
    for item in list(examples or [])[:MAX_EXAMPLES]:
        text = " ".join(str(item or "").split())
        if text:
            lines.append(f'- "{text[:400]}"')
    if not lines:
        return ""
    return "\n" + EXAMPLES_HEADER + "\n" + "\n".join(lines) + "\n"


def collection_hint(collections) -> str:
    """Рядок контексту про підбірку(и), у яких лежить пост."""
    names = [str(c).strip() for c in (collections or []) if str(c).strip()]
    if not names:
        return ""
    joined = ", ".join(names[:4])
    lowered = joined.lower()
    hint = f"Saved by the owner in collection: {joined}."
    if any(mark in lowered for mark in TUTORIAL_MARKS):
        hint += (" This is likely a tutorial or breakdown: describe WHICH technique "
                 "or workflow is being shown, step by step where visible, not just "
                 "what the picture looks like.")
    return hint

# Стара однокадрова інструкція лишається під рукою: за нею писані відповіді
# без опису й тегів, і вона ж — запасний варіант для дуже дрібних моделей.
PROMPT = SINGLE_PASS_PROMPT


def build_prompt(template: str, frames: int, kind: str, mode: str = "video",
                 taxonomy=None, examples=None, profiles=None, default=None) -> str:
    """Інструкція для моделі. Порожній `template` = `default` (типово — повна
    однозапитна, зі словником); двокроковий режим просить DESCRIBE_PROMPT."""
    text = (template or "").strip() or (default or SINGLE_PASS_PROMPT)
    if taxonomy is not None and "{taxonomy}" in text:
        text = text.replace("{taxonomy}", taxonomy.render(mode, profiles))
    else:
        # Без словника (або в інструкції без плейсхолдера) модель списків не
        # побачить — теги все одно перевіряються кодом.
        text = text.replace("{taxonomy}", "")
    text = text.replace("{examples}", render_examples(examples))
    return (
        text.replace("{frames}", str(max(1, int(frames or 1))))
        .replace("{kind}", kind or "post")
        .replace("{mode}", (mode or "video").upper())
    )


def build_tag_prompt(frames: int, kind: str, mode: str, taxonomy, profiles,
                     summary: str = "", description: str = "", ocr: str = "",
                     template: Optional[str] = None) -> str:
    """Другий запит: опис уже є, лишилось обрати теги зі списків цього поста."""
    text = template or TAG_PROMPT
    text = text.replace("{taxonomy}",
                        taxonomy.render(mode, profiles) if taxonomy is not None else "")
    return (
        text.replace("{summary}", (summary or "").strip())
        .replace("{description}", (description or "").strip())
        .replace("{ocr}", (ocr or "").strip() or "none")
        .replace("{frames}", str(max(1, int(frames or 1))))
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
    summary: str = ""          # одне речення «що це як референс»
    tags: List[str] = field(default_factory=list)
    frames: int = 0
    on_screen_text: str = ""
    transcript: str = ""       # голос за кадром, якщо транскрибували
    # Теги, яких немає у словнику. Не мовчазна втрата, а матеріал для того,
    # щоб словник ріс по реальному контенту.
    dropped: List[str] = field(default_factory=list)
    # Теги, чий «доказ» модель сама спростувала («no visible sneakers»).
    rejected: List[str] = field(default_factory=list)
    # Основа поста за відповіддю моделі (live-action, 3d-render…) і профілі
    # словника, які з неї вийшли, — для журналу й для тестів.
    medium: str = ""
    profiles: tuple = ()
    # Не помилка, а «частину не вдалось»: опис є, а тегів немає. Викликач
    # лише логує — вердикт лишається придатним до збереження.
    warning: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and self.category in CATEGORIES

    @property
    def label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def has_text(self) -> bool:
        """Чи є що покласти у файл, навіть якщо категорія не переконала.

        Самий лише маркер «autotagged» — не теги: словник дописує його завжди,
        і без цієї перевірки порожня відповідь виглядала б як опис.
        """
        real_tags = [t for t in self.tags if t and t != "autotagged"]
        return bool(self.description or self.summary or real_tags)

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
        ttl=int(getattr(cfg, "vision_ttl_seconds", 0) or 0),
    )


class VisionClient:
    def __init__(self, base_url: str = DEFAULT_URL, model: str = "",
                 timeout: int = 60, min_confidence: float = 0.55,
                 prompt: str = "", taxonomy=None, ttl: int = 0):
        self.base_url = normalize_url(base_url)
        self.model = (model or "").strip()
        self.timeout = timeout
        self.min_confidence = min_confidence
        self.prompt = prompt or ""
        self.taxonomy = taxonomy
        self.ttl = int(ttl or 0)
        self.session = requests.Session()
        # Знімок /api/v0/models: тип і контекст моделей. None = ще не питали.
        self._info: Optional[dict] = None

    # ------------------------------------------------------------ вивантаження
    def unload(self) -> str:
        """Просить LM Studio звільнити памʼять під моделлю. Повертає підсумок.

        Модель на 4B важить кілька гігабайт відеопамʼяті, і після проходу вона
        там уже не потрібна — а LM Studio тримає завантажене, доки не скажуть
        інакше. TTL у запиті рятує лише JIT-завантажені моделі; ту, що людина
        завантажила руками у вікні LM Studio, вивантажує тільки це.
        """
        model = (self.model or "").strip()
        if not model:
            return ""
        url = f"{api_root(self.base_url)}/api/v1/models/unload"
        try:
            resp = self.session.post(url, json={"instance_id": model}, timeout=15)
        except requests.RequestException as exc:
            raise VisionError(f"не вдалось звернутись до LM Studio: {_short(exc)}") from exc
        if resp.status_code == 404:
            # v1 з'явився в LM Studio 0.4.0; у старіших вивантаження по HTTP немає.
            raise VisionError(
                "ця версія LM Studio не вміє вивантажувати модель по HTTP "
                "(потрібна 0.4.0+). Допоможе TTL — модель зникне сама.")
        if not resp.ok:
            raise VisionError(f"LM Studio відповів {resp.status_code}")
        return model

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

    def _model_info(self, refresh: bool = False) -> dict:
        """Рідний `/api/v0/models` LM Studio: id → {type, loaded_context_length, …}.

        Це єдине джерело, що знає ТИП моделі (vlm / llm / embeddings) і контекст,
        з яким її справді завантажено. Старіші збірки ендпоінта не мають — тоді
        порожньо, і далі працюють евристика за назвою та типовий розмір кадру.
        """
        if self._info is not None and not refresh:
            return self._info
        info: dict = {}
        try:
            resp = self.session.get(f"{api_root(self.base_url)}/api/v0/models", timeout=10)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("data") if isinstance(body, dict) else None
            for item in items or []:
                if isinstance(item, dict) and item.get("id"):
                    info[str(item["id"])] = item
        except (requests.RequestException, ValueError, AttributeError):
            info = {}
        self._info = info
        return info

    def model_types(self, refresh: bool = False) -> dict:
        """id моделі → тип (`vlm` / `llm` / `embeddings`). Помилка → {}."""
        return {
            model_id: str(item.get("type"))
            for model_id, item in self._model_info(refresh).items()
            if item.get("type")
        }

    def model_type(self, model: str = "") -> str:
        return self.model_types().get((model or self.model or "").strip(), "")

    def context_length(self, model: str = "") -> Optional[int]:
        """З яким контекстом модель завантажена зараз (токени); невідомо → None."""
        item = self._model_info().get((model or self.model or "").strip()) or {}
        try:
            value = int(item.get("loaded_context_length") or 0)
        except (TypeError, ValueError):
            value = 0
        return value if value > 0 else None

    def resolve_model(self) -> str:
        """Якщо модель не вказана — беремо першу завантажену, що виглядає візуальною.

        Перша в списку буває текстовою: тоді картинки летять у порожнечу.
        """
        if self.model:
            return self.model
        models = self.list_models()
        if not models:
            raise VisionError("У LM Studio не завантажено жодної моделі.")
        types = self.model_types()
        visual = [m for m in models if looks_visual(m, types)]
        self.model = (visual or models)[0]
        return self.model

    def warm_up(self, timeout: Optional[float] = None) -> str:
        """Піднімає модель порожнім текстовим запитом. Повертає її назву.

        LM Studio вантажить модель при першому запиті (JIT) — 45–60 с, а то й
        більше. Якщо це перше завантаження трапляється всередині запиту з
        кадрами, він упирається в звичайний таймаут, і пост лишається без опису.
        Тому вантажимо окремо, з щедрим таймаутом, і лише потім шлемо кадри.
        Невдача — VisionError із причиною: викликач вирішує, що з цим робити.
        """
        model = self.resolve_model()
        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": 1,
            "messages": [{"role": "user", "content": "ok"}],
        }
        if self.ttl > 0:
            payload["ttl"] = int(self.ttl)
        wait = float(timeout) if timeout else float(max(int(self.timeout or 0), 300))
        try:
            resp = self.session.post(
                f"{self.base_url}/chat/completions", json=payload, timeout=wait)
        except requests.ConnectionError as exc:
            raise VisionError("LM Studio не відповідає") from exc
        except requests.Timeout as exc:
            raise VisionError(f"модель не завантажилась за {int(wait)} с") from exc
        except requests.RequestException as exc:
            raise VisionError(f"LM Studio: {_short(exc)}") from exc
        if not resp.ok:
            raise VisionError(f"LM Studio відповів {resp.status_code}: {_error_text(resp)}")
        # Після завантаження контекст уже відомий — старий знімок його не мав.
        self._info = None
        return model

    # ---------------------------------------------------------- класифікація
    @staticmethod
    def _context_text(caption: str = "", username: str = "", collections=None,
                      transcript: str = "") -> str:
        context = []
        if username:
            context.append(f"Account: @{username}")
        if caption.strip():
            context.append(f"Caption: {caption.strip()[:400]}")
        hint = collection_hint(collections)
        if hint:
            context.append(hint)
        if transcript and transcript.strip():
            context.append(
                "Voice-over transcript (what the author SAYS; use it to name the "
                f"technique, do not retell it): {transcript.strip()[:1200]}")
        return "\n".join(context)

    def _complete(self, model: str, text: str, shots: Sequence[bytes],
                  max_tokens: int, images_first: bool = False) -> tuple:
        """Один запит до моделі. Повертає (відповідь, помилка): рівно одне з двох.

        `images_first`: зображення ПЕРЕД текстом. LM Studio/llama.cpp кешує
        префікс запиту, тож другий запит двокрокового режиму, що починається з
        тих самих кадрів, не кодує їх удруге.
        """
        pictures = [{"type": "image_url", "image_url": {
            "url": "data:image/jpeg;base64," + base64.b64encode(shot).decode()
        }} for shot in shots]
        words = {"type": "text", "text": text}
        content = pictures + [words] if images_first else [words] + pictures

        payload = {
            "model": model,
            "temperature": 0,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}],
        }
        if self.ttl > 0:
            # LM Studio вивантажує модель через стільки секунд простою. Це
            # страхує на випадок, коли застосунок упав і вивантажити явно не
            # встиг: інакше модель висить у памʼяті до перезапуску LM Studio.
            payload["ttl"] = int(self.ttl)
        try:
            # Кожен кадр — ще кілька секунд кодування, тож запас росте з кількістю.
            resp = self.session.post(
                f"{self.base_url}/chat/completions", json=payload,
                timeout=self.timeout + 2 * len(shots),
            )
            resp.raise_for_status()
            body = resp.json()
        except requests.ConnectionError:
            return "", "LM Studio не відповідає"
        except requests.Timeout:
            return "", "модель не встигла відповісти"
        except requests.RequestException as exc:
            return "", f"LM Studio: {_short(exc)}"
        except ValueError:
            return "", "LM Studio повернув не-JSON відповідь"

        try:
            message = body["choices"][0]["message"]
            answer = message.get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            return "", "несподівана відповідь моделі"
        if isinstance(answer, list):  # деякі збірки віддають список частин
            answer = " ".join(
                part.get("text", "") for part in answer if isinstance(part, dict)
            )
        answer, thought = strip_thinking(str(answer or ""))
        reasoning = message.get("reasoning_content") or message.get("reasoning")
        if not answer.strip() and (thought or reasoning):
            # Модель «думала» і вичерпала відповідь на роздуми. Без цього рядка
            # це виглядало б як «не вдалось розібрати відповідь» — без підказки,
            # що виправляти треба вибір моделі, а не інструкцію.
            return "", THINKING_ERROR
        return answer, ""

    def classify(self, images: Sequence[bytes], caption: str = "",
                 username: str = "", kind: str = "post",
                 mode: str = "", collections=None, examples=None,
                 transcript: str = "") -> VisionVerdict:
        """Один запит: опис і теги разом. Помилки повертає, а не кидає."""
        shots = [img for img in (images or []) if img][:MAX_FRAMES]
        if not shots:
            return VisionVerdict(error="немає зображення")
        try:
            model = self.resolve_model()
        except VisionError as exc:
            return VisionVerdict(error=str(exc))

        mode = mode or ("video" if len(shots) > 1 or kind in ("reel", "video")
                        else "image")
        text = build_prompt(self.prompt, len(shots), kind, mode, self.taxonomy,
                            examples=examples)
        context = self._context_text(caption, username, collections, transcript)
        if context:
            text += "\n\n" + context

        # опис і теги з доказами не влазять у 120 токенів, які вистачало на
        # саму категорію
        answer, error = self._complete(model, text, shots, 1200)
        if error:
            return VisionVerdict(error=error)
        verdict = parse_answer(answer)
        verdict.frames = len(shots)
        # Користь видно з тексту, а не з кадру: «10 websites for…», «how to…».
        # Модель дивиться на картинку й описує студію — ознаку дає текст.
        verdict.tags = list(verdict.tags) + useful_tags(
            verdict.on_screen_text, caption, transcript) + software_tags(
            caption, verdict.on_screen_text, transcript)
        if self.taxonomy is not None:
            # Інструкцію модель порушує, цю перевірку — ні.
            verdict.tags, verdict.dropped = self.taxonomy.normalize(verdict.tags, mode)
        return verdict

    def describe_then_tag(self, images: Sequence[bytes], caption: str = "",
                          username: str = "", kind: str = "post",
                          mode: str = "", collections=None, examples=None,
                          transcript: str = "") -> VisionVerdict:
        """Два запити на один файл: спершу опис і medium, потім теги зі списків
        цього поста.

        Один великий словник на 4B-модель закінчувався загальними словами
        (`cinematic` у 284 описах із 451), а специфіка не добиралась. Тож другий
        запит бачить лише профілі, які код вибрав за medium (`profiles_for`).
        Помилки повертає, а не кидає: впав перший запит — вердикт із помилкою;
        впав другий — опис лишається, а `warning` каже, чого бракує.
        """
        shots = [img for img in (images or []) if img][:MAX_FRAMES]
        if not shots:
            return VisionVerdict(error="немає зображення")
        try:
            model = self.resolve_model()
        except VisionError as exc:
            return VisionVerdict(error=str(exc))

        mode = mode or ("video" if len(shots) > 1 or kind in ("reel", "video")
                        else "image")
        text = build_prompt(self.prompt, len(shots), kind, mode, None,
                            examples=examples, default=DESCRIBE_PROMPT)
        context = self._context_text(caption, username, collections, transcript)
        if context:
            text += "\n\n" + context

        answer, error = self._complete(model, text, shots, 1200, images_first=True)
        if error:
            return VisionVerdict(error=error)
        verdict = parse_answer(answer)
        verdict.frames = len(shots)
        if verdict.error and not verdict.has_text:
            return verdict

        screen = verdict.on_screen_text
        verdict.profiles = profiles_for(
            verdict.medium, mode,
            " ".join(part for part in (verdict.summary, verdict.description, screen) if part))

        tags: List[str] = []
        rejected = list(verdict.rejected)
        if self.taxonomy is not None:
            tag_text = build_tag_prompt(
                len(shots), kind, mode, self.taxonomy, verdict.profiles,
                verdict.summary, verdict.description, screen)
            reply, error = self._complete(model, tag_text, shots, 700, images_first=True)
            parsed = None if error else parse_tags_answer(reply)
            if error:
                verdict.warning = f"теги не отримано: {error}"
            elif parsed is None:
                verdict.warning = "теги не отримано: не вдалось розібрати відповідь"
            else:
                tags, more = parsed
                rejected.extend(more)
        if not tags:
            # Своя інструкція першого запиту могла сама повернути теги.
            tags = list(verdict.tags)
        verdict.rejected = rejected
        tags = tags + useful_tags(screen, caption, transcript) + software_tags(
            caption, screen, transcript)
        if self.taxonomy is not None:
            verdict.tags, verdict.dropped = self.taxonomy.normalize(tags, mode)
        else:
            verdict.tags = tags
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

    # Стара інструкція (і чужі кастомні) називала описом саме «summary»; тепер це
    # окреме поле, тож підміняємо лише коли description порожній.
    summary = _clean_text(data.get("summary") or "")[:300]
    description = _clean_text(data.get("description") or "")
    if not description and not data.get("description"):
        description = _clean_text(data.get("summary") or "")
    tags, rejected = split_tags(data.get("tags"))
    screen = data.get("on_screen_text") or data.get("screen_text") or ""
    if isinstance(screen, (list, tuple)):
        screen = " | ".join(str(part) for part in screen if str(part).strip())
    screen = _clean_text(screen)[:500]

    medium = _clean_medium(data.get("medium"))
    category = str(data.get("category", "")).strip().lower()
    if category not in CATEGORIES:
        # Опис і теги вже є — віддаємо їх разом із помилкою, вони не винні.
        return VisionVerdict(
            error=f"невідома категорія «{category}»" if category else "модель не назвала категорію",
            description=description, summary=summary, tags=tags,
            on_screen_text=screen, rejected=rejected, medium=medium,
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
        summary=summary,
        tags=tags,
        on_screen_text=screen,
        rejected=rejected,
        medium=medium,
    )


MEDIUMS = ("live-action", "3d-render", "2d-animation", "motion-graphics",
           "stop-motion", "screen-recording", "illustration", "photograph",
           "graphic-design", "mixed-media")


def _clean_medium(value) -> str:
    """Основа поста від моделі → одна з MEDIUMS; усе інше — порожньо (невідомо).

    Модель каже «cgi», «3d» чи «photography» — це ті самі синоніми, що й у
    словнику тегів, тож беремо їхню таблицю, а не ведемо другу.
    """
    text = re.sub(r"[\s_]+", "-", str(value or "").strip().lower())
    text = DEFAULT_ALIASES.get(text, text)
    return text if text in MEDIUMS else ""


def parse_tags_answer(text: str) -> Optional[tuple]:
    """Відповідь другого запиту → (теги, відхилені за доказом) або None.

    None — відповідь не розібрати; порожній список тегів — це чесне «нічого не
    підійшло», і це не помилка.
    """
    chunk = _first_json_object((text or "").strip())
    if chunk is None:
        return None
    try:
        data = json.loads(chunk)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return split_tags(data.get("tags"))


THINKING_ERROR = (
    "модель витратила відповідь на роздуми (thinking) і не дійшла до JSON — "
    "обери instruct-модель без thinking або іншу візуальну модель"
)


def strip_thinking(text: str) -> tuple:
    """Вирізає блок `<think>…</think>`. Повертає (текст без нього, чи він був).

    Незакритий `<think>` означає, що відповідь обірвалась посеред роздумів — тоді
    від тексту не лишається нічого. Закриваючий тег без відкриваючого теж
    трапляється: деякі шаблони самі дописують відкриваючий у промпт.
    """
    raw = text or ""
    had = False
    if "</think>" in raw:
        had = True
        raw = raw.rsplit("</think>", 1)[1]
    if "<think>" in raw:
        had = True
        raw = raw.split("<think>", 1)[0]
    return raw.strip(), had


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


def split_tags(value) -> tuple:
    """Теги від моделі → (прийняті, відхилені за доказом).

    Новий формат — обʼєкти `{"tag", "evidence"}`; старі й чужі інструкції
    віддають просто рядки, і вони працюють як раніше. Тег, чий доказ містить
    заперечення («no visible sneakers»), модель сама спростувала — його в
    результат не пускаємо, а лише повідомляємо викликачеві.
    """
    if not isinstance(value, (list, tuple)):
        return clean_tags(value), []
    accepted, rejected = [], []
    for item in value:
        if isinstance(item, dict):
            name = item.get("tag") or item.get("name") or ""
            evidence = str(item.get("evidence") or "")
            if NEGATIVE_EVIDENCE.search(evidence) and not _negative_by_name(name):
                rejected.extend(clean_tags([name]))
                continue
            accepted.append(name)
        else:
            accepted.append(item)
    return clean_tags(accepted), rejected


def _negative_by_name(tag) -> bool:
    """Тег, що сам означає відсутність чогось: «faceless-shot», «no-dialogue».

    Для нього доказ «no face visible» — підтвердження, а не спростування, і
    фільтр заперечень його чіпати не має. Інакше `faceless-shot` відкидався
    саме тоді, коли модель була права.
    """
    name = str(tag or "").strip().lower()
    return "less" in name or name.startswith(("no-", "non-", "without-"))


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


def _error_text(resp, limit: int = 160) -> str:
    """Що LM Studio написала в тілі помилки (там причина зриву завантаження)."""
    try:
        body = resp.json()
        error = body.get("error") if isinstance(body, dict) else None
        text = error.get("message") if isinstance(error, dict) else error
        text = str(text or body)
    except ValueError:
        text = getattr(resp, "text", "") or ""
    text = " ".join(str(text).split())
    return text[:limit] + ("…" if len(text) > limit else "")


def _short(exc: Exception, limit: int = 90) -> str:
    text = str(exc).strip().replace("\n", " ") or exc.__class__.__name__
    return text[:limit] + ("…" if len(text) > limit else "")
