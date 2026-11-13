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
- **No machine paths in the repo.** The catalog JSON has no paths (checked).
  Machine-specific settings (NAS paths, drive names) go in
  `mogul.local.toml`, which is in `.gitignore` and is layered over
  `mogul.toml` (nested sections merge). Template: `mogul.local.toml.example`.
  Reports contain full paths but are gitignored.
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
        "part": null,
        "notes": "Listed as 1998-06-31",
        "extension": "mp3",
        "filename": "O&A 1998-07-01 -- Listed as 1998-06-31.mp3",
        "original_filename": "O&A 1998-06-31.mp3"
      },
      "physical": {
        "hashes": {
          "xxh64": "4dc4c7e1a2b3f00e",
          "audio_blake3": "...",
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

- Three roles:
  | Hash | Covers | Job |
  |---|---|---|
  | `xxh64` | audio only | **identifies the media**: the ID and the HASH8 in filenames |
  | `audio_blake3` | audio only | **confirms the media**: secure, checked before anything counts as a duplicate |
  | `blake3`, `sha256`, `sha512` | whole file | **identify the exact file**, byte for byte |
- xxh64 is fast but not secure: a file can be crafted to match it on purpose
  (poisoning). So xxh64 finds candidates and `audio_blake3` confirms them
  (`hashing.audio_match()` → confirmed / unconfirmed / collision).
  Both audio hashes come from one ffmpeg read.
- Entries made before audio_blake3 existed are "unconfirmed" (treated as a
  match by xxh64 alone) until `radio verify` or an import fills it in.
- **Collision** (same xxh64, different audio_blake3): never moved or treated
  as a duplicate. Scan adds it as its own entry with a WARNING; import
  SKIPs it; verify flags it.
- Mavrick strips tags himself with TagScanner before importing (stripping
  gives him the same whole-file hash every time). MOGUL doesn't strip tags.
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
  config.py           loads mogul.toml (+ mogul.local.toml), resolves folders
  hashing.py          audio-only xxh64 + whole-file hashes
  mediainfo.py        technical details via mediainfo
  fingerprint.py      chromaprint digest + similarity
  store.py            safe JSON read/write (temp file + rename)
  progress.py         "[ 53/194  ~12m05s left]" labels, ETA by bytes not file count
  damage.py           finds damage in a file (mp3val spots -> show times, ffmpeg errors, empty gaps)
modules/radio/
  __init__.py         radio actions
  naming.py           filename pattern parsing
  scan.py             the radio scanner
  importer.py         the radio importer (move, rename, sort, dupes)
  verify.py           checks the library against the catalog
  fingerprint_pass.py adds missing fingerprints, several files at once
  quarantine.py       moves bad recordings + their entries out of the catalog
  damaged.py          lists recordings with damaged spots; mark ok/keep/bad after listening
  check.py            reads every file for damage; readable worst-first report
tools/                standalone helpers (parsetest.py, fpdigest.py, fixnames.sh, damagemap.py, sameday.py, listen.py, findtags.py)
examples/             sample JSON
```

Needs on the server: Python 3.11+, `pip install -r requirements.txt`,
`ffmpeg`, `mediainfo`, `fpcalc` (`libchromaprint-tools`), `mp3val`.

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
  - **DUPLICATE**: same audio (xxh64, confirmed by audio_blake3) already
    cataloged. Not added. Flagged
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
- **Cleaner copy alert:** when an ADDED file's show + date already has a
  damaged entry (any verdict but bad), scan prints `^ this date has a
  DAMAGED copy: NAME [verdict], N% alike` (95%+ = same recording) and
  lists it in the report's `better_copies`. Mavrick then checks with
  `tools/sameday.py`, marks the damaged one `--bad --note "replaced by ..."`
  and quarantines it.
- Test result: mp3 vs IA-style ogg vs m4a of the same audio = 99%
  fingerprint digest similarity.

## Radio import (`mogul.py radio import`)

- Moves cataloged files from `mogul_incoming/radio` into the radio library,
  renamed and sorted. Run `scan` first; only cataloged files move.
- Settings in `mogul.toml` `[radio]`:
  - `library` (default `mogul_library/radio`, meant to point at the NAS)
  - `dupes` (default `mogul_dupes/radio`), `rename_dupes` (default true)
  - `folder_template = "{show}/{yyyy}/{mm}"`
  - `filename_template = "{yyyy}-{mm}-{dd}_{code}_{HASH8}"` (extension added)
  - Tokens: `{show} {code} {yyyy} {mm} {dd} {month_name} {HASH8} {hash8}
    {part} {notes}`. `{HASH8}` = first 8 chars of the xxh64, uppercase.
    `{part}` = two digits (01, 02...).
  - `[ ... ]` in a template = optional: dropped when a token inside is
    empty. Default filename template is
    `{yyyy}-{mm}-{dd}_{code}[_pt{part}]_{HASH8}`, so files without a part
    keep the old names: `2005-03-14_O&A_pt02_DE8CBE6D.mp3` vs
    `2005-03-15_O&A_D5948F25.mp3`.
  - Example: `Opie and Anthony/1999/09/1999-09-14_O&A_D0DBAAC0.mp3`
- Name collision (a *different* file already has that name): use the next
  8-character run of the xxh64 (offset 1, 2, ... 8).
- Per file:
  - **IMPORT**: it's the exact file the catalog entry was made from
    (original filename + size) and the entry isn't in the library yet.
  - **DUPLICATE**: same audio as something already in the library or
    imported this run → moved to `dupes`. Never deleted. With
    `rename_dupes = true` (default) it gets the same folder and name as the
    library file it matches, keeping its own extension, plus ` (2)`, ` (3)`
    for more copies: `dupes/Opie and Anthony/1998/06/1998-06-26_O&A_7494A5EA (2).mp3`.
    With `false` it keeps its original name and subfolder. The console line
    and report say which library file it's the same as.
  - **WAIT**: a copy whose original is also in incoming; handled as a
    duplicate after the original imports.
  - **SKIP**: not in the catalog, unreadable, or the catalog entry was made
    from a different copy that isn't here. Left in place.
- After a move, the entry's `naming.filename` becomes the library name and
  `status.imported` gets a timestamp. Catalog saved after every file.
- Moves on the same drive are a rename. Across drives: copy to a `.part`
  file, check size + whole-file xxh64, rename into place, then delete the
  source.
- Empty subfolders left in incoming are removed.
- `--dry-run` moves nothing and writes no catalog changes. Every run writes
  `mogul_reports/radio-import_<date>_<time>.json` (from → to for every
  move, so an undo can be built from it later).
- `mogul_library/`, `mogul_dupes/` and `mogul_quarantine/` are in `.gitignore`.
- Not built yet: undo, and choosing *which* copy to keep by quality
  (right now the first one scanned wins).

## Radio verify (`mogul.py radio verify`)

- Walks the radio library, re-hashes every file and matches it to its
  catalog entry by audio hashes (never by name). Moves nothing.
- Results: **OK**; **FILLED** (missing audio_blake3 added); **FOUND**
  (entry not marked imported, file is in the right folder → marked
  imported); **CHANGED** (same audio, whole file differs since scan, e.g.
  tags edited; `--update` records new whole-file hashes, size, mtime);
  **MISPLACED** (wrong folder/name, left alone); **EXTRA** (second library
  file with the same audio); **COLLISION**; **UNKNOWN** (not in catalog);
  **BAD** (unreadable audio); then **MISSING** (entries marked imported
  whose file wasn't found).
- `--dry-run` writes nothing. Report: `mogul_reports/radio-verify_*.json`.
- Run it after an import, and now and then as a health check of the NAS.

## Radio fingerprint (`mogul.py radio fingerprint`)

- Adds the fingerprint digest to every catalog entry that has none (e.g.
  scanned with `scan --no-fingerprint`, which is much faster for big
  batches: fingerprinting is CPU work that depends on audio *length*).
- Finds files through the catalog: imported entries in the library at
  `<folder_template>/<filename>`; others in `mogul_incoming/radio` by
  original filename. Only used if the size matches the catalog.
- Runs several files at once: `--jobs N` (default: CPU cores minus one).
- fpcalc gives up at the first damaged mp3 frame ("Header missing"), even
  when the rest of a 4-hour show is fine. When that happens the audio is
  decoded by ffmpeg instead (skipping bad frames, like a player) and piped
  into fpcalc; the digest comes out identical. The fingerprint block then
  gets `decode_errors` (how many errors ffmpeg reported), and the console
  says "skipped N damaged spot(s)". Useful later for picking clean copies.
- If that fails too (really broken audio), records
  `status.fingerprint_failed = {date, error}` and skips it next time;
  `--retry-failed` tries again. `--dry-run` lists what it would do.
- Catalog saved after every finished file. Report:
  `mogul_reports/radio-fingerprint_*.json`.
- Suggested flow for big batches: `scan --no-fingerprint` → `import` →
  `fingerprint` (can run overnight).

## Radio quarantine (`mogul.py radio quarantine`)

- Pulls bad recordings out of the library and the main catalog so they can
  be checked by hand. Default: every entry with `status.fingerprint_failed`,
  plus entries marked bad with `radio damaged --bad`.
  `--file NAME` (repeatable) picks library files by name instead.
- The file moves to `[radio] quarantine` (default `mogul_quarantine/radio`,
  gitignored), same show/yyyy/mm layout. The entry moves out of its month
  file into `mogul_json/radio/_quarantine.json` (schema
  `mogul.radio.quarantine`) with a `quarantine` block: date, reason, where
  it was (library/incoming, relative paths only) and a damage report
  (percent empty, biggest run of 512+ zero bytes and where, empty bytes at
  the start). Typical bad download: big empty stretch at the front.
- Scan reports quarantined audio as QUARANTINED and doesn't add it; import
  SKIPs it. So the same bad copy never gets re-cataloged.
- `--restore NAME` (or `--restore all`) moves the file back and reinserts the entry (clears
  `fingerprint_failed` so the fingerprint pass retries it). `--list` shows
  what's quarantined. `--dry-run` changes nothing. Nothing is deleted.

## Radio check (`mogul.py radio check`)

- Reads every cataloged file all the way through for damage, whether or
  not it fingerprinted (fpcalc can get through damage without failing,
  e.g. 2002-08-17). Code in `mogul_core/damage.py`, shared with
  `tools/damagemap.py` and quarantine's empty-bytes report.
- MP3s: mp3val resync offsets ("MPEG stream error, resynchronized") mapped
  to show times via ffprobe's frame list, grouped into stretches (spots
  within 10 s), with a rough "audio missing" (junk bytes glued onto a
  frame / average bytes per second). Other formats: ffmpeg decoder error
  count, no times. Every file: percent empty bytes + biggest empty gap.
  mp3val's wordy warnings are shortened (VBR header counts off, VBR
  without header, mixed MPEG versions); "no tags" is ignored.
- Saves `status.check = {date, tool, spots, stretches, first, last,
  missing_seconds, warnings, empty_percent, biggest_gap, readable/error}`
  (times in seconds). Spots or unreadable also set `status.damaged`
  (found_by "check"; keeps any verdict). Skips files already checked
  unless `--recheck`, so a long run can be stopped and resumed.
- `--quarantine` checks the quarantined files instead (saved as
  `quarantine.check` in `_quarantine.json`). `--jobs N` (default up to 4;
  disk-bound). `--dry-run` reports but saves nothing.
- Reports: `mogul_reports/radio-check_*.txt` (the one to read: counts,
  CAN'T BE READ, DAMAGED worst first with one-line summaries and
  verdicts, NOT FOUND; covers everything checked so far, not just this
  run) and `.json` (every stretch).
- **Real length vs header length:** an mp3's Xing header (and so
  mediainfo and the catalog's `physical.duration`) keeps the ORIGINAL
  length even after a piece goes missing or the end is cut off. check
  stores `real_duration` (mp3val's frame count x samples per frame / sample
  rate; matches ffprobe) and `header_missing_seconds` (header frames minus
  real frames; tested exact: a 40 s cut showed 40.0 s). That catches the
  "music then suddenly dialog" skips the spot estimate can't (a clean cut
  leaves no junk, just one broken frame at the seam) and truncated files
  (mp3val: "file is truncated"). Missing >= 1 s counts as DAMAGED.
- Speed: a clean 1-hour mp3 takes ~0.3 s, a damaged one ~2 s; the NAS
  read speed is the limit.

## Same-day comparison (`tools/sameday.py YYYY-MM-DD`)

- Lists every recording of a date (library, incoming, quarantine):
  format, bitrate, length (real length from check when there is one),
  how much shorter than the longest copy, notes, check summary, verdict;
  then pairwise fingerprint digest similarity. For picking the best copy
  and spotting copies with audio missing.
- Not built yet: per-gap measurement by aligning two copies with the full
  fingerprint ("at 0:16:33, 47 s missing"); same alignment as splice repair.

## Listening list (`tools/listen.py`)

- After `radio check`: every damaged file not judged yet, worst first
  (unreadable, then most audio lost, then most spots), with library path,
  header length vs real length, and EVERY damaged stretch's show time.
  Stretch times come from the newest `radio-check_*.json` per file
  (the catalog only keeps first/last). `--all` includes judged ones,
  `--top N` limits.
- Writes `mogul_reports/listen.txt` and one Audacity label file per
  damaged file in `mogul_reports/audacity_labels/` (tab-separated
  start/end/text; labels start 2 s early; a label where the audio really
  ends when the header claims more). In Audacity: File > Import > Labels.
- Flow: `radio check` → `radio damaged --auto-ok` → `tools/listen.py` → listen → `radio damaged
  --ok/--keep/--bad` → `radio quarantine` (bad ones out, hashes kept,
  never re-added by scan).

## Radio damaged (`mogul.py radio damaged`)

- When fpcalc needs the ffmpeg fallback (scan or fingerprint pass), the
  entry gets `status.damaged = {date, decode_errors, found_by, checked}`.
- `radio damaged` lists them with the count and whether they've been
  checked; `--unchecked` shows only the ones not judged yet. Entries
  fingerprinted before the flag existed are found by
  `fingerprint.decode_errors`.
- After listening: `--ok NAME` (plays fine, stays), `--keep NAME`
  (playable but damaged, e.g. clicks/bloops; stays as the copy we have,
  wanted: a cleaner copy) or `--bad NAME` (`checked = {date, verdict}`).
  `quarantine` picks up verdict "bad" entries by default, as well as failed
  fingerprints. `--wanted` lists the "keep" ones (feeds a future
  bounty / wanted list). The list is sorted worst first.
- **Every damaged file stays marked damaged, whatever the verdict**, so
  when a clean copy turns up he knows which one to pull. `status.damaged.found`
  holds the one-line "what the system found", e.g. `4 broken spot(s) in 4
  stretch(es) between 1:04:31 and 4:06:44, ~0.0 s lost` or `only 0:36:05 of
  audio really there (header says 4:04:49, 3 h 29 min short); zeroed
  stretch of 51.9 MB at 47% through the file (about 1 h 53 min of audio)`.
  Set by `radio check`; `radio damaged` (any run) fills it in for entries
  checked before it existed. Damaged = broken spots, unreadable, an empty
  gap, or audio missing vs header (not just spots). Shown under each file
  in the `radio damaged` list.
- `--note TEXT` with `--ok/--keep/--bad` saves Mavrick's own words on
  the verdict (`checked.note`), shown in the list and listen.txt.
  A verdict never erases the damage record: `status.check` and
  `status.damaged` stay. ok = damaged but listenable, stays; keep = rough,
  stays, wanted list; bad = unlistenable / big chunks missing, quarantine.
- `--auto-ok` marks every not-yet-judged file that `radio check` timed
  with no more than `auto_ok_max_missing` (mogul.toml, default 0.5 s)
  lost, real length matching the header, and no empty gap, as ok
  (`checked = {date, verdict: ok, by: auto, rule, note}`), so listening goes to
  the real problems. `--max-missing N` overrides; `--dry-run` lists only.
  Also covers files flagged only by the fingerprint pass whose check came
  back whole (full length, no broken frames; 15 of them, mostly 2012:
  junk before the first / after the last frame); their found line says so.
  Count on 2026-11-12: 149 damaged = 134 found by check + those 15.
  Why: Mavrick listened to 2009-04-01 at 1:04:31 (4 spots, 0.0 s missing):
  a faint tap, a seam where pieces were joined. Many 2009 files look like
  this. Don't run it while `radio check` is running (both write the
  month files).
- Works on any library file, flagged or not (found_by "by hand"), e.g.
  2002-08-17_O&A_DFE66FEE: fingerprinted fine but has ~380 bad frames.
- `tools/damagemap.py FILE` shows *where* a file is damaged: mp3val's
  resync offsets mapped to show times via ffprobe's frame list, grouped
  into stretches, with a rough "audio likely missing" per stretch (junk
  bytes glued onto a frame / average bytes per second). Without mp3val it
  falls back to oversized frames (rough for VBR). Note: ffprobe glues junk
  onto the previous packet, so there are no byte gaps between packets.
- Repair without re-encoding is possible: `mp3val -f -nb FILE` drops the
  broken frames and fixes the Xing header (tested: digest identical, no
  decode errors left). But it changes the audio xxh64, so the fixed file
  no longer matches anyone else's copy of the original. Not built: if
  wanted later, a `repair` that keeps the original hashes on the entry
  ("repaired from") so other copies still match. `ffmpeg -c copy` does
  NOT fix it (bad frame is copied too).

## Radio specifics

### Incoming filenames

Pattern (in `mogul.toml`): `{code} {yyyy}-{mm}-{dd}[_{part}][ -- {notes}]`

- `O&A 1998-07-21.mp3`
- `O&A 1998-06-26 -- 1st WNEW show.mp3` (everything after ` -- ` is notes)
- `O&A 2005-03-14_02 -- Optional title.mp3` (part 2 of that day). Stored as
  `naming.part` (a number, null when absent). For multi-part days (pt1/pt2,
  hourly blocks); later these get linked as parts of one recording.
  Older entries have no `part` key: read it with `.get("part")`.

Show codes map to show names in `mogul.toml` under `[radio.shows]`
(`O&A`, `R&F`, `HSS`, more added as needed).

`tools/parsetest.py` shows how filenames are read (using the real parser and
templates) and the library name each would get, and flags problems: invalid
dates, unknown show codes, names that don't match the pattern.

### Usenet name shapes

`tools/fixnames.sh` (run inside the folder; `-n` = preview) renames
`2008-10-01-O&A-CF64k.mp3`, `2008-10-30 Thu O&A CF64k.mp3` and
`2008-10-30-O&A CF64K.mp3` to `O&A 2008-10-30 -- CF64k.mp3` (the rest of
the name becomes notes). Never overwrites.

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

## Not built yet

- Undo for imports (the import reports have every from → to).
- Fixing MISPLACED files automatically.
- Recording other copies' whole-file hashes on an entry ("also seen as"),
  so someone else's tagged copy matches by full-file hash too.
- Quality views from the catalog (best copy per date, dates with only
  low-bitrate copies). No judging during import: every distinct recording
  is kept.

## File checks (planned)

- Tags buried mid-file (merged without stripping): `ID3` header at byte
  offsets other than 0; extra `Xing`/`Info` frames; `APETAGEX`/`LYRICS200`
  not at the end. Chained OGG streams (`ogginfo`).
- Holes from bad downloads: runs of 4 KB+ zero bytes. Found in the
  2010 batch: zeroed runs of exactly 4, 8, 16, 24 MiB = download pieces
  that never arrived (file was created full size, missing pieces stay
  zero). That's why those files are full size but much shorter.
- `tools/findtags.py FILE` finds buried ID3v2/ID3v1/APE/Lyrics3 tags and
  extra Xing/Info header frames (pieces glued without stripping), each
  with its show time. Tested on 3 pieces joined with `cat`. A glued file's
  first Xing header only covers the first piece, so the header length is
  SHORTER than the real one (e.g. 2008-09-15: header 17 min, real 230).
  Mavrick's theory: the seam spots (~0.2 s "lost" each, 2008/2013 groups)
  are buried tags. Not yet confirmed on his files.
- Decode errors: `ffmpeg -v error -i FILE -f null -`; `mp3val` for MP3s.
  (Done: `radio check`. Empty gaps are reported too. Buried tags and
  chained OGG streams are still to do.)
- Damaged copies should lose to clean ones when picking which duplicate
  to keep.

## Future ideas (not now)

- **CBS/XM split era (O&A, ~2006-2009):** files come as separate feeds,
  coded `O&A-CBS` and `O&A-XM` in `[radio.shows]` (both map to "Opie and
  Anthony"; works today, sorts CBS before XM). Plan: a script to merge the
  two feeds of a day into one file plus a cue sheet, for an MKA with
  chapters later. Use the fingerprint to find where the feeds overlap so
  the cut points come out automatically. Keep the original files' hashes
  in the catalog (marked as merged) so other people's copies still match.

- **Bits / clips (community, via web UI later):** a bit = `{xxh64, start,
  end, title, tags, by}`. The xxh64 pins the exact audio, so timecodes are
  right for anyone with that file whatever its name. Use the fingerprint to
  find the offset between recordings of the same show (mp3/ogg, full/CF) so
  a bit marked on one copy shows on the others. Bits live in their own JSON
  files (per show/month), separate from the recording catalog. Overlapping
  submissions merge into one bit; the count acts as votes ("greatest bits").
  Same format feeds cue sheets and MKA chapters. Examples Mavrick gave:
  "Opie and Jimmy fight", "Anthony and Opie fight", Norton telling the
  "Draggin Wagon" caller to cancel XM, then "wait, don't cancel".
- **Internet Archive download / mirror:** a curated list (JSON in the repo)
  of known IA items Mavrick uploaded or vetted, each with size and file
  count. `mirror` pulls them into `mogul_incoming/radio` with the official
  `internetarchive` package, checks every file against IA's MD5 from
  `_files.xml` before scanning (retry on mismatch), can skip IA-made OGG
  derivatives, resumes after interruption, checks free disk space, and
  warns on big ones before starting, e.g.
  `[WARNING] THIS ARCHIVE IS 3.25TB [WARNING]` with a yes/no prompt.
  Skip files already owned: store md5/sha1 at scan time so IA's checksums
  can be matched against the catalog without downloading.
- **IA publish (`radio publish`, planned):** one IA item per library month
  folder, e.g. `mogul_library/radio/Ron and Fez/2008/10/` → item
  `MOGUL-RF-2008-10`, with every audio file plus that month's catalog JSON.
  IA identifiers only allow letters, numbers, `_`, `-`, `.` (no `&`, no
  spaces), so each show needs an IA-safe slug in `mogul.toml` (`O&A` → `OA`,
  `R&F` → `RF`); the filenames inside can keep `&`. IA guidance: max ~1,000
  files / 500 GB per item (1 TB hard limit); a month is far under that.
  Running updates are fine: `ia upload <same identifier> ...` adds or
  replaces files later. Consider `--no-derive` so IA doesn't add its own OGG
  copies. Item metadata: title like "Ron and Fez - October 2008",
  mediatype audio, date. Only library files go up, never dupes.
- **Mirror agreement (the "EULA" gag):** first run of `mirror` scrolls a
  huge fake-legalese wall that makes no sense, then offers "Press C to
  condense" which shows the short, funny, readable version (fan archive,
  don't sell it, check disk space, report mislabeled shows, the Opie
  SSD + signed WOW sticker offer). User types `agree`; saved to their
  `mogul.local.toml` so it's asked once. Nothing is sent anywhere.
- **Mirror health check:** a scheduled command (cron or systemd timer on
  protostar) that checks every published IA item still exists and still
  has every file with the expected checksum, using IA's metadata (no
  downloading). Reports e.g. "The following IA mirrors are removed:" /
  "files missing" / "checksum changed", and pushes it somewhere he'll see
  it (ntfy, Discord webhook or email). Removed items get marked in the
  index so `mirror` falls back to the secondary host. Later: secondary
  storage (Cloudflare R2 / Backblaze B2 / Wasabi; watch download/egress
  pricing, not just storage) with IA as primary.
- **Mirror index:** the list of items (`MOGUL-RF-2008-10`, `-11`, `-12`, ...)
  can be generated from the catalog itself (one per month file), published
  in the repo with file count and total size per item, so `mirror` can show
  the size warning before downloading.
- Per-hash "API" JSON per file, later filled with tags and history (who was
  on the show, etc.). Tags get stripped from files; a "rebuild tags" option
  writes them back when copying to a media player.
- Identify unknown or misdated episodes by fingerprint ("what is this
  file?"), including tracing worst-of/replay clips back to the original air
  date. Watch out for time-compressed replays (sped up 2–5%).
- Cue sheets / commercial-free cuts built by aligning a full show with its
  CF version, so only the full file needs keeping.
- **Splice repair:** when a file is damaged only in one stretch (e.g.
  2002-08-17: clean to 2:43:47, 156 bad stretches to the end, ~9 s
  missing), any other copy covering that stretch (another source, a
  clip, a worst-of replay) could fill it. Align with the fingerprint,
  splice the clean part in, keep the original's hashes on the entry.
- MKA container as a "playable zip": audio + chapters + attachments
  (manifest, cover, original tags).
- Issues and bounty list (`mogul.py issues --bounty`).
- Other modules: audiobooks (multi-file sets, "unclean" sets where one part
  is encoded differently), music (MusicBrainz), video.
- Eventual web UI and website, reading only the JSON/DB, never the media
  folders.
