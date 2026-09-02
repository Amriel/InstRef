# InstRef

**Turn your Instagram saves into a searchable reference library.**

InstRef pulls your saved and liked Instagram posts, writes proper metadata into
the files themselves, and files everything into [Eagle](https://eagle.cool/).
A local vision model watches each video and writes a description and tags from
a controlled vocabulary — so six months later you can actually find that one
reel with the liquid-metal transition.

Runs on Windows. Nothing leaves your machine: Instagram, your disk, your Eagle
library, and a model running locally in LM Studio.

[Українською](README.uk.md) · [MIT licence](LICENSE)

---

## What it does

**Downloads** saved posts, collections and likes — videos, photos, carousels
and thumbnails — into a folder you choose. Incremental: it remembers what it
already fetched and never pulls the same post twice.

**Names files readably.** `2026-08-20_studioalt_Liquid metal study.mp4`, not
`2026-08-20_studioalt_DZXi35noMC8.mp4`. The template is configurable.

**Writes metadata inside the files.** Caption, author, post URL, date and tags
go into the MP4 iTunes atoms and JPEG EXIF — including the Windows XP fields,
so Explorer shows them. No sidecar files needed.

**Imports into Eagle** with tags and annotations, one item per post, into the
folder you nominate. Eagle keeps its own copy of every file, so InstRef can
optionally **clean up after itself**: once a post is confirmed present in the
library, the local copy is deleted — while still remembered, so nothing is
ever downloaded twice. The download folder is a staging area, not an archive.

**Describes what it sees.** A vision model in [LM Studio](https://lmstudio.ai/)
looks at frames sampled across the whole clip — not the cover, which for reels
is usually a black frame — and writes an English one-paragraph description plus
tags drawn from a fixed vocabulary of ~370 terms across 29 categories.

**Filters your likes.** Saves are yours and get downloaded as-is. Likes are
noisier, so rules plus the model sort memes from references. Anything either is
unsure about waits in a review tab with a big preview and two buttons.

**Catches reposts.** The same reel posted by three accounts is three posts and
three different files. A perceptual hash of the frames recognises it anyway
and holds the copy for review with a note about the original.

**Takes single links too.** Paste a few post URLs and they go through the same
pipeline — download, describe, Eagle — onto a "By link" shelf.

---

## Install

Download the latest **InstRef-Setup.exe** from
[Releases](https://github.com/Amriel/InstRef/releases) and run it. No Python
required. Installs per user, no admin prompt.

A portable `.zip` is published alongside it if you prefer no installer.

<details>
<summary>Running from source</summary>

```bash
git clone https://github.com/Amriel/InstRef
cd InstRef
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.pyw
```

Python 3.10+. On Windows `install.bat` does the same thing in one click.
</details>

---

## Getting started

1. **Connect a session.** Open the *Session* tab. InstRef tries to read the
   `sessionid` cookie from your browser. Chrome and Edge on Windows 11 encrypt
   cookies with App-Bound Encryption and will fail — running as administrator
   does not help. Paste the value manually instead: DevTools → Application →
   Cookies → `instagram.com` → `sessionid`.

2. **Pick a folder** and which collections to sync. *Settings → Downloads*.

3. **Connect Eagle** (optional). Eagle's local API only answers while the app
   is open. *Settings → Eagle* → **Test connection**.

4. **Connect a model** (optional). In LM Studio load a vision model and start
   the server (*Developer → Start Server*). Paste the address in
   *Settings → Model* — `/v1` is appended automatically.

5. **Sync.**

---

## The vision model

The model receives frames from across the clip rather than the cover image —
judging a reel by its first frame was the single largest source of
misclassification. Frames are picked **by scene cut**, not at even intervals:
a fast-cut reel sampled evenly lands on transitions and black frames. How many
depends on length — roughly one per five seconds, between 6 and 60. Carousels
are described slide by slide, because slides are different pictures and one
shared description would be a lie about each of them.

It returns four things: a filing category, an English description written like
a director's note, any **text visible on screen** (step titles, plugin names —
what tutorials are actually searched by), and tags. Posts saved in a collection
named like a tutorial get a hint to describe the technique, not the picture.
Optionally, with `faster-whisper` installed, the voice-over is transcribed and
handed to the model as context.

**Tags come from a controlled vocabulary.** `3d-render`, `3drender`, `render`
and `3d` are four different tags to Eagle and none of them finds the others —
which is how reference libraries stop being searchable. So the model picks from
fixed lists covering lighting, colour, framing, camera angle, lens, movement,
editing, composition, medium, technique, subject, materials, environment and
mood.

Asking a model to follow a list is not enough; it will improvise anyway. So the
vocabulary is **enforced in code** after the answer: unknown tags are dropped,
near-misses are mapped (`skiing` → `sport-action`), mutually exclusive tags are
resolved, per-category limits are applied, and motion tags are stripped from
stills.

The vocabulary grows deliberately. A rejected tag is counted, and once the model
has asked for it enough times it surfaces in *Suggestions* where one click adds
it to a category of your choosing. It grows from your actual content rather than
the model's imagination.

Recommended model: `Qwen3-VL-4B-Instruct`. Take **Instruct**, not **Thinking** —
reasoning variants spend hundreds of tokens deliberating before producing the
one line of JSON we need.

Descriptions you like can be starred in the review tab; the last few become
examples in the prompt, so the model's register drifts towards yours rather
than mine. Old descriptions written before the vocabulary existed can be
re-normalised in one click without asking the model again, and a vocabulary
report shows which tags are overused and which never fire.

The library gets described in the background: after every sync, a handful of
older Eagle items without a description get one, so coverage grows on its own.

---

## Review

Everything that was not decided outright waits in one tab with large previews.

A card marked **◆ model** means the model already decided and you are confirming
or overturning it; without the mark, the rules could not tell and the call is
entirely yours. Rejected posts appear too — the clip was already downloaded to
extract frames, so showing it costs nothing, and a wrong "meme" would otherwise
be invisible. Those sort first, because a wrong rejection costs you a post while
a wrong approval costs you a file.

Each card shows a filmstrip of the clip and the model's description and tags
in editable fields — fix them there and the corrected version is what lands in
Eagle. Keyboard works: arrows to move, `Y` keep, `N` drop, `O` open. The tab
also keeps score of how often you agree with the model, per category, which is
a far more honest measure of its accuracy than its self-reported confidence.

Nothing reaches Eagle before you have seen it.

---

## Configuration notes

Settings, the database and logs live in `%APPDATA%\InstRef` and survive
uninstalling. The tag vocabulary is `taxonomy.json` in the same folder — edit it
by hand if you like; updates never overwrite it.

Any setting left empty means "use the built-in default", which is how app
updates can improve defaults without discarding your edits.

---

## Documentation

- [README.uk.md](README.uk.md) — full manual, in Ukrainian
- [DEVELOPMENT.md](DEVELOPMENT.md) — architecture, data model, and a log of
  every bug found and what it taught us
- [CLAUDE.md](CLAUDE.md) — working rules for contributors

---

## Built with

[instagrapi](https://github.com/subzeroid/instagrapi) ·
[PySide6](https://doc.qt.io/qtforpython-6/) ·
[OpenCV](https://opencv.org/) ·
[mutagen](https://mutagen.readthedocs.io/) ·
[piexif](https://piexif.readthedocs.io/)

InstRef uses Instagram's private mobile API through `instagrapi`. That is what
makes reading your own saves possible at all — and it also means Instagram can
see it as automation, because that is what it is.

Instagram flags **pace**, not volume: short even intervals and repeated logins
look like a script. InstRef defends the account by asking less often, not by
hiding — it refuses to run twice within `min_hours_between_runs` (6 by default),
reuses a stored session instead of logging in fresh each time, waits 8–15
seconds between pages, starts scheduled runs at a random offset, never runs two
syncs at once, and if Instagram answers "please wait a few minutes" it stops
the whole run and stays away for a day — even if you press the button. Posts
that keep failing (deleted, private) are given up on after three attempts
instead of being retried forever. Lower those at your own risk, and expect a
warning from Instagram if you sync many times an hour.

The session cookie is stored encrypted with Windows DPAPI, the database is
backed up weekly and before every migration, and the app checks GitHub once a
day for a newer release.
