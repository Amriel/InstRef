# Changelog

Human-readable notes for each release. The release workflow copies the section
for the tagged version into the GitHub release, and the app shows it under
*About → Update*. Newest first.

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
