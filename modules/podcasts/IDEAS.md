# MOGUL Podcasts (placeholder)

Podcast episodes, from each show's RSS feed.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py podcasts ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py podcasts <action>`, a catalog under
`mogul_json/podcasts/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- The feed is just XML: every episode has a title, date (`pubDate`), a unique `guid` and the audio file's URL (`enclosure`). Easy to read with Python's built-in XML parser.
- Very close to radio: shows, episodes by air date, audio files. Could reuse most of the radio module (naming, scan, import, check).
- Use the feed's `guid` alongside the audio-only xxh64, so a re-uploaded or retagged episode still matches.
- Keep a copy of the feed XML in the catalog: feeds get edited or vanish, and old episodes drop off the end.

## Decisions

(none yet)
