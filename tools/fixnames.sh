#!/bin/bash
# Rename Usenet-style names into MOGUL's pattern:  CODE YYYY-MM-DD -- <rest>.ext
# Works for any show code (O&A, R&F, HSS, O&A-CBS ...): it takes the code
# from the name itself.
#   2008-10-01-O&A-CF64k.mp3            -> O&A 2008-10-01 -- CF64k.mp3
#   2008-10-30 Thu O&A CF64k.mp3        -> O&A 2008-10-30 -- CF64k.mp3
#   2008-10-30-O&A CF64K.mp3            -> O&A 2008-10-30 -- CF64K.mp3
#   O&A 2008-08-04 Mon O&A CF64K.mp3    -> O&A 2008-08-04 -- CF64K.mp3
#   2008-10-30 Thu R&F CF64k.mp3        -> R&F 2008-10-30 -- CF64k.mp3
#
# Which folder:
#   fixnames.sh radio         the radio incoming folder (mogul_incoming/radio,
#                             or wherever mogul.toml / mogul.local.toml point)
#   fixnames.sh /some/folder  any folder
#   fixnames.sh               the folder you're in
# Subfolders are included; each file is renamed where it sits.
#
# Options (combine freely, any order):
#   -n   only show what would happen (rename nothing)
#   -p   plain: drop the rest of the name too  ->  O&A 2008-08-04.mp3
#        (the CF64K / FS / TO part says WHICH copy it is; without -p it's
#         kept as the notes, which MOGUL stores in the catalog)
#
#   bash tools/fixnames.sh radio -n -p     preview
#   bash tools/fixnames.sh radio -p        do it
#
# Show codes it doesn't know are still renamed; scan will then flag an
# unknown code as a PROBLEM until it's added to [radio.shows] in mogul.toml.
# When the name has a weekday (Mon, Thu...), it's checked against the date.
# If they don't match (a sign the date is wrong), the file is LEFT ALONE
# so the weekday clue isn't lost; fix that one by hand. Same when the name
# has two different show codes. Never overwrites: if the new name is
# taken, the file is skipped.

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
dry=0; plain=0; target=""
for a in "$@"; do
  case "$a" in
    -n) dry=1 ;;
    -p) plain=1 ;;
    -*) echo "unknown option: $a (use -n and/or -p)"; exit 1 ;;
    *)  [ -n "$target" ] && { echo "only one folder at a time"; exit 1; }; target="$a" ;;
  esac
done

if [ -z "$target" ]; then
  dir="$PWD"
elif [ -d "$target" ]; then
  dir="$(cd "$target" && pwd)"
else
  # a media type like "radio": its incoming folder, as MOGUL's config says
  incoming="$(cd "$ROOT" && python3 -c "
import sys; sys.path.insert(0, '.')
from mogul_core.config import Config
print(Config().incoming)" 2>/dev/null)"
  if [ -z "$incoming" ]; then echo "couldn't read mogul.toml to find the incoming folder"; exit 1; fi
  dir="$incoming/$target"
  if [ ! -d "$dir" ]; then echo "no such folder: $dir"; exit 1; fi
fi
[ $dry = 1 ] && echo "(preview only, nothing renamed)"
echo "folder: $dir"

code_re='[A-Z0-9]+(&[A-Z0-9]+)?(-[A-Za-z0-9]+)?'
renamed=0; left=0
while IFS= read -r -d '' path; do
  f="${path##*/}"; here="${path%/*}"
  rel="${path#"$dir"/}"
  [[ "$f" =~ ^(($code_re)\ )?([0-9]{4}-[0-9]{2}-[0-9]{2})[-\ ]([A-Za-z]+\ )?($code_re)[-\ ](.+)\.([A-Za-z0-9]+)$ ]] || continue
  lead="${BASH_REMATCH[2]}"; day="${BASH_REMATCH[5]}"; wd="${BASH_REMATCH[6]% }"
  code="${BASH_REMATCH[7]}"; rest="${BASH_REMATCH[10]}"; ext="${BASH_REMATCH[11]}"
  if [ -n "$lead" ] && [ "$lead" != "$code" ]; then
    echo "LEFT ALONE: $rel: starts with $lead but says $code later (which show is it?)"; left=$((left+1)); continue
  fi
  if [ $plain = 1 ]; then new="$code $day.$ext"; else new="$code $day -- $rest.$ext"; fi
  if [ -n "$wd" ]; then
    real=$(LC_ALL=C date -d "$day" +%a 2>/dev/null)
    w3="${wd,,}"; w3="${w3:0:3}"
    if [ -z "$real" ]; then
      echo "LEFT ALONE: $rel: $day is not a real date"; left=$((left+1)); continue
    elif [ "${real,,}" != "$w3" ]; then
      echo "LEFT ALONE: $rel: says $wd but $day was a $real (which one is right?)"; left=$((left+1)); continue
    fi
  fi
  [ "$new" = "$f" ] && continue
  if [ -e "$here/$new" ]; then echo "SKIP (name taken): $rel -> $new"; left=$((left+1)); continue; fi
  echo "$rel  ->  $new"
  renamed=$((renamed+1))
  [ $dry = 0 ] && mv -n -- "$path" "$here/$new"
done < <(find "$dir" -type f -print0 | sort -z)

if [ $dry = 1 ]; then
  echo "would rename: $renamed   left alone / skipped: $left"
else
  echo "renamed: $renamed   left alone / skipped: $left"
fi
