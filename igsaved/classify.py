"""Груба класифікація пролайканих постів: мем / арт / реклама / під питанням.

Чесно про межі методу: справжнього сигналу «це мем» в Instagram немає, тож
рішення будується на хештегах, тексті підпису, імені акаунта, тривалості та
позначці платної співпраці. Це працює добре на явних випадках і плутається на
межових — саме тому все непевне їде в ревʼю, а не видаляється.

Найточніший інструмент тут — списки акаунтів: вони мають абсолютний пріоритет
над правилами і поповнюються з вікна ревʼю одним кліком.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

DOWNLOAD = "download"
SKIP = "skip"
REVIEW = "review"

# --------------------------------------------------------------------------
# Типові набори. Користувач може дописати свої в налаштуваннях.
# --------------------------------------------------------------------------
MEME_TAGS = [
    "meme", "memes", "memepage", "dankmemes", "dank", "shitpost", "funny", "funnyvideos",
    "humor", "humour", "comedy", "joke", "jokes", "lol", "lmao", "relatable", "fail",
    "мем", "меми", "мемы", "мемчики", "прикол", "приколы", "приколи", "жиза", "ржака",
    "юмор", "гумор", "шутка", "смешно", "смішно",
]
MEME_ACCOUNT_HINTS = [
    "meme", "memes", "9gag", "humor", "humour", "funny", "lol", "prikol", "jokes",
    "comedy", "shitpost", "dank", "мем", "прикол",
]
ART_TAGS = [
    "art", "artist", "artwork", "digitalart", "illustration", "illustrator", "drawing",
    "painting", "sketch", "conceptart", "characterdesign", "3d", "3dart", "3dartist",
    "cgi", "cg", "render", "rendering", "blender", "c4d", "cinema4d", "houdini",
    "octane", "redshift", "unreal", "vfx", "animation", "motion", "motiondesign",
    "motiongraphics", "graphicdesign", "design", "typography", "branding", "editorial",
    "photography", "photographer", "portrait", "cinematography", "architecture",
    "interior", "product", "aiart", "midjourney", "generative", "creativecoding",
    "арт", "дизайн", "ілюстрація", "иллюстрация", "анімація", "анимация", "рендер",
    "типографіка", "фотографія", "архітектура",
]
# Свідомо без «media», «works», «lab» — надто загальні навіть як окремі слова.
ART_ACCOUNT_HINTS = [
    "studio", "studios", "art", "arts", "artist", "design", "designs", "designer",
    "motion", "visual", "visuals", "creative", "cgi", "3d", "film", "films",
    "atelier", "graphics", "render", "photo", "photography",
]
AD_TAGS = [
    "ad", "ads", "advert", "advertising", "sponsored", "paidpartnership", "promo",
    "promotion", "commercial", "campaign", "brand", "branded", "collab",
    "реклама", "рекламa", "промо", "кампанія", "кампания",
]
AD_PHRASES = [
    "paid partnership", "sponsored by", "in partnership with", "#ad", "у співпраці",
    "рекламний", "рекламная", "at замовлення",
]

_HASHTAG = re.compile(r"#([0-9A-Za-z_Ѐ-ӿÀ-ɏ]{2,40})")
_NAME_SPLIT = re.compile(r"[^a-z0-9]+")


def name_hit(name: str, hints: Sequence[str]) -> Optional[str]:
    """Шукає підказку в імені акаунта як ОКРЕМЕ слово, а не як підрядок.

    Підрядковий пошук давав грубі промахи: «art» сидить усередині gamestart,
    spartan, parts — і ігровий ролик отримував бал на користь арту.
    Тому: збіг цілим токеном або на межі слова.
    """
    lowered = (name or "").lower()
    if not lowered:
        return None
    tokens = {t for t in _NAME_SPLIT.split(lowered) if t}
    for hint in hints:
        hint = hint.lower()
        if hint in tokens:
            return hint
        # на межі слова: studio.alt, alt_studio, 3d-artist
        if lowered.startswith(hint) and len(lowered) > len(hint) \
                and not lowered[len(hint)].isalnum():
            return hint
        if lowered.endswith(hint) and len(lowered) > len(hint) \
                and not lowered[-len(hint) - 1].isalnum():
            return hint
    return None


@dataclass
class Verdict:
    decision: str = REVIEW
    reasons: List[str] = field(default_factory=list)
    meme_score: int = 0
    art_score: int = 0
    ad_score: int = 0
    # Рішення прийняла візуальна модель, а не правила. Такі пости показуємо
    # окремою вкладкою: людина має бачити, що саме модель зробила за неї.
    by_model: bool = False

    @property
    def label(self) -> str:
        return {DOWNLOAD: "качати", SKIP: "мем", REVIEW: "під питанням"}.get(
            self.decision, self.decision
        )

    def why(self) -> str:
        return "; ".join(self.reasons) if self.reasons else "немає виразних ознак"


@dataclass
class Rules:
    """Налаштовувані набори. Порожній список означає «брати типовий»."""

    block_accounts: Sequence[str] = ()
    allow_accounts: Sequence[str] = ()
    meme_tags: Sequence[str] = ()
    art_tags: Sequence[str] = ()
    ad_tags: Sequence[str] = ()
    max_meme_seconds: float = 0.0     # 0 = не зважати на тривалість

    def memes(self) -> set:
        return _norm(self.meme_tags) or set(MEME_TAGS)

    def arts(self) -> set:
        return _norm(self.art_tags) or set(ART_TAGS)

    def ads(self) -> set:
        return _norm(self.ad_tags) or set(AD_TAGS)


def _norm(values: Sequence[str]) -> set:
    return {str(v).strip().lstrip("#@").lower() for v in values if str(v).strip()}


def classify(media, rules: Optional[Rules] = None) -> Verdict:
    """Головна функція. Приймає об'єкт Media з instagrapi (або будь-що схоже)."""
    rules = rules or Rules()
    verdict = Verdict()

    user = getattr(media, "user", None)
    username = ((getattr(user, "username", "") if user else "") or "").lower()
    full_name = ((getattr(user, "full_name", "") if user else "") or "").lower()
    caption = (getattr(media, "caption_text", "") or "")
    lowered = caption.lower()
    tags = {t.lower() for t in _HASHTAG.findall(caption)}

    # 1. Списки акаунтів — абсолютний пріоритет, без жодних правил.
    blocked, allowed = _norm(rules.block_accounts), _norm(rules.allow_accounts)
    if username and username in blocked:
        verdict.decision = SKIP
        verdict.reasons.append(f"акаунт @{username} у чорному списку")
        return verdict
    if username and username in allowed:
        verdict.decision = DOWNLOAD
        verdict.reasons.append(f"акаунт @{username} у білому списку")
        return verdict

    # 2. Реклама — визначається найнадійніше, бо Instagram позначає її сам.
    if getattr(media, "is_paid_partnership", False):
        verdict.ad_score += 3
        verdict.reasons.append("позначено як платна співпраця")
    if getattr(media, "sponsor_tags", None):
        verdict.ad_score += 3
        verdict.reasons.append("є рекламний партнер")
    ad_hits = tags & rules.ads()
    if ad_hits:
        verdict.ad_score += 2
        verdict.reasons.append("рекламні хештеги: " + ", ".join(sorted(ad_hits)))
    if any(phrase in lowered for phrase in AD_PHRASES):
        verdict.ad_score += 2
        verdict.reasons.append("рекламна фраза в підписі")

    # 3. Арт.
    art_hits = tags & rules.arts()
    if art_hits:
        verdict.art_score += 2 if len(art_hits) > 1 else 1
        verdict.reasons.append("арт-хештеги: " + ", ".join(sorted(art_hits)))
    account_art = name_hit(username, ART_ACCOUNT_HINTS) or name_hit(full_name, ART_ACCOUNT_HINTS)
    if account_art:
        verdict.art_score += 1
        verdict.reasons.append(f"акаунт схожий на творчий ({account_art})")

    # 4. Мем.
    meme_hits = tags & rules.memes()
    if meme_hits:
        verdict.meme_score += 2 if len(meme_hits) > 1 else 1
        verdict.reasons.append("мем-хештеги: " + ", ".join(sorted(meme_hits)))
    account_meme = name_hit(username, MEME_ACCOUNT_HINTS) or name_hit(full_name, MEME_ACCOUNT_HINTS)
    if account_meme:
        verdict.meme_score += 2
        verdict.reasons.append(f"акаунт схожий на мемний ({account_meme})")

    duration = getattr(media, "video_duration", None)
    if rules.max_meme_seconds and duration and duration <= rules.max_meme_seconds \
            and verdict.art_score == 0:
        verdict.meme_score += 1
        verdict.reasons.append(f"коротке відео ({duration:.0f} с) без арт-ознак")

    # 5. Рішення.
    # Качаємо лише за вагомими ознаками: реклама або арт-хештеги.
    # Схоже ім'я акаунта саме по собі — привід показати тобі, а не вирішувати.
    if verdict.ad_score >= 2:
        verdict.decision = DOWNLOAD
    elif art_hits and verdict.art_score > verdict.meme_score:
        verdict.decision = DOWNLOAD
    elif verdict.meme_score >= 2 and verdict.art_score == 0:
        verdict.decision = SKIP
    else:
        verdict.decision = REVIEW
    return verdict
