#!/bin/bash
# Fingerprint, hash and check anything new, then save the catalog to GitHub.
#   bash tools/update.sh        (works from any folder)
cd "$(dirname "$0")/.." || exit 1
python3 mogul.py radio fingerprint
python3 mogul.py radio hashes
python3 mogul.py radio check
git add -A
git commit -m "update" || echo "(nothing new to commit)"
git pull --rebase && git push
