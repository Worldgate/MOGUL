# MOGUL Manga (placeholder)

Manga: series, volumes, chapters.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py manga ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py manga <action>`, a catalog under
`mogul_json/manga/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Metadata from an online API, like comics; needs working out which one. Keys in `secrets.toml`.
- Chapters vs volumes: a series may be collected both ways; scanlation group matters like fansub groups do for anime.
- Same CBZ page-image hashing as comics; could share that code.

## Decisions

(none yet)
