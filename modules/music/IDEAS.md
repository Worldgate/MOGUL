# MOGUL Music (placeholder)

Albums, singles, compilations.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py music ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py music <action>`, a catalog under
`mogul_json/music/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Identify with MusicBrainz (release / recording IDs) alongside the xxh64 audio ID.
- Same audio-only xxh64 + audio_blake3 as radio, so retagged copies still match.

## Decisions

(none yet)
