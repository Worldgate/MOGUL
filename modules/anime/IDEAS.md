# MOGUL Anime (placeholder)

Anime series and movies.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py anime ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py anime <action>`, a catalog under
`mogul_json/anime/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Could share most of the TV module (seasons/episodes); split out only what's different (fansub groups, absolute episode numbers, specials/OVAs).

## Decisions

(none yet)
