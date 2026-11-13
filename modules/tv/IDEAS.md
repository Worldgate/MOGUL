# MOGUL TV (placeholder)

TV series: shows, seasons, episodes.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py tv ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py tv <action>`, a catalog under
`mogul_json/tv/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Video files: the ID would be taken over the video+audio packets only (no container/tags), like radio's audio-only xxh64.

## Decisions

(none yet)
