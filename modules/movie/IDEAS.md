# MOGUL Movies (placeholder)

Movies (the library on abyss).

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py movie ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py movie <action>`, a catalog under
`mogul_json/movie/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- There's already a separate movie scanner that cross-checks file runtimes against TMDB; fold its ideas in when this starts.

## Decisions

(none yet)
