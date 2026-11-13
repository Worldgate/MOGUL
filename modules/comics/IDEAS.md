# MOGUL Comics (placeholder)

Comic books: series, volumes, issues.

Nothing here runs yet: this folder is for writing ideas down until the
module gets built. It isn't listed in `mogul.py` (MEDIA_TYPES), so
`mogul.py comics ...` won't work until it is.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py comics <action>`, a catalog under
`mogul_json/comics/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- Metadata has to come from an online API: series/issue info, cover dates, creators. Needs working out which one (API keys go in `secrets.toml`, never the repo).
- Files are usually CBZ/CBR (zip/rar of page images). Same idea as radio's audio-only ID: hash the page images inside, not the archive, so a re-zipped or retagged copy still matches.
- Some CBZs carry a `ComicInfo.xml` with metadata already in it; read it when it's there.

## Decisions

(none yet)
