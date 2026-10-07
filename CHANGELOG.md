# Changelog

Human-readable notes for each release. The release workflow copies the section
for the tagged version into the GitHub release, and the app shows it under
*About → Update*. Newest first.

## 2.6.0

- **Descriptions are written from a few large frames instead of dozens of tiny
  ones.** Until now a 60-second reel could go to the model as 60 frames shrunk
  to 384 px — about a hundred image tokens each, roughly a tenth of what
  Qwen-VL is built to read per picture. The model answered with frame-by-frame
  lists ("later frames show…") and tags that were not in the clip (`talking-head`
  on a mountain landscape, `sneaker` where there were no shoes). Now every frame
  is 896 px by default, and the count follows the length: 4 for a clip up to
  ten seconds, 8 up to thirty, 12 up to a minute, 20 up to two, 32 beyond. For
  a multi-scene post the model is asked to cover the whole clip in order — what
  it opens with, what it moves through, how it ends — instead of the dominant
  scene only. Settings
  above the new ceiling are lowered when the app starts (and written back the
  next time settings are saved); the log says so. To redo
  the descriptions written the old way, open *Eagle → Library → Describe the
  library* and choose *Rewrite stale*.
- **A one-line summary above the description.** The model now also writes one
  sentence — subject, genre, technique — which opens the Eagle note ("In
  short: …") and the file comment. The long description covers the clip scene
  by scene; the summary is what you skim.
- **Fixed: CGI and 3D stills never got a medium tag.** `cgi`, `ai-generated`
  and `mixed-media` live in both the video and the image list, but the
  vocabulary index only remembered the video one, so on photos and carousel
  slides they were thrown away as unknown; `3d-render` was folded into a
  video-only tag. 97 discarded `cgi` proposals in the vocabulary report were
  this bug, not the model.
- **A new vocabulary, and tags chosen in a second step.** The old list had 378
  tags, 171 of which were never used once, no words for 2D or graphic design,
  and `cinematic` on two posts out of three. The vocabulary is now 432 tags in
  37 groups, organised by profile: a core set (light, colour, dominant hue,
  framing, mood, genre, subject, people, what the post is a reference for)
  always applies; film language (camera moves, editing and transitions, lens,
  grade, location, time and weather, vehicles, products) applies to live
  footage; materials, 3D technique and 3D design to renders; 2D style and 2D
  design to animation and illustration; motion design and graphic design to
  their own. The model first describes the post and names its medium; the app
  then asks it for tags from the matching profiles only — so a fashion film
  gets `steadicam` and `film-emulation`, a render gets `subsurface-skin` and
  `fluid-simulation`, and neither sees the other's list. Software (`blender`,
  `cinema-4d`, `redshift`, `comfyui`…) is taken from the caption, hashtags and
  on-screen text by rules, never guessed from the frames. Tags with no evidence
  in the vocabulary report (`ad`, `cinematic`) are ignored instead of counted.
  A `taxonomy.json` saved before this version is set aside as
  `taxonomy.pre-profiles.json` the first time the vocabulary dialog is opened.
- **Every tag must cite its evidence.** The model now names what it saw for each
  tag; a tag it cannot back up (it literally writes "no visible sneakers") is
  dropped before it reaches Eagle. Decision categories (`ad`, `art`…) no longer
  pollute the vocabulary suggestions.
- **A failed description is retried next run.** Previously, if LM Studio was
  still loading, timed out, or was simply closed, the post was filed as done and
  went to Eagle without a note — and only the slow library pass (10 per run)
  could catch up. The run now warms the model up first and says plainly when it
  cannot; posts that still lack a description are described from the files on
  disk at the start of the next run, and their Eagle items are updated.
- **Thinking models are recognised.** A model that spends its whole answer on
  reasoning and never produces the JSON is reported as such instead of as an
  unparseable reply. Whether a model is visual or text-only is now read from LM
  Studio, not guessed from its name.
- Settings → Model → Frames: "frames per video" is now a ceiling (1–32), and
  there is a new "frame side" field; "seconds per frame" is gone.

## 2.5.5

- **Describe the whole Eagle library, not only the app's own folder.** Until now
  every library pass was filtered to "Instagram Saved" and its subfolders, so a
  library of 1,351 items reported 266 — correctly, but not usefully: a reference
  library is searched as a whole, and everything brought in by hand stayed
  untagged. *Eagle → Library* now has a switch for it.

## 2.5.4

- **Fixed: the app only ever saw the first 200 items of the Eagle library.**
  "Everything already has a description" was true — of the first page. Eagle's
  `offset` means a page number in some builds and an item offset in others;
  InstRef asked for `offset=200`, which in the page-number build means "page
  200", i.e. nothing, so every walk of the library ended after 200 items. The
  walk now calibrates on its second request and keeps whichever meaning returns
  new items, de-duplicating by item id. This affected everything that reads the
  library: describing old items, the duplicate check before import, and the
  cleanup of local copies.

## 2.5.3

- **A failed cleanup no longer fails the work that succeeded.** Unloading the
  model is a tidying step; when it raised, the whole "describe the library" run
  reported as failed even though the descriptions were already in Eagle. It is
  now caught broadly and reported as "the model stayed in memory", nothing more.
- **Long steps say what they are doing.** Describing one library item means
  pulling dozens of frames and a request that can take minutes, and the log said
  nothing the whole time — it looked frozen. Each item now logs when frames are
  being pulled and when the model is asked.
- **Running from source: a warning when the files on disk change under a live
  app.** Python reads a module once, at import, so replacing files while InstRef
  is open leaves half the app old and half new (lazily imported parts arrive
  new) — which produces errors that exist in neither version. Starting a sync or
  a library description now checks and says to restart.

## 2.5.2

- **The model no longer stays loaded in LM Studio after a run.** LM Studio keeps
  a loaded model in memory until something tells it otherwise, so a 4B vision
  model sat on several gigabytes of VRAM between runs that happen once every few
  hours. InstRef now asks it to unload when the run ends — including after an
  error or a manual stop — and sends a TTL with each request so LM Studio frees
  the memory by itself if the app never got the chance. Both live under
  *Model → Connection → Memory*, together with an "Unload now" button.
  Unloading over HTTP needs LM Studio 0.4.0 or newer; on older versions the app
  says so instead of failing quietly.

## 2.5.1

- **Fixed: unrelated posts were flagged as duplicates and parked in Review.**
  The frame fingerprint (dHash) compares each pixel with the one to its right,
  so a flat frame — a black opening, a white flash, a plain background — turns
  into an all-zero hash that matches every other flat frame exactly. Reels
  routinely start on one, so unrelated clips looked identical. Flat frames are
  no longer fingerprinted, old ones are cleared from the database on start, and
  a single matching frame is no longer enough: a duplicate now needs either a
  near-exact frame or two frames pointing at the same post. On a real library
  this took the false positives from 8 posts out of 34 down to none.
  Anything already sitting in Review as a "possible duplicate" was one of
  these — keep it with `Y`.

## 2.5.0

- **Optional components install from inside the app.** *Maintenance → Extras*
  lists what is not bundled (voice transcription with faster-whisper), shows
  whether it is installed and installs or removes it with one button. No
  console, no `pip`: the installed build looks for a matching Python and, if
  there is none, downloads a small one from python.org by itself. The same tab
  downloads the whisper model up front, so the first run does not sit silent
  for minutes.
- **Eagle is asked what it already has.** Before importing, the app reads the
  library and skips posts that are in it, instead of trusting only its own
  database — which knows nothing about items you imported by hand, or about
  anything from before "forget download history". This was the source of
  repeated duplicates. Found items are written back into the database, so the
  check costs one library read per run. Can be switched off under
  *Eagle → Connection*.
- **Useful posts get their own tags.** A reel that lists tools, explains a
  workflow or walks through settings looks like any other clip to a vision
  model — the meaning is in the on-screen text. Text is now matched by rules:
  `useful` plus `resource-list`, `tutorial`, `tips`, `workflow-breakdown`,
  `software-tip`, `prompt-share`, `explainer`, `news-drop`, `course-promo`,
  `link-in-bio`. *Model → Vocabulary → Normalize* applies them to the existing
  library without asking the model again.

## 2.4.5

- Updating the installed app now works end to end. The helper waits until the
  app has actually exited before starting the silent installer (previously the
  installer found the files in use and quietly gave up), then starts InstRef
  again. The installer writes a log to `%TEMP%\InstRef-update.log`.

## 2.4.4

- Relaunch helper without `timeout` (it fails in a detached process). Not
  enough on its own — see 2.4.5.

## 2.4.3

- After a one-click update the installed app starts again by itself. The silent
  installer never relaunched it; a small helper now waits for the install to
  finish and opens InstRef.
- Release notes are now written for people: this file is the source, and the
  app shows the section for the new version instead of raw commit messages.

## 2.4.2

- The LM Studio model list loads on its own — on startup and when you open
  *Model*. Visual models come first; text-only models are marked so a
  `qwen3.8-27b` cannot be picked by accident.
- Release notes are built from the repository instead of GitHub's bare
  "Full Changelog" link.

## 2.4.1

- If the configured model is text-only, the app says so before the run — in the
  log, on the *Model* page and on the *Overview* card — instead of silently
  producing posts without descriptions.
- An answer that contains nothing but the vocabulary marker is no longer stored
  as a description, so the post gets described properly next time.
- The window log is also written to `logs/gui_YYYY-MM.log`.

## 2.4.0

- New window layout: a sidebar with one section per topic (Overview, Sync,
  Review, Model, Eagle, Account, Maintenance, About), sub-tabs inside each
  section, status cards for session / Eagle / model, and settings that save
  themselves when you switch sections.
- One-click update from GitHub: the installed app downloads and runs the
  installer; a source checkout downloads the release archive, replaces the code
  without touching your settings, database, session or vocabulary, refreshes
  dependencies and restarts.

## 2.3.x

- Build fixes for the first Windows release: dependency pins, Qt teardown in
  tests, UTF-8 on the Windows runner.

## 2.3.0

- Account protection: one run at a time, a hard stop with a 24-hour pause when
  Instagram answers "please wait", a random offset for scheduled runs, weekly
  database backups and a backup before every migration, session cookie
  encrypted with Windows DPAPI, posts that keep failing are given up on after
  three attempts, log rotation.
- Better descriptions: frames picked by scene cut and by clip length, on-screen
  text extracted, reposts caught by a perceptual hash, collection name used as a
  hint, your starred descriptions used as examples, prompt hash stored, old
  tags re-normalised against the vocabulary, a small backlog described after
  every sync, likes read with a cursor instead of a fixed window.
- Review: filmstrip on video cards, editable description and tags, keyboard
  triage, agreement statistics, download by URL, quick-start indicators.
- Update check, optional voice-over transcription, redescribe with another
  model, vocabulary report.

## 2.2.0

- Product boundary: InstRef is a pipeline, Eagle is the library. Local copies
  can be removed after a confirmed import.

## 2.1.0

- Rate limiting after Instagram's automation warning: minimum hours between
  runs, session reuse, slower paging.

## 2.0.0

- Renamed to InstRef, single review queue, installer, GitHub.
