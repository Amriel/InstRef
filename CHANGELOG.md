# Changelog

Human-readable notes for each release. The release workflow copies the section
for the tagged version into the GitHub release, and the app shows it under
*About → Update*. Newest first.

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
