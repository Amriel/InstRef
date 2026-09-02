"""Контрольований словник тегів для бібліотеки референсів.

Модель не вигадує теги, а обирає зі списків. Це головне, що відрізняє
бібліотеку, у якій щось знаходиться, від звалища синонімів: `3d-render`,
`3drender`, `3D Render` і `render` — це чотири різні теги для Eagle, і жоден
із них не знайде решту.

Просити модель «дотримуйся списку» недостатньо — вона все одно час від часу
щось вигадає. Тому список тут ще й ЗАСТОСОВУЄТЬСЯ кодом: усе, чого в ньому
немає, відкидається після відповіді. Відкинуте не зникає безслідно — воно
рахується, і те, що модель пропонує раз за разом, застосунок пропонує додати
в словник свідомо, одним кліком.

Словник лежить у taxonomy.json поруч із config.json — його можна правити
руками. Порожній або зіпсований файл означає «взяти вбудований».
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

MARKER = "autotagged"          # службова позначка «тут модель уже була»
BOTH, VIDEO, IMAGE = "both", "video", "image"

TARGET_MIN, TARGET_MAX = 5, 18


@dataclass
class Category:
    key: str
    title: str
    tags: List[str] = field(default_factory=list)
    limit: int = 3            # скільки максимум брати з цієї категорії
    mode: str = BOTH          # both | video | image
    note: str = ""

    def fits(self, mode: str) -> bool:
        return self.mode in (BOTH, mode)


# --------------------------------------------------------------------------
# Вбудований словник. Зібраний під конкретну задачу: референси реклами, моди,
# автомобілів, CGI/AI та моушену для 3D-дженералиста й арт-директора.
# --------------------------------------------------------------------------
DEFAULT_CATEGORIES: List[Category] = [
    Category("lighting_source", "LIGHTING SOURCE", limit=3, tags=[
        "golden-hour", "blue-hour", "magic-hour", "midday-sun", "overcast",
        "open-shade", "moonlight", "candlelight", "firelight", "neon-lit",
        "sodium-streetlamps", "led-streetlights", "shopfront-fluorescents",
        "phone-flashlight", "ring-light", "window-spill", "tungsten",
        "fluorescent", "practical-lighting", "natural-daylight", "mixed-lighting",
    ]),
    Category("lighting_quality", "LIGHTING QUALITY", limit=3, tags=[
        "hard-light", "soft-light", "fog-diffusion", "volumetric-lighting",
        "high-key", "low-key", "chiaroscuro",
    ]),
    Category("lighting_direction", "LIGHTING DIRECTION", limit=3, tags=[
        "top-light", "side-light", "back-light", "under-light", "front-light",
        "rim-light", "backlit", "silhouette",
    ]),
    Category("lighting_setup", "LIGHTING SETUP", limit=3, tags=[
        "three-point-lighting", "color-gels", "direct-flash", "gobo-lighting",
        "motivated-lighting",
    ]),
    Category("color", "COLOR", limit=2, tags=[
        "warm-tones", "cool-tones", "monochromatic", "desaturated",
        "black-and-white", "sepia", "teal-orange", "single-color-dominant",
        "complementary-colors", "pastel-palette", "saturated-colors",
        "muted-palette", "vibrant", "high-contrast",
    ]),
    Category("frame_size", "FRAME SIZE", limit=3, tags=[
        "extreme-close-up", "tight-headshot", "headshot", "close-up",
        "medium-shot", "upper-body", "three-quarter-body", "entire-body",
        "wide-shot", "extreme-wide",
    ]),
    Category("shot_type", "SHOT TYPE", limit=3, tags=[
        "clean-single", "two-shot", "group-shot", "over-the-shoulder",
        "point-of-view", "profile-shot", "faceless-shot", "establishing-shot",
        "detail-shot", "reverse-shot",
    ]),
    Category("camera_angle", "CAMERA ANGLE", limit=3, tags=[
        "low-angle", "high-angle", "overhead", "aerial-shot", "worms-eye",
        "dutch-angle", "eye-level",
    ], note="aerial-shot means the camera is high above looking down — not just visible sky."),
    Category("lens", "LENS", limit=3, tags=[
        "ultrawide", "fisheye", "wide-lens", "telephoto", "macro-lens",
        "anamorphic", "tilt-shift",
    ]),
    Category("movement", "CAMERA MOVEMENT", limit=2, mode=VIDEO, tags=[
        "handheld", "gimbal", "locked-off", "slow-motion", "time-lapse",
        "hyperlapse", "whip-pan", "orbit", "push-in", "pull-out",
        "tracking-shot", "dolly", "dolly-zoom", "crane", "arc", "speed-ramp",
        "reverse-motion", "freeze-frame", "bullet-time",
    ]),
    Category("format", "FORMAT", limit=1, tags=[
        "vertical", "square", "letterbox", "split-screen",
    ]),
    Category("editing", "EDITING", limit=2, mode=VIDEO, tags=[
        "montage", "rapid-cut", "single-take", "jump-cut", "match-cut",
        "match-motion", "cut-ins", "double-exposure-edit",
    ]),
    Category("composition", "COMPOSITION", limit=3, tags=[
        "symmetrical", "centered", "negative-space", "leading-lines",
        "frame-within-frame", "layered-composition", "minimalist-composition",
        "geometric-composition",
    ], note="only when the composition is genuinely striking."),
    Category("medium_video", "PRIMARY MEDIUM", limit=1, mode=VIDEO, tags=[
        "live-action", "cgi", "ai-generated", "2d-animation", "3d-animation",
        "motion-graphics", "stop-motion", "screen-recording", "mixed-media",
    ], note="pick exactly one foundation."),
    Category("medium_image", "PRIMARY MEDIUM", limit=1, mode=IMAGE, tags=[
        "photograph", "cgi", "ai-generated", "3d-render", "illustration",
        "digital-painting", "traditional-painting", "sketch", "concept-art",
        "collage", "photo-edit", "screenshot", "infographic", "poster-design",
        "mixed-media",
    ], note="pick exactly one foundation."),
    Category("technique", "ADDITIONAL TECHNIQUE", limit=3, tags=[
        "photoreal-3d", "stylized-3d", "low-poly", "toon-shaded", "isometric",
        "wireframe", "compositing", "img2vid", "txt2vid", "morphing",
        "seamless-loop", "ai-artifacts-visible", "particle-effects",
        "text-overlay", "kinetic-typography", "transitions-heavy",
        "glitch-effects", "datamosh", "film-grain", "halation", "lens-flare",
        "anamorphic-flares", "chromatic-aberration", "vignette", "light-leaks",
        "smartphone-vertical", "drone-footage", "vhs-look", "found-footage",
        "polaroid", "scanned-film",
    ]),
    Category("sheet", "REFERENCE SHEETS", limit=1, tags=[
        "character-sheet", "environment-sheet", "prop-sheet", "mood-board",
        "costume-design",
    ], note="only for actual production reference sheets."),
    Category("content", "CONTENT CATEGORY", limit=2, tags=[
        "automotive", "fashion", "beauty", "food-beverage", "tech-product",
        "fitness", "gaming", "music-video", "sports", "tutorial",
        "behind-the-scenes", "vlog", "art-process", "design-process",
        "real-estate", "hospitality", "travel", "advertising",
        "editorial-content", "casino",
    ]),
    Category("subject", "SUBJECT", limit=2, tags=[
        "portrait", "group-portrait", "hands-only", "faceless", "architecture",
        "interior", "landscape", "cityscape", "abstract", "product-only",
        "food-styling", "still-life", "posed-portrait", "candid-portrait",
    ]),
    Category("animals", "ANIMALS", limit=2, tags=[
        "cat", "dog", "horse", "bird", "fish", "insect", "reptile",
        "marine-life", "wildlife", "livestock", "fantasy-creature", "dragon",
        "monster", "chicken",
    ], note="species not listed → use the general one (fish, bird, insect)."),
    Category("vehicles", "VEHICLES", limit=2, tags=[
        "car", "motorcycle", "truck", "supercar", "classic-car", "off-road",
        "bicycle", "boat", "yacht", "aircraft", "helicopter", "train", "spaceship",
    ]),
    Category("product", "PRODUCT TYPE", limit=2, tags=[
        "perfume", "cosmetics", "skincare", "electronics", "laptop",
        "phone-product", "headphones", "watch-product", "jewelry-product",
        "eyewear", "footwear", "apparel", "bag", "furniture", "lighting-product",
        "tool", "instrument", "beverage-product", "packaged-food", "supplement",
        "toy", "art-piece",
    ]),
    Category("materials", "MATERIALS", limit=2, tags=[
        "metallic", "chrome", "glass", "liquid", "water", "fabric", "leather",
        "wood", "stone", "concrete", "plastic", "ceramic", "paper",
        "holographic", "iridescent", "transparent", "translucent",
        "wet-surface", "reflective-surface", "fur", "hair-detail",
        "food-texture", "skin-detail",
    ]),
    Category("phenomena", "NATURAL PHENOMENA", limit=2, tags=[
        "fog", "mist", "rain", "snow", "lightning", "fire", "smoke",
        "explosion", "dust", "sparks", "bubbles", "waves", "sunbeams",
        "aurora", "rainbow",
    ]),
    Category("environment", "ENVIRONMENT", limit=2, tags=[
        "nature", "urban", "forest", "desert", "beach", "mountain",
        "underwater", "futuristic", "dystopian", "fantasy", "sci-fi", "kitchen",
        "workshop", "garage", "retail-space", "restaurant", "club", "gym",
        "stage", "industrial-space", "indoor", "outdoor",
    ]),
    Category("key_object", "KEY OBJECT", limit=2, tags=[
        "food-dish", "screen", "sneaker", "garment", "machinery", "plant",
        "sculpture",
    ]),
    Category("behavior", "SUBJECT BEHAVIOR", limit=2, mode=VIDEO, tags=[
        "talking-head", "asmr", "cooking", "crafting", "demonstration", "dance",
        "sport-action", "presenter", "transformation-reveal", "performance",
        "walk-cycle", "action-scene",
    ], note="skiing / snowboarding / mountain-biking → sport-action."),
    Category("content_format", "CONTENT FORMAT", limit=2, tags=[
        "software-demo", "app-promo", "ui-overlay", "seamless-loop", "boomerang",
        "crew-visible", "on-set", "product-in-use", "before-after", "unboxing",
        "ootd", "get-ready-with-me",
    ]),
    Category("aesthetic", "AESTHETIC", limit=1, tags=[
        "cinematic", "documentary-style", "vlog-style", "editorial-style",
        "commercial-style", "raw-footage", "polished", "lo-fi-aesthetic",
        "hi-fi-aesthetic",
    ]),
]

# Теги, які не можуть стояти поруч: лишається той, що модель назвала першим.
DEFAULT_EXCLUSIVE: List[List[str]] = [
    ["cgi", "3d-animation", "ai-generated"],
    ["talking-head", "faceless-shot"],
    ["txt2vid", "img2vid"],
    ["handheld", "locked-off"],
    ["slow-motion", "time-lapse"],
    ["hard-light", "soft-light"],
    ["indoor", "outdoor"],
    ["high-key", "low-key"],
]

# Найчастіші промахи: модель каже вужче, ніж дозволяє словник.
DEFAULT_ALIASES: Dict[str, str] = {
    "skiing": "sport-action",
    "snowboarding": "sport-action",
    "surfing": "sport-action",
    "skateboarding": "sport-action",
    "mountain-biking": "sport-action",
    "running": "sport-action",
    "goldfish": "fish",
    "shark": "marine-life",
    "whale": "marine-life",
    "computer": "screen",
    "monitor": "screen",
    "display": "screen",
    "3d": "3d-animation",
    "3d-render": "3d-animation",
    "render": "3d-animation",
    "cgi-animation": "cgi",
    "vfx": "compositing",
    "typography": "text-overlay",
    "titles": "text-overlay",
    "closeup": "close-up",
    "close-up-shot": "close-up",
    "bw": "black-and-white",
    "monochrome": "monochromatic",
    "night": "moonlight",
    "sunset": "golden-hour",
    "sunrise": "golden-hour",
    "neon": "neon-lit",
    "car-commercial": "automotive",
    "supercars": "supercar",
    "sneakers": "sneaker",
    "clothing": "apparel",
    "makeup": "cosmetics",
    "drone": "drone-footage",
    "loop": "seamless-loop",
    "slowmo": "slow-motion",
    "timelapse": "time-lapse",
    "pov": "point-of-view",
    "ots": "over-the-shoulder",
    "birds-eye": "overhead",
    # Промахи, які модель робила раз за разом на реальній бібліотеці.
    "backlight": "backlit",
    "backlighting": "backlit",
    "photography": "photograph",
    "photo": "photograph",
    "motiongraphics": "motion-graphics",
    "motion-graphic": "motion-graphics",
    "mograph": "motion-graphics",
    "blackandwhite": "black-and-white",
    "black-white": "black-and-white",
    "minimalist": "minimalist-composition",
    "minimal": "minimalist-composition",
    "minimalism": "minimalist-composition",
}


def clean_token(text: str) -> str:
    """Один вигляд тегу: малі літери, дефіси, без ґраток і зайвого."""
    tag = str(text or "").strip().strip("#").strip().lower()
    tag = re.sub(r"[\s_]+", "-", tag)
    tag = re.sub(r"[^a-z0-9\-]", "", tag)
    tag = re.sub(r"-{2,}", "-", tag).strip("-")
    return tag


class Taxonomy:
    """Словник тегів: рендерить інструкцію для моделі й перевіряє відповідь."""

    def __init__(self, categories: Optional[List[Category]] = None,
                 aliases: Optional[Dict[str, str]] = None,
                 exclusive: Optional[List[List[str]]] = None):
        self.categories = categories if categories is not None else \
            [Category(**{**c.__dict__, "tags": list(c.tags)}) for c in DEFAULT_CATEGORIES]
        self.aliases = dict(aliases if aliases is not None else DEFAULT_ALIASES)
        self.exclusive = [list(group) for group in
                          (exclusive if exclusive is not None else DEFAULT_EXCLUSIVE)]
        self._index: Dict[str, Category] = {}
        self._reindex()

    def _reindex(self) -> None:
        self._index = {}
        for category in self.categories:
            for tag in category.tags:
                # Один тег може жити в кількох категоріях (наприклад cgi):
                # для перевірки достатньо першої, ліміти рахуємо по ній же.
                self._index.setdefault(tag, category)

    # ------------------------------------------------------------ читання
    def known(self, tag: str) -> bool:
        return tag in self._index

    def all_tags(self, mode: str = BOTH) -> List[str]:
        tags: List[str] = []
        for category in self.categories:
            if mode == BOTH or category.fits(mode):
                tags.extend(category.tags)
        return tags

    def category_titles(self) -> List[Tuple[str, str]]:
        return [(c.key, c.title) for c in self.categories]

    def find_category(self, key: str) -> Optional[Category]:
        return next((c for c in self.categories if c.key == key), None)

    # ------------------------------------------------------- для інструкції
    def render(self, mode: str = VIDEO) -> str:
        """Списки тегів у вигляді, який іде в інструкцію моделі."""
        blocks: List[str] = []
        for category in self.categories:
            if not category.fits(mode) or not category.tags:
                continue
            head = f"{category.title} — pick 0-{category.limit}"
            if category.note:
                head += f"  ({category.note})"
            blocks.append(head + "\n" + ", ".join(category.tags))
        return "\n\n".join(blocks)

    # ------------------------------------------------------- перевірка
    def normalize(self, tags: Sequence[str], mode: str = VIDEO,
                  add_marker: bool = True) -> Tuple[List[str], List[str]]:
        """Лишає тільки словникові теги. Повертає (взяті, відкинуті).

        Саме тут словник із побажання стає правилом: інструкцію модель час
        від часу порушує, а цю перевірку — ні.
        """
        kept: List[str] = []
        dropped: List[str] = []
        used: Dict[str, int] = {}
        blocked: set = set()

        for raw in tags or []:
            tag = clean_token(raw)
            if not tag or tag == MARKER:
                continue
            tag = self.aliases.get(tag, tag)
            if tag in kept:
                continue

            category = self._index.get(tag)
            if category is None:
                dropped.append(tag)
                continue
            if not category.fits(mode):
                # Наприклад handheld на нерухомій картинці — не помилка моделі,
                # а те, чого в цьому режимі просто не буває.
                dropped.append(tag)
                continue
            if tag in blocked:
                continue
            if used.get(category.key, 0) >= category.limit:
                continue

            kept.append(tag)
            used[category.key] = used.get(category.key, 0) + 1
            for group in self.exclusive:
                if tag in group:
                    blocked.update(item for item in group if item != tag)

        if add_marker:
            kept.append(MARKER)
        return kept, dropped

    # --------------------------------------------------------- редагування
    def add(self, tag: str, category_key: str) -> bool:
        tag = clean_token(tag)
        category = self.find_category(category_key)
        if not tag or category is None or tag in category.tags:
            return False
        category.tags.append(tag)
        self._reindex()
        return True

    # ------------------------------------------------------------------ IO
    def to_dict(self) -> dict:
        return {
            "categories": [
                {"key": c.key, "title": c.title, "limit": c.limit,
                 "mode": c.mode, "note": c.note, "tags": list(c.tags)}
                for c in self.categories
            ],
            "aliases": dict(self.aliases),
            "exclusive": [list(g) for g in self.exclusive],
        }

    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def from_dict(cls, data: dict) -> "Taxonomy":
        categories = []
        for item in data.get("categories") or []:
            tags = [clean_token(t) for t in item.get("tags") or []]
            categories.append(Category(
                key=str(item.get("key") or ""),
                title=str(item.get("title") or item.get("key") or ""),
                tags=[t for t in tags if t],
                limit=int(item.get("limit") or 3),
                mode=str(item.get("mode") or BOTH),
                note=str(item.get("note") or ""),
            ))
        if not categories:
            raise ValueError("у словнику немає жодної категорії")
        # Вбудовані синоніми — підкладка під файл: нові промахи, які ми
        # навчились мапити, мають діяти і в тих, хто зберіг словник раніше.
        # Свій запис у файлі має перевагу.
        aliases = dict(DEFAULT_ALIASES)
        aliases.update({
            clean_token(k): clean_token(v)
            for k, v in (data.get("aliases") or {}).items()
        })
        exclusive = [[clean_token(t) for t in group]
                     for group in (data.get("exclusive") or [])]
        return cls(categories, aliases, exclusive)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Taxonomy":
        """Читає словник із файлу; зіпсований або відсутній — беремо вбудований."""
        if path is None:
            from .config import app_dir

            path = app_dir() / "taxonomy.json"
        path = Path(path)
        if path.exists():
            try:
                return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError, ValueError, TypeError):
                pass
        return cls()

    @classmethod
    def ensure_file(cls, path: Optional[Path] = None) -> Path:
        """Файл словника має існувати, щоб його можна було відкрити й правити."""
        if path is None:
            from .config import app_dir

            path = app_dir() / "taxonomy.json"
        path = Path(path)
        if not path.exists():
            cls().save(path)
        return path


def mode_for(path_or_kind) -> str:
    """VIDEO чи IMAGE — від цього залежить половина категорій."""
    text = str(path_or_kind or "").lower()
    if text.endswith((".mp4", ".m4v", ".mov", ".webm", ".mkv", ".gif")):
        return VIDEO
    if text in ("reel", "video", "clips"):
        return VIDEO
    return IMAGE
