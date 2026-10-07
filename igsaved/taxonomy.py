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

Словник ділиться на ПРОФІЛІ (core / live / 3d / 2d / motion / graphic): один
список на 2400 токенів для 4B-моделі закінчується загальними словами. Тож у
промпт тегів іде лише те, що стосується цього поста (див. `profiles_for`), а
відповідь перевіряє `normalize` по ВСЬОМУ словнику: тег із непоказаної
категорії, який модель назвала сама, лишається валідним.

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

from .useful import SOFTWARE_ALIASES, SOFTWARE_TAGS

MARKER = "autotagged"          # службова позначка «тут модель уже була»
BOTH, VIDEO, IMAGE = "both", "video", "image"

TARGET_MIN, TARGET_MAX = 5, 18

# Профілі. CORE йде в промпт завжди; решта — за типом поста.
CORE, LIVE, P3D, P2D, MOTION, GRAPHIC = "core", "live", "3d", "2d", "motion", "graphic"
PROFILE_ORDER = (CORE, LIVE, P3D, P2D, MOTION, GRAPHIC)
# Службовий «профіль»: категорія поза промптом. Порожній `profiles` означає
# core, тож «ніколи не показувати моделі» треба сказати окремо.
HIDDEN = "none"

# Версія формату файлу словника. Без неї файл, збережений ДО профілів (старі
# категорії, старі синоніми), перекривав би новий вбудований словник — а
# `ensure_file` створює такий файл будь-кому, хто відкрив діалог словника.
FILE_VERSION = 2


@dataclass
class Category:
    key: str
    title: str
    tags: List[str] = field(default_factory=list)
    limit: int = 3            # скільки максимум брати з цієї категорії
    mode: str = BOTH          # both | video | image
    note: str = ""
    # У яких профілях категорія йде в промпт. Порожньо = ("core",).
    profiles: Tuple[str, ...] = ()

    def fits(self, mode: str) -> bool:
        return self.mode in (BOTH, mode)

    @property
    def effective_profiles(self) -> Tuple[str, ...]:
        return tuple(self.profiles) or (CORE,)

    @property
    def visible(self) -> bool:
        """Чи показується категорія моделі взагалі (software — ні)."""
        return HIDDEN not in self.profiles

    def shown_in(self, profiles: Optional[Sequence[str]]) -> bool:
        """Чи потрапляє категорія в промпт для цих профілів (None = усі)."""
        if not self.visible:
            return False
        if profiles is None:
            return True
        return bool(set(self.effective_profiles) & (set(profiles) | {CORE}))


def _c(key: str, title: str, tags: str, limit: int = 2, mode: str = BOTH,
       note: str = "", profiles: Tuple[str, ...] = ()) -> Category:
    return Category(key, title, [t.strip() for t in tags.split(",") if t.strip()],
                    limit=limit, mode=mode, note=note, profiles=profiles)


# --------------------------------------------------------------------------
# Вбудований словник. Власник — 3D-дженераліст і арт-директор, але бібліотека
# це й живе відео (реклама, фешн, кліпи), і 2D, і графдизайн.
# --------------------------------------------------------------------------
DEFAULT_CATEGORIES: List[Category] = [
    # ------------------------------------------------------------- core
    _c("lighting_quality", "LIGHTING QUALITY",
       "hard-light, soft-light, fog-diffusion, volumetric-lighting, high-key, "
       "low-key, chiaroscuro",
       note="high-key = bright, airy, few shadows; low-key = mostly dark. "
            "Pick what dominates."),
    _c("lighting_direction", "LIGHTING DIRECTION",
       "top-light, side-light, backlit, under-light, front-light, rim-light, "
       "silhouette"),
    _c("lighting_source", "LIGHTING SOURCE",
       "natural-daylight, golden-hour, blue-hour, overcast, neon-lit, "
       "practical-lighting, studio-lighting, window-spill, candlelight, firelight, "
       "mixed-lighting"),
    _c("color", "COLOR TREATMENT",
       "warm-tones, cool-tones, monochromatic, desaturated, black-and-white, sepia, "
       "teal-orange, pastel-palette, saturated-colors, muted-palette, high-contrast, "
       "low-contrast"),
    _c("hue", "DOMINANT HUE",
       "red-dominant, orange-dominant, yellow-dominant, green-dominant, "
       "blue-dominant, purple-dominant, pink-dominant, gold-dominant, white-dominant, "
       "dark-dominant",
       note="only when one colour clearly dominates the frame."),
    _c("frame_size", "FRAME SIZE",
       "extreme-close-up, close-up, medium-shot, medium-wide, wide-shot, "
       "extreme-wide, detail-shot"),
    _c("camera_angle", "CAMERA ANGLE",
       "low-angle, high-angle, eye-level, overhead, aerial-shot, dutch-angle, "
       "point-of-view",
       note="aerial-shot means the camera is high above looking down — not just "
            "visible sky."),
    _c("format", "FORMAT", "vertical, square, letterbox, split-screen", limit=1),
    _c("composition", "COMPOSITION",
       "symmetrical, centered, negative-space, leading-lines, frame-within-frame, "
       "layered-composition, minimalist-composition, rule-of-thirds",
       note="only when genuinely striking."),
    _c("mood", "MOOD",
       "dramatic, calm, energetic, playful, eerie, romantic, luxurious, melancholic, "
       "gritty, dreamy"),
    _c("genre", "GENRE / FORMAT",
       "commercial, fashion-film, music-video, short-film, documentary, trailer, "
       "title-sequence, brand-film, social-ad, showreel, interview, "
       "behind-the-scenes, tutorial, art-process, editorial, product-film, "
       "event-recap"),
    _c("subject", "SUBJECT",
       "portrait, group-portrait, hands-only, faceless, architecture, interior, "
       "landscape, cityscape, abstract, product-only, still-life, vehicle-subject, "
       "animal-subject, food-subject, typography-subject"),
    _c("people", "PEOPLE / PERFORMANCE",
       "actor, model, dancer, athlete, crowd, couple, kids, presenter, talking-head",
       note="talking-head ONLY when a person speaks to camera; model = "
            "fashion/beauty model."),
    _c("useful", "USEFUL / INFORMATIONAL",
       "useful, tutorial, tips, resource-list, tool-recommendation, "
       "workflow-breakdown, software-tip, explainer, prompt-share, news-drop, "
       "course-promo, link-in-bio", limit=4,
       note="the post TEACHES or GIVES something: steps, a list of tools or "
            "sites, a settings walkthrough. Always add `useful` together with "
            "the specific one. Not for pretty footage that merely looks nice."),
    _c("origin", "ORIGIN", "ai-generated, ai-assisted", limit=1,
       note="pick only when the AI origin is evident (artifacts, watermark, "
            "caption says so)."),
    _c("medium", "PRIMARY MEDIUM",
       "live-action, 3d-render, 2d-animation, motion-graphics, stop-motion, "
       "screen-recording, illustration, photograph, graphic-design, mixed-media",
       limit=1, note="exactly one foundation."),
    _c("reference_for", "REFERENCE FOR",
       "lighting-reference, camera-move-reference, color-grade-reference, "
       "material-reference, animation-reference, typography-reference, "
       "composition-reference, edit-rhythm-reference",
       note="what a 3D artist or director would open this post for."),

    # ------------------------------------------------------------ live
    _c("movement", "CAMERA MOVEMENT",
       "handheld, steadicam, locked-off, slow-motion, time-lapse, hyperlapse, orbit, "
       "push-in, pull-out, tracking-shot, dolly-zoom, crane, speed-ramp, whip-pan, "
       "rack-focus, crash-zoom, fpv-drone, drone-footage, probe-lens, snorricam",
       mode=VIDEO, profiles=(LIVE,)),
    _c("editing", "EDITING / TRANSITIONS",
       "montage, rapid-cut, single-take, jump-cut, match-cut, zoom-transition, "
       "whip-transition, morph-cut, music-synced-edit, cut-ins, freeze-frame, "
       "reverse-motion", mode=VIDEO, profiles=(LIVE,)),
    _c("lens", "LENS",
       "ultrawide, fisheye, wide-lens, telephoto, macro-lens, anamorphic, tilt-shift",
       limit=1, profiles=(LIVE,)),
    _c("grade", "GRADE / LOOK",
       "film-emulation, bleach-bypass, crushed-blacks, lifted-shadows, grainy, "
       "halation, lens-flare, vignette, light-leaks, vhs-look, found-footage, "
       "polaroid-look, flat-log-look", profiles=(LIVE,)),
    _c("location", "LOCATION",
       "studio, street, rooftop, apartment, car-interior, warehouse, "
       "industrial-space, office, hotel, restaurant, club, stage, gym, pool, beach, "
       "forest, desert, mountain, underwater, nature-location, retail-space, kitchen",
       profiles=(LIVE,)),
    _c("weather", "TIME / WEATHER",
       "day, night-scene, dawn-dusk, rain, snow, fog, haze, storm, sunbeams",
       profiles=(LIVE,)),
    _c("phenomena", "NATURAL PHENOMENA",
       "fire, smoke, explosion, dust, sparks, bubbles, waves, lightning, "
       "water-splash", profiles=(LIVE,)),
    _c("vehicles", "VEHICLES",
       "car, supercar, classic-car, motorcycle, truck, off-road, bicycle, boat, "
       "aircraft, train", profiles=(LIVE,)),
    _c("animals", "ANIMALS",
       "cat, dog, horse, bird, fish, insect, wildlife, fantasy-creature", limit=1,
       note="species not listed → use the general one (fish, bird, insect).",
       profiles=(LIVE,)),
    _c("product", "PRODUCT TYPE",
       "perfume, cosmetics, skincare, electronics, laptop, phone-product, "
       "headphones, watch-product, jewelry-product, eyewear, footwear, apparel, bag, "
       "furniture, beverage-product, packaged-food, toy, art-piece",
       profiles=(LIVE,)),
    _c("content_format", "CONTENT FORMAT",
       "product-in-use, before-after, unboxing, crew-visible, on-set, ootd, "
       "get-ready-with-me, asmr, cooking, dance, sport-action, "
       "transformation-reveal, performance", profiles=(LIVE,)),

    # -------------------------------------------------------------- 3d
    _c("technique_3d", "3D TECHNIQUE",
       "photoreal-3d, stylized-3d, low-poly, toon-shaded, isometric, wireframe, "
       "clay-render, procedural, fluid-simulation, cloth-simulation, "
       "particle-effects, destruction-fx, volumetrics, hard-surface, "
       "character-animation, camera-projection, compositing, motion-blur",
       limit=3, profiles=(P3D,)),
    _c("materials", "MATERIALS",
       "metallic, chrome, glass, liquid, water, fabric, leather, wood, stone, "
       "concrete, plastic, ceramic, paper, holographic, iridescent, translucent, "
       "subsurface-skin, wet-surface, reflective-surface, fur, hair-detail, "
       "carbon-fiber, neon-tube, emissive", limit=3, profiles=(P3D,)),
    _c("design_3d", "3D DESIGN",
       "character-design, creature-design, environment-design, prop-design, "
       "vehicle-design, product-visualization, architectural-visualization, "
       "abstract-3d, logo-animation-3d", profiles=(P3D,)),
    _c("sheet", "REFERENCE SHEETS",
       "character-sheet, environment-sheet, prop-sheet, mood-board, costume-design",
       limit=1, note="only for actual production reference sheets.",
       profiles=(P3D,)),

    # -------------------------------------------------------------- 2d
    _c("style_2d", "2D STYLE",
       "anime, cel-shaded, flat-vector, hand-drawn, frame-by-frame, "
       "cutout-animation, rotoscope, pixel-art, line-art, cartoon, graphic-novel, "
       "watercolor-look, painterly, sketchy, collage, mixed-2d-3d",
       limit=3, profiles=(P2D,)),
    _c("design_2d", "2D DESIGN",
       "character-design, creature-design, background-art, concept-art, storyboard, "
       "comic-panel, book-illustration, editorial-illustration", profiles=(P2D,)),

    # ---------------------------------------------------------- motion
    _c("motion_design", "MOTION DESIGN",
       "kinetic-typography, shape-animation, logo-animation, ui-animation, "
       "infographic-motion, transitions-heavy, morphing, seamless-loop, "
       "glitch-effects, datamosh, text-overlay, data-visualization, motion-blur",
       limit=3, profiles=(MOTION,)),

    # --------------------------------------------------------- graphic
    _c("graphic_design", "GRAPHIC DESIGN",
       "logo-design, branding, poster-design, typography-design, layout-design, "
       "icon-set, packaging-design, editorial-layout, grid-system, type-driven, "
       "brutalist-design, swiss-style, retro-design, y2k-design",
       limit=3, profiles=(GRAPHIC,)),

    # ------------------------------------------- поза промптом: software
    # Заповнюється лише правилами з тексту (useful.software_tags): бренд
    # програми модель із кадру не вгадує, а називала його зі слова в підписі.
    Category("software", "SOFTWARE", tags=list(SOFTWARE_TAGS), limit=4, mode=BOTH,
             profiles=(HIDDEN,)),
]

# Теги, які не можуть стояти поруч: лишається той, що модель назвала першим.
DEFAULT_EXCLUSIVE: List[List[str]] = [
    ["high-key", "low-key"],
    ["hard-light", "soft-light"],
    ["handheld", "locked-off"],
    ["slow-motion", "time-lapse"],
    ["talking-head", "faceless"],
    ["day", "night-scene"],
]

# Не теги й не кандидати: слово-паразит, назви категорій рішення і старі теги
# без близької заміни. Тихо відкидаються: ні в «збережені», ні в «пропозиції
# до словника» (інакше «cinematic» пропонувався б знову й знову).
IGNORED_TAGS = frozenset({
    "cinematic", "cinematic-look", "meme", "art", "ad", "game", "other",
    "gobo-lighting", "three-point-lighting", "color-gels", "ring-light",
    "phone-flashlight", "clean-single", "over-the-shoulder", "ots",
    "profile-shot", "reverse-shot", "double-exposure-edit", "complementary-colors",
    "single-color-dominant", "geometric-composition", "chromatic-aberration",
    "livestock", "spaceship", "lighting-product", "tool", "instrument",
    "supplement", "food-texture", "aurora", "rainbow", "futuristic", "dystopian",
    "fantasy", "sci-fi", "indoor", "outdoor", "screen", "computer", "monitor",
    "display", "machinery", "plant", "action-scene", "app-promo", "vlog",
    "vlog-style", "raw-footage", "polished", "lo-fi-aesthetic", "hi-fi-aesthetic",
    "casino", "real-estate", "gaming",
})

# Найчастіші промахи: модель каже вужче, ніж дозволяє словник.
DEFAULT_ALIASES: Dict[str, str] = {
    # --- спорт, люди, тварини
    "skiing": "sport-action", "snowboarding": "sport-action",
    "surfing": "sport-action", "skateboarding": "sport-action",
    "mountain-biking": "sport-action", "running": "sport-action",
    "fitness": "sport-action", "sports": "sport-action", "dancing": "dance",
    "goldfish": "fish", "shark": "wildlife", "whale": "wildlife",
    "reptile": "wildlife", "marine-life": "wildlife", "chicken": "bird",
    "dragon": "fantasy-creature", "monster": "fantasy-creature",
    # --- 3D / CGI (режимні — у MODE_ALIASES; тут запас для файлу)
    "cgi": "3d-render", "3d-animation": "3d-render", "3d": "3d-render",
    "render": "3d-render", "cgi-render": "3d-render", "cgi-animation": "3d-render",
    "photoreal": "photoreal-3d", "stylized": "stylized-3d",
    "vfx": "compositing", "2d": "2d-animation",
    # --- світло
    "night-time": "night-scene", "nighttime": "night-scene",
    "moonlight": "night-scene", "night": "night-scene", "dusk": "dawn-dusk",
    "sunrise": "golden-hour", "sunset": "golden-hour", "magic-hour": "golden-hour",
    "midday-sun": "natural-daylight", "open-shade": "natural-daylight",
    "neon": "neon-lit", "backlight": "backlit", "backlighting": "backlit",
    "back-light": "backlit", "low-light": "low-key",
    "dramatic-lighting": "dramatic", "studio-lit": "studio-lighting",
    "sodium-streetlamps": "practical-lighting",
    "shopfront-fluorescents": "practical-lighting",
    "led-streetlights": "practical-lighting",
    "motivated-lighting": "practical-lighting", "tungsten": "practical-lighting",
    "fluorescent": "practical-lighting", "direct-flash": "hard-light",
    "reflections": "reflective-surface", "reflection": "reflective-surface",
    "reflected-surface": "reflective-surface",
    # --- колір
    "bw": "black-and-white", "blackandwhite": "black-and-white",
    "black-white": "black-and-white", "monochrome": "monochromatic",
    "vibrant": "saturated-colors", "blue-tones": "blue-dominant",
    "red-tones": "red-dominant", "orange-tones": "orange-dominant",
    "green-tones": "green-dominant", "gold-tones": "gold-dominant",
    "dark-tones": "dark-dominant", "dark-background": "dark-dominant",
    "black-background": "dark-dominant", "white-background": "white-dominant",
    # --- кадр, камера, монтаж
    "closeup": "close-up", "close-up-shot": "close-up",
    "tight-headshot": "extreme-close-up", "headshot": "close-up",
    "upper-body": "medium-shot", "three-quarter-body": "medium-wide",
    "entire-body": "wide-shot", "establishing-shot": "wide-shot",
    "two-shot": "couple", "group-shot": "crowd", "faceless-shot": "faceless",
    "hand-only": "hands-only", "first-person": "point-of-view",
    "pov": "point-of-view", "birds-eye": "overhead", "worms-eye": "low-angle",
    "static-shot": "locked-off", "tripod": "locked-off", "gimbal": "steadicam",
    "arc": "orbit", "dolly": "push-in", "bullet-time": "slow-motion",
    "match-motion": "match-cut", "slowmo": "slow-motion",
    "fast-motion": "time-lapse", "timelapse": "time-lapse",
    "drone": "drone-footage", "loop": "seamless-loop", "boomerang": "seamless-loop",
    "smartphone-vertical": "vertical", "minimal": "minimalist-composition",
    "minimalism": "minimalist-composition", "minimalist": "minimalist-composition",
    # --- плівка й гради
    "film-grain": "grainy", "scanned-film": "film-emulation",
    "anamorphic-flares": "lens-flare", "polaroid": "polaroid-look",
    # --- походження й носій
    "img2vid": "ai-generated", "txt2vid": "ai-generated",
    "ai-artifacts-visible": "ai-generated",
    "digital-art": "illustration", "digitalart": "illustration",
    "digital-painting": "illustration", "traditional-painting": "painterly",
    "sketch": "sketchy", "concept": "concept-art", "anime-style": "anime",
    "photography": "photograph", "photo": "photograph", "photo-edit": "photograph",
    "screenshot": "screen-recording", "infographic": "graphic-design",
    "motiongraphics": "motion-graphics", "motion-graphic": "motion-graphics",
    "mograph": "motion-graphics", "graphic": "graphic-design", "logo": "logo-design",
    "typography": "typography-design", "titles": "text-overlay",
    "ui-overlay": "ui-animation", "character-portrait": "portrait",
    "posed-portrait": "portrait", "candid-portrait": "portrait",
    "walk-cycle": "character-animation",
    # --- жанр
    "car-commercial": "commercial", "advertising": "commercial",
    "commercial-style": "commercial", "documentary-style": "documentary",
    "editorial-style": "editorial", "editorial-content": "editorial",
    "software-demo": "tutorial", "demonstration": "tutorial",
    "design-process": "art-process", "crafting": "art-process",
    # --- предмети, продукт, місця
    "sneakers": "footwear", "sneaker": "footwear", "shoe-product": "footwear",
    "clothing": "apparel", "garment": "apparel", "makeup": "cosmetics",
    "beauty": "cosmetics", "tech-product": "electronics",
    "supercars": "supercar", "helicopter": "aircraft", "yacht": "boat",
    "automotive": "vehicle-subject", "food-dish": "food-subject",
    "food-beverage": "food-subject", "food-styling": "food-subject",
    "skin-detail": "subsurface-skin", "transparent": "translucent",
    "sculpture": "art-piece", "mist": "fog", "workshop": "industrial-space",
    "garage": "industrial-space", "hospitality": "hotel",
    "travel": "nature-location", "nature": "nature-location", "urban": "street",
}
# Софт: b3d, c4d, ae, ue5, sd, mj… — спільна таблиця з правилами з тексту.
DEFAULT_ALIASES.update(SOFTWARE_ALIASES)


# Режимні синоніми. Загальний alias не знає режиму: `fashion` у відео — це
# жанр, а в картинці — людина. Вбудовані й не читаються з taxonomy.json: файл
# користувача зберігає старий мапінг і інакше знову викидав би тег
# (`3d-render` → `3d-animation`, якого більше нема). Ідентичні записи
# (`3d-render` → `3d-render`) — навмисні: вони стоять попереду стороннього alias.
_THREE_D = {
    "3d-render": "3d-render", "cgi": "3d-render", "3d-animation": "3d-render",
    "3d": "3d-render", "render": "3d-render", "cgi-render": "3d-render",
    "cgi-animation": "3d-render",
}
MODE_ALIASES: Dict[str, Dict[str, str]] = {
    IMAGE: {**_THREE_D, "fashion": "model"},
    VIDEO: {**_THREE_D, "fashion": "fashion-film"},
}


def clean_token(text: str) -> str:
    """Один вигляд тегу: малі літери, дефіси, без ґраток і зайвого."""
    tag = str(text or "").strip().strip("#").strip().lower()
    tag = re.sub(r"[\s_]+", "-", tag)
    tag = re.sub(r"[^a-z0-9\-]", "", tag)
    tag = re.sub(r"-{2,}", "-", tag).strip("-")
    return tag


# ----------------------------------------------------------------- профілі
# medium (відповідь моделі на першому запиті) → які списки показати на другому.
MEDIUM_PROFILES: Dict[str, Tuple[str, ...]] = {
    "live-action": (CORE, LIVE),
    "photograph": (CORE, LIVE),
    "3d-render": (CORE, P3D, LIVE),
    "stop-motion": (CORE, P3D, LIVE),
    "2d-animation": (CORE, P2D),
    "illustration": (CORE, P2D),
    "motion-graphics": (CORE, MOTION, GRAPHIC),
    "screen-recording": (CORE, MOTION, GRAPHIC),
    "graphic-design": (CORE, GRAPHIC),
}
UNKNOWN_MEDIUM_PROFILES = (CORE, LIVE, P3D, MOTION)

# Дешева евристика поверх medium: слова цілком (не підрядки — «art» у
# «gamestart» уже був багом), у описі й тексті з кадру.
_WORDS_3D = re.compile(r"\b(?:3d|renders?|rendered|rendering|cgi|blender|houdini|c4d)\b",
                       re.IGNORECASE)
_WORDS_GRAPHIC = re.compile(r"\b(?:logos?|typography|posters?|branding)\b",
                            re.IGNORECASE)


def profiles_for(medium: str, mode: str = VIDEO, text: str = "") -> Tuple[str, ...]:
    """Профілі словника для поста: код вирішує, модель лише називає medium.

    `mode` поки не змінює вибір (слайд каруселі може бути фото, ролик — 3D), але
    лишається в підписі: рішення за режимом — це місце, куди воно вже вписане.
    """
    chosen = set(MEDIUM_PROFILES.get(clean_token(medium), UNKNOWN_MEDIUM_PROFILES))
    if text and _WORDS_3D.search(text):
        chosen.add(P3D)
    if text and _WORDS_GRAPHIC.search(text):
        chosen.add(GRAPHIC)
    return tuple(p for p in PROFILE_ORDER if p in chosen)


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
        self._index: Dict[str, List[Category]] = {}
        self._reindex()

    def _reindex(self) -> None:
        self._index = {}
        for category in self.categories:
            for tag in category.tags:
                # Один тег може жити в кількох категоріях (motion-blur — і в
                # 3D TECHNIQUE, і в MOTION DESIGN): тримаємо всі, інакше тег
                # видно лише в режимі першої категорії.
                self._index.setdefault(tag, []).append(category)

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

    def category_labels(self) -> List[Tuple[str, str]]:
        """Назви для списків у UI: профіль у дужках, щоб було видно, де категорія йде."""
        labels = []
        for category in self.categories:
            profile = "" if CORE in category.effective_profiles and category.visible \
                else " (" + ("поза промптом" if not category.visible
                             else ", ".join(category.effective_profiles)) + ")"
            labels.append((category.key, category.title + profile))
        return labels

    def find_category(self, key: str) -> Optional[Category]:
        return next((c for c in self.categories if c.key == key), None)

    # ------------------------------------------------------- для інструкції
    def render(self, mode: str = VIDEO, profiles: Optional[Sequence[str]] = None) -> str:
        """Списки тегів у вигляді, який іде в інструкцію моделі.

        Без `profiles` — усі видимі категорії (сумісність); зі списком — лише
        ті, що мають перетин із ним або з core.
        """
        blocks: List[str] = []
        for category in self.categories:
            if not category.fits(mode) or not category.tags:
                continue
            if not category.shown_in(profiles):
                continue
            head = f"{category.title} — pick 0-{category.limit}"
            if category.note:
                head += f"  ({category.note})"
            blocks.append(head + "\n" + ", ".join(category.tags))
        return "\n\n".join(blocks)

    def _fits(self, tag: str, mode: str) -> bool:
        return any(c.fits(mode) for c in self._index.get(tag, ()))

    def _resolve(self, tag: str, mode: str) -> str:
        """Сирий тег → словниковий з огляду на режим."""
        mapped = MODE_ALIASES.get(mode, {}).get(tag)
        if mapped:
            return mapped
        target = self.aliases.get(tag, tag)
        if target != tag and not self._fits(target, mode) and self._fits(tag, mode):
            # Ціль alias не існує в цьому режимі, а сирий тег існує — він і є
            # правильним, не варто його втрачати.
            return tag
        return target

    # ------------------------------------------------------- перевірка
    def normalize(self, tags: Sequence[str], mode: str = VIDEO,
                  add_marker: bool = True) -> Tuple[List[str], List[str]]:
        """Лишає тільки словникові теги. Повертає (взяті, відкинуті).

        Саме тут словник із побажання стає правилом: інструкцію модель час
        від часу порушує, а цю перевірку — ні. Профілів тут не знають: тег із
        категорії, якої модель не бачила, але назвала сама, лишається валідним.
        """
        kept: List[str] = []
        dropped: List[str] = []
        used: Dict[str, int] = {}
        blocked: set = set()

        for raw in tags or []:
            tag = clean_token(raw)
            if not tag or tag == MARKER or tag in IGNORED_TAGS:
                continue
            tag = self._resolve(tag, mode)
            if tag in kept or tag in IGNORED_TAGS:
                continue

            candidates = self._index.get(tag)
            if not candidates:
                dropped.append(tag)
                continue
            # Перша категорія, що існує в цьому режимі; ліміт — по ній.
            category = next((c for c in candidates if c.fits(mode)), None)
            if category is None:
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
            "version": FILE_VERSION,
            "categories": [
                {"key": c.key, "title": c.title, "limit": c.limit,
                 "mode": c.mode, "note": c.note, "profiles": list(c.profiles),
                 "tags": list(c.tags)}
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
        if int(data.get("version") or 1) < FILE_VERSION:
            # Файл збережено до профілів: його категорії й синоніми — це старий
            # вбудований словник, а не правки власника. Брати його означало б
            # назавжди лишити cinematic, студію й жодного 2D.
            raise ValueError("словник у файлі застарів — беремо вбудований")
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
                profiles=tuple(str(p) for p in (item.get("profiles") or [])),
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
        """Читає словник із файлу; зіпсований, застарілий або відсутній — вбудований."""
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
        """Файл словника має існувати, щоб його можна було відкрити й правити.

        Застарілий (до профілів) файл відкладається в taxonomy.pre-profiles.json
        і замінюється вбудованим: інакше діалог показував би власнику словник,
        якого застосунок уже не використовує.
        """
        if path is None:
            from .config import app_dir

            path = app_dir() / "taxonomy.json"
        path = Path(path)
        stale = False
        if path.exists():
            try:
                stale = int(json.loads(path.read_text(encoding="utf-8")).get("version") or 1) \
                    < FILE_VERSION
            except (json.JSONDecodeError, OSError, ValueError, TypeError, AttributeError):
                stale = False
        if stale:
            # Нічого не видаляємо без згоди: власні теги власника лишаються в
            # копії поруч, а не пропадають разом із застарілим файлом.
            backup = path.with_name(path.stem + ".pre-profiles" + path.suffix)
            if not backup.exists():
                os.replace(path, backup)
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
