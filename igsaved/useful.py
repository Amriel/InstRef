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
