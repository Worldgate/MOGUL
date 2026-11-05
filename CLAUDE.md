# MOGUL

Metadata Organizer & Global Unified Library (working name).

A media cataloging tool: scan media files, identify them by hash, dedupe,
rename and sort them, and keep a catalog that can later feed a website.
Starting with the **radio** module (Opie and Anthony, Howard Stern, Ron and
Fez, and about a dozen other shows). Movies, TV, anime, cartoons, audiobooks,
music, ebooks and comics come later.

## Working with Mavrick

- He doesn't write code. Deliver **complete, drop-in files**, never patches
  or "change line 40" instructions.
- Explain in plain terms what a file does and how to run it.
- He thinks out loud and decides as he goes. When something is undecided,
  pick a sensible default, say what you picked, and keep it easy to change
  (usually a config setting).
- Don't build ahead of the current goal. Note future ideas below instead.

## Current goal

Scan the radio collection, **dedupe it, rename it, and sort it**. Nothing
else yet.

## Environment

- Server hostname: `protostar` (also `abyss` for the movie library). Project
  lives in `~/mogul`.
- Python 3.11+ (uses the built-in `tomllib`).
- Tools on the server: `mediainfo`, `fpcalc` (chromaprint), `ffmpeg`.
- Claude Code runs on the server as a separate non-root user named `claude`.
- Repo: GitHub is the main home (so others can suggest changes), with his
  local Gitea as a mirror.

## Architecture

- **One core plus per-media-type modules.** The core handles config, walking
  folders, hashing, the JSON store, SQLite, and the CLI. Each module (radio,
  audiobooks, video, ...) handles what's specific to its media.
- **CLI shape:** `mogul.py <mediatype> <action> [options]`
  e.g. `mogul.py radio scan`, `mogul.py radio dedupe --dry-run`.
- **Radio is split in two halves:**
  1. **Scanner**: finds new files, reads them, hashes them, writes the JSON.
     Read-only, never moves or renames anything.
  2. **Import**: moves, renames and sorts files based on the fields in the JSON.
- Anything that moves or renames files must support `--dry-run`, and should
  **move duplicates to a folder, never delete them**.

## Data storage

- **JSON is the source of truth. SQLite is a disposable index** rebuilt from
  the JSON. Anything done by hand (corrections, verified dates) must be saved
  in JSON, or a database rebuild will lose it.
- **One JSON file per show per month:**
  `mogul_json/radio/<show>/<yyyy>/<mm> - <Month name>.json`
  e.g. `mogul_json/radio/Opie and Anthony/1999/09 - September.json`
  This folder must be backed up, not regenerated (don't call it a "cache").
- Folders (set in `mogul.toml` under `[core]`, relative to that file):
  - `mogul_incoming/<media type>/` where new files are dropped
  - `mogul_json/` the catalog
  - `mogul_reports/` one report per scan
  `mogul_incoming/` and `mogul_reports/` are in `.gitignore`, plus every
  audio/video extension anywhere in the repo as a safety net. Media never
  goes to GitHub. **`mogul_json/` is pushed to GitHub on purpose** (the
  catalog is versioned in git).
- **No API keys or secrets in the repo.** When they're needed, they go in
  `secrets.toml` (already in `.gitignore`) or environment variables.
- Write JSON safely: write to a temp file, then rename over the old one.
- No file paths stored in the JSON. Location is worked out from the naming
  fields; where a file sits during a scan is working state only.

### Monthly manifest layout

```json
{
  "header": {
    "schema": "mogul.radio.month",
    "schema_version": 1,
    "media_type": "radio",
    "show": "Opie and Anthony",
    "code": "O&A",
    "year": 1998,
    "month": 7,
    "created": "2026-11-05T00:40:00-06:00",
    "updated": "2026-11-05T00:40:00-06:00",
    "file_count": 1,
    "mogul_version": "0.1.0"
  },
  "files": [
    {
      "naming": {
        "show": "Opie and Anthony",
        "code": "O&A",
        "year": 1998,
        "month": 7,
        "day": 1,
        "notes": "Listed as 1998-06-31",
        "extension": "mp3",
        "filename": "O&A 1998-07-01 -- Listed as 1998-06-31.mp3",
        "original_filename": "O&A 1998-06-31.mp3"
      },
      "physical": {
        "hashes": {
          "xxh64": "4dc4c7e1a2b3f00e",
          "blake3": "...",
          "sha256": "...",
          "sha512": "..."
        },
        "size": 241927168,
        "duration": 15120.404,
        "container": "MPEG Audio",
        "codec": "mp3",
        "bitrate": 128000,
        "bitrate_mode": "CBR",
        "channels": 2,
        "sample_rate": 44100,
        "mtime": "2026-11-04T01:12:00Z",
        "added": "2026-11-05T00:40:00-06:00"
      },
      "fingerprint": {
        "algorithm": "chromaprint",
        "digest_version": 1,
        "digest": "545c212ed43ca02e...",
        "digest_minutes": 40
      }
    }
  ]
}
```

- `files` is a **list**, so duplicates show up as multiple entries sharing
  an `xxh64`. Dedupe = group by `xxh64`.
- Year, month and day are stored as **numbers**. Zero padding (`07`, `01`)
  is added only when building filenames.
- `mtime` is tracked (last modified). `atime` is not (unreliable, changes on
  every play). `added` = when MOGUL first saw the file.
- Planned later: per-step status (scanned, fingerprinted, checked, renamed,
  each with a date) and a `checks` block (see "File checks").

## Hashing

- **`xxh64` is the file ID.** Use the full 16-hex-character value, always
  stored as a quoted lowercase hex string (never a number, since JavaScript
  can't hold 64-bit integers exactly).
- Mavrick picked xxhash64 partly because nobody else uses it for media IDs.
- The ID is taken over the **audio packets only**:
  `ffmpeg -i FILE -map 0:a:0 -c copy -f data -` piped into xxh64. No
  container, tags or Xing/Info header frame. Tested: same ID after ID3v2
  retag, ID3v1 tag added, OGG comment retag; different ID after a re-encode.
  (Mavrick also tested MP3 → MKA → MP3 keeps it.)
- Also stored: `blake3`, `sha256`, `sha512` (cheap at radio file sizes,
  under ~200 MB).
- **No CRC32.**
- Not stored yet: `md5` and `sha1`. Internet Archive publishes these, so
  add them if verifying mirrored downloads against IA becomes a goal.
- First 8 hex characters of the xxh64 may appear in filenames as a short
  label. The full hash in the JSON is the real ID.

## Acoustic fingerprint

- Chromaprint via `fpcalc -raw -length 0` (`-length 0` = whole file; the
  default only covers the first 2 minutes).
- **Digest** stored in the JSON: one 32-bit value per minute (each bit =
  majority vote across that minute's frames), as 8 hex chars per minute.
  About 2 KB for a 4-hour show. Tool: `tools/fpdigest.py`.
- Measured on his files:
  - same recording, mp3 vs IA-made ogg: 98.5–98.8%
  - different shows: about 83.5–86%
  - working cutoff: 95%+ = probably the same recording
- The digest is a **filter**. Confirm likely matches with the full
  fingerprint, kept as a binary sidecar file named by xxh64 (not in JSON).
- A hash *of* the fingerprint does not work for matching (encodes differ
  ~1%, so hashes never match).
- Internet Archive auto-generates OGG copies from uploaded MP3s. When both
  exist, keep the MP3. IA's `_files.xml` marks these OGGs as
  `source="derivative"`.

## Code layout

```
mogul.py              CLI entry point: mogul.py <media type> <action> [options]
mogul.toml            settings
requirements.txt      Python packages (xxhash, blake3)
mogul_core/           shared code
  config.py           loads mogul.toml, resolves folders
  hashing.py          audio-only xxh64 + whole-file hashes
  mediainfo.py        technical details via mediainfo
  fingerprint.py      chromaprint digest + similarity
  store.py            safe JSON read/write (temp file + rename)
modules/radio/
  __init__.py         radio actions
  naming.py           filename pattern parsing
  scan.py             the radio scanner
tools/                standalone helpers (parsetest.py, fpdigest.py)
examples/             sample JSON
```

Needs on the server: Python 3.11+, `pip install -r requirements.txt`,
`ffmpeg`, `mediainfo`, `fpcalc` (`libchromaprint-tools`).

## Radio scanner (`mogul.py radio scan`)

- Walks `mogul_incoming/radio` and subfolders for the extensions in
  `[radio] extensions`. Read-only: never moves, renames or deletes.
- For each file: parse filename → audio xxh64 + whole-file hashes →
  check catalog → mediainfo → fingerprint digest → add to month JSON.
- Loads the whole radio catalog first, so duplicates are caught across
  every month, not just the file's own month.
- Results per file:
  - **ADDED**: new audio, written to its month file (saved after each
    file, so Ctrl-C keeps progress).
  - **DUPLICATE**: same xxh64 already cataloged. Not added. Flagged
    "DIFFERENT DATE" when the catalog has it under another date.
  - **ALREADY**: the same file scanned before (same xxh64, filename and
    first whole-file hash).
  - **PROBLEM**: invalid date, unknown show code, name doesn't match the
    pattern, unreadable audio. Not added.
- Only hash duplicates count. Different releases of one episode (mp3/ogg/
  m4a, full vs commercial free, best-of) have different audio data, so
  each gets its own entry.
- `--dry-run` writes no catalog JSON (still writes a report).
  `--no-fingerprint` skips fpcalc. `--path` scans another folder.
- Every run writes `mogul_reports/radio-scan_<date>_<time>.json` listing
  added, duplicates (with matches), already cataloged, problems, warnings.
  The import step (not built yet) will use the catalog to move, rename and
  sort, and send duplicates to a dupes folder.
- Test result: mp3 vs IA-style ogg vs m4a of the same audio = 99%
  fingerprint digest similarity.

## Radio specifics

### Incoming filenames

Pattern (in `mogul.toml`): `{code} {yyyy}-{mm}-{dd}[ -- {notes}]`

- `O&A 1998-07-21.mp3`
- `O&A 1998-06-26 -- 1st WNEW show.mp3` (everything after ` -- ` is notes)

Show codes map to show names in `mogul.toml` under `[radio.shows]`
(`O&A`, `R&F`, `HSS`, more added as needed).

`tools/parsetest.py` shows how filenames are read and flags problems:
invalid dates, unknown show codes, names that don't match the pattern.

### Other filename shapes seen (handle later)

- Hourly parts: `2011-01-17 Mon-O&A-06h00m` (several files = one recording).
  The weekday doubles as a date check.

### Recordings vs episodes

- **Episode** = one broadcast (show + date).
- **Recording** = one file (or set of parts) of that episode.
- One episode can have many recordings: 128k vs 64k, full show (`FS`),
  commercial-free (`CF`), talk-only (`TO`, early music-radio era with the
  songs cut), hourly parts.

### Eras

Shows are organized into eras: continuous stretches under one station or
banner name.

- O&A **WNEW era**: first show 1998-06-26, ends 2002-08-22 (Infinity
  cancelled the show). 3–7pm weekdays, music-radio format, so ~40–70 minute
  talk-only files are normal.
- Earlier: WAAF Boston (from March 1995). Later: "Custody Battle" (temporary
  name, CBS → XM/Sirius move), The Virus XM era, "The Final Era" (after
  Anthony's firing).
- Weekend dates in a weekday era are a soft warning, not an error. Early on
  they may really have done weekend shows.

### Known oddities

- `O&A 1998-06-31` (not a real date). Renamed by Mavrick to
  `O&A 1998-07-01 -- Listed as 1998-06-31`. Date is a guess; on the
  bounty list for anyone who can prove the real date.
- `O&A 1998-06-27` is a Saturday, which may be legit.

## Possible future rename scheme (undecided)

`<show>/<yyyy>/<mm>/<yyyy-mm-dd> <code> [<hash8>] <cut><bitrate>.<ext>`
e.g. `1998-07-21 O&A [9a01bc3f] FS128K.mp3`
Should be a template in the config so it can be changed and previewed with
`--dry-run`.

## File checks (planned)

- Tags buried mid-file (merged without stripping): `ID3` header at byte
  offsets other than 0; extra `Xing`/`Info` frames; `APETAGEX`/`LYRICS200`
  not at the end. Chained OGG streams (`ogginfo`).
- Holes from bad downloads: runs of 4 KB+ zero bytes.
- Decode errors: `ffmpeg -v error -i FILE -f null -`; `mp3val` for MP3s.
- Damaged copies should lose to clean ones when picking which duplicate
  to keep.

## Future ideas (not now)

- Per-hash "API" JSON per file, later filled with tags and history (who was
  on the show, etc.). Tags get stripped from files; a "rebuild tags" option
  writes them back when copying to a media player.
- Identify unknown or misdated episodes by fingerprint ("what is this
  file?"), including tracing worst-of/replay clips back to the original air
  date. Watch out for time-compressed replays (sped up 2–5%).
- Cue sheets / commercial-free cuts built by aligning a full show with its
  CF version, so only the full file needs keeping.
- MKA container as a "playable zip": audio + chapters + attachments
  (manifest, cover, original tags).
- Issues and bounty list (`mogul.py issues --bounty`).
- Other modules: audiobooks (multi-file sets, "unclean" sets where one part
  is encoded differently), music (MusicBrainz), video.
- Eventual web UI and website, reading only the JSON/DB, never the media
  folders.
