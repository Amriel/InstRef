"""Позначка «це не картинка, це користь».

Половина збереженого — не референс кадру, а порада: список сайтів, розбір
техніки, налаштування, підбірка інструментів. Візуально такий пост нічим не
відрізняється від будь-якого іншого ролика: студія, світло, текст на екрані —
і модель чесно описує саме це, бо саме це вона й бачить. Сенс лежить у ТЕКСТІ
на екрані та в підписі: «10 niche websites for vibe coders», «Stop using
Pinterest — use these instead».

Тому позначку ставить не модель, а правила: текст або містить ці ознаки, або
ні, і це видно без здогадок. Модель усе одно може додати ті самі теги зі
словника — правила лише гарантують, що вони не загубляться.

Усі теги тут мусять бути в категорії `useful` словника, інакше перевірка
словником їх викине.
"""

from __future__ import annotations

import re
from typing import Dict, List, Sequence, Tuple

MARKER = "useful"

# Софт, згадка якого разом із навчальною ознакою означає «підказка по програмі».
SOFTWARE = (
    r"blender|houdini|cinema ?4d|c4d|after ?effects|\bae\b|premiere|davinci|"
    r"nuke|unreal|unity|zbrush|substance|touchdesigner|figma|photoshop|"
    r"illustrator|lightroom|maya|3ds ?max|marvelous|midjourney|comfyui|runway"
)

RULES: List[Tuple[str, Sequence[str]]] = [
    ("resource-list", (
        r"\b\d+\s*(?:/\s*\d+)?\s+(?:\w+\s+){0,2}"
        r"(?:websites?|sites?|tools?|apps?|resources?|plugins?|scripts?|"
        r"fonts?|assets?|prompts?|extensions?|channels?|accounts?)\b",
        r"\b(?:websites?|tools?|resources?|plugins?|apps?)\s+(?:for|every|you|"
        r"i use|that)\b",
        r"\buse these instead\b", r"\bhere are\b", r"\bmy go-?to\b",
        r"\balternatives?\b", r"\bcheat ?sheet\b",
        r"\bпідбірк|\bсайт(?:и|ів)\b|\bінструмент(?:и|ів)\b",
    )),
    ("tutorial", (
        r"\bhow (?:to|i)\b", r"\btutorial\b", r"\btut\b", r"\bstep \d\b",
        r"\bhere'?s how\b", r"\bguide\b", r"\bwalk-?through\b", r"\blesson\b",
        r"\bкак сделать\b", r"\bяк зробити\b", r"\bурок\b", r"\bтуторіал\b",
    )),
    ("tips", (
        r"\btips?\b", r"\btricks?\b", r"\bhacks?\b", r"\bshortcuts?\b",
        r"\bstop (?:using|doing|making)\b", r"\bmistakes?\b", r"\bpro tip\b",
        r"\bnever\b.{0,20}\bagain\b", r"\bлайфхак\b", r"\bпорад",
    )),
    ("workflow-breakdown", (
        r"\bbreakdown\b", r"\bworkflow\b", r"\bhow (?:i|we) (?:made|built|did)\b",
        r"\bnode setup\b", r"\bpipeline\b", r"\bрозбір\b",
    )),
    ("explainer", (
        r"\bexplained\b", r"\bwhat is\b", r"\bwhy\b.{0,24}\b(?:matters|works)\b",
        r"\bthe difference between\b",
    )),
    ("prompt-share", (r"\bprompts?\b", r"\bпромпт",)),
    ("news-drop", (
        r"\bjust (?:released|dropped|launched|added)\b", r"\bis out now\b",
        r"\bnew (?:update|feature|version|model)\b", r"\bv\d+(?:\.\d+)? is here\b",
    )),
    ("course-promo", (
        r"\bcourse\b", r"\bmasterclass\b", r"\bwebinar\b", r"\bfree training\b",
        r"\bкурс\b",
    )),
    ("link-in-bio", (
        r"\blinks? in (?:bio|the description)\b", r"\bdm me\b", r"\bsave this\b",
        r"\bbookmark this\b",
        # «Comment "CODE" and I'll send you the links» — окрема, дуже частa форма.
        r"\bcomment\b[^.]{0,60}\b(?:send|get|dm|link)\b",
        r"\bпосилання в (?:біо|шапці)\b",
    )),
]

# Теги, поява яких означає «пост дає користь». `link-in-bio` і `course-promo`
# самі по собі — ознака промо, а не користі, тож marker від них не залежить.
CONTENT_TAGS = {"resource-list", "tutorial", "tips", "workflow-breakdown",
                "explainer", "prompt-share", "news-drop", "software-tip"}

_COMPILED: Dict[str, List] = {
    tag: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for tag, patterns in RULES
}
_SOFTWARE = re.compile(SOFTWARE, re.IGNORECASE)


def useful_tags(*texts: str) -> List[str]:
    """Теги користі за текстом на екрані, підписом і транскриптом.

    Порядок збережено: спершу конкретне (що саме дає пост), потім `useful`.
    """
    blob = " ".join(t for t in texts if t).strip()
    if not blob:
        return []
    found: List[str] = []
    for tag, patterns in _COMPILED.items():
        if any(pattern.search(blob) for pattern in patterns):
            found.append(tag)
    if found and _SOFTWARE.search(blob) and CONTENT_TAGS.intersection(found):
        found.append("software-tip")
    if CONTENT_TAGS.intersection(found):
        found.append(MARKER)
    return found


# ----------------------------------------------------------------- програми
# Назву програми модель із кадру не вгадує («Never guess software brands»), але
# вона часто є в підписі, в тексті на екрані й у хештегах. Тож тег ставить код:
# він шукає ЦІЛІ слова (не підрядки: «art» у «gamestart» уже був багом).
SOFTWARE_TAGS = [
    "blender", "houdini", "cinema-4d", "maya", "3ds-max", "unreal-engine", "unity",
    "after-effects", "nuke", "davinci-resolve", "premiere", "redshift", "octane",
    "arnold", "vray", "substance", "zbrush", "marvelous-designer", "touchdesigner",
    "notch", "embergen", "comfyui", "stable-diffusion", "midjourney", "runway",
    "kling", "sora", "veo", "flux", "photoshop", "illustrator", "figma",
]

# Скорочення й альтернативні написання → тег. Спільні з синонімами словника.
SOFTWARE_ALIASES: Dict[str, str] = {
    "b3d": "blender", "c4d": "cinema-4d", "cinema4d": "cinema-4d",
    "ae": "after-effects", "aftereffects": "after-effects",
    "ue5": "unreal-engine", "ue4": "unreal-engine", "unreal": "unreal-engine",
    "resolve": "davinci-resolve", "davinci": "davinci-resolve",
    "sd": "stable-diffusion", "mj": "midjourney", "3dsmax": "3ds-max",
    "touch-designer": "touchdesigner", "ps": "photoshop",
}
# Слова, що без `#` частіше означають щось своє: Unity of design, Runway show,
# «resolve the issue», Arnold, SD card. Для них потрібен хештег.
_AMBIGUOUS = frozenset({
    "ae", "sd", "mj", "ps", "resolve", "unity", "runway", "flux", "notch", "arnold",
    "maya", "octane", "substance", "nuke", "davinci",
})
# Багатослівні й розбиті форми, яких не дає ні тег, ні alias.
_EXTRA_PHRASES = {
    ("v", "ray"): "vray", ("octane", "render"): "octane",
    ("touch", "designer"): "touchdesigner", ("substance", "painter"): "substance",
    ("substance", "designer"): "substance", ("unreal", "engine", "5"): "unreal-engine",
    ("stable", "diffusion"): "stable-diffusion", ("cinema", "4d"): "cinema-4d",
    ("marvelous", "designer"): "marvelous-designer", ("3ds", "max"): "3ds-max",
    ("after", "effects"): "after-effects", ("davinci", "resolve"): "davinci-resolve",
    ("unreal", "engine"): "unreal-engine", ("premiere", "pro"): "premiere",
}


def _phrase_table() -> Dict[tuple, str]:
    table: Dict[tuple, str] = {}
    for tag in SOFTWARE_TAGS:
        table[tuple(tag.split("-"))] = tag
        table[(tag.replace("-", ""),)] = tag
    for alias, tag in SOFTWARE_ALIASES.items():
        table[tuple(alias.split("-"))] = tag
        table[(alias.replace("-", ""),)] = tag
    table.update(_EXTRA_PHRASES)
    return table


_PHRASES = _phrase_table()
_TOKEN = re.compile(r"(#?)([a-z0-9]+)")


def software_tags(caption: str = "", screen_text: str = "", transcript: str = "",
                  hashtags: Sequence[str] = ()) -> List[str]:
    """Програми, названі в тексті поста: «Made in Blender + Redshift #c4d».

    Токени, а не підрядки; регістр не важить; `#houdini` теж рахується. Двозначні
    слова (ae, sd, resolve, unity…) беруться лише з хештегом. Слово «render» тегу
    програми не дає: це техніка, а не софт. Порядок — за появою в тексті.
    """
    blob = " ".join(str(t) for t in (caption, screen_text, transcript) if t)
    tags_blob = " ".join("#" + str(h).strip().lstrip("#") for h in (hashtags or []) if h)
    tokens = [(m.group(2), bool(m.group(1)))
              for m in _TOKEN.finditer((blob + " " + tags_blob).lower())]
    found: List[str] = []
    index = 0
    while index < len(tokens):
        for size in (3, 2, 1):
            chunk = tokens[index:index + size]
            if len(chunk) < size:
                continue
            tag = _PHRASES.get(tuple(word for word, _ in chunk))
            if not tag:
                continue
            first_word, hashed = chunk[0]
            if size == 1 and first_word in _AMBIGUOUS and not hashed:
                continue
            if tag not in found:
                found.append(tag)
            index += size - 1
            break
        index += 1
    return found
