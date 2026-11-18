# MOGUL Movies (placeholder)

Movies (the library on abyss).

Started 2026-11-18: `mogul.py movie tmdb` downloads and keeps each
movie's TMDB info (see CLAUDE.md, "Movie TMDB"). Everything else below is
still ideas.

When it gets built it uses the shared core in `mogul_core/` (config,
hashing, JSON store, fingerprint, damage checks, progress) and follows the
same shape as radio: `mogul.py movie <action>`, a catalog under
`mogul_json/movie/`, scan (read-only) then import (move/rename/sort,
dupes to a folder, never deleted).

## Ideas

- There's already a separate movie scanner that cross-checks file runtimes against TMDB; fold its ideas in when this starts.

- Index movies by actor, director, writer, composer... from the saved
  TMDB files (Mavrick had this before; the old script is lost).
- Movie identifier (credits OCR / video-track hash): Mavrick is looking
  for his old script.

## Decisions

- TMDB data is fetched once per movie with append_to_response and kept
  as-is in `mogul_cache/tmdb/movie/` (gitignored, re-downloadable).
- API key lives in `secrets.toml`, never the repo.
