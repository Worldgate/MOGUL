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
# Run it inside the folder with the files.
#   -n   only show what would happen (rename nothing)
#   -p   plain: drop the rest of the name too  ->  O&A 2008-08-04.mp3
# Show codes it doesn't know are still renamed; scan will then flag an
# unknown code as a PROBLEM until it's added to [radio.shows] in mogul.toml.
#        (the CF64K / FS / TO part says WHICH copy it is; without -p it's
#         kept as the notes, which MOGUL stores in the catalog)
# Flags can be combined: -n -p
#
# When the name has a weekday (Mon, Thu...), it's checked against the date.
# If they don't match (a sign the date is wrong), the file is LEFT ALONE
# so the weekday clue isn't lost; fix that one by hand.
# Never overwrites: if the new name is taken, the file is skipped.
dry=0; plain=0
for a in "$@"; do
  case "$a" in
    -n) dry=1 ;;
    -p) plain=1 ;;
    *) echo "unknown option: $a (use -n and/or -p)"; exit 1 ;;
  esac
done
shopt -s nullglob
for f in *; do
  [ -f "$f" ] || continue
  code_re='[A-Z0-9]+(&[A-Z0-9]+)?(-[A-Za-z0-9]+)?'
  if [[ "$f" =~ ^(($code_re)\ )?([0-9]{4}-[0-9]{2}-[0-9]{2})[-\ ]([A-Za-z]+\ )?($code_re)[-\ ](.+)\.([A-Za-z0-9]+)$ ]]; then
    lead="${BASH_REMATCH[2]}"; day="${BASH_REMATCH[5]}"; wd="${BASH_REMATCH[6]% }"
    code="${BASH_REMATCH[7]}"; rest="${BASH_REMATCH[10]}"; ext="${BASH_REMATCH[11]}"
    if [ -n "$lead" ] && [ "$lead" != "$code" ]; then
      echo "LEFT ALONE: $f: starts with $lead but says $code later (which show is it?)"; continue
    fi
    if [ $plain = 1 ]; then new="$code $day.$ext"; else new="$code $day -- $rest.$ext"; fi
    if [ -n "$wd" ]; then
      real=$(LC_ALL=C date -d "$day" +%a 2>/dev/null)
      w3="${wd,,}"; w3="${w3:0:3}"
      if [ -z "$real" ]; then
        echo "LEFT ALONE: $f: $day is not a real date"; continue
      elif [ "${real,,}" != "$w3" ]; then
        echo "LEFT ALONE: $f: says $wd but $day was a $real (which one is right?)"; continue
      fi
    fi
    if [ "$new" = "$f" ]; then continue; fi
    if [ -e "$new" ]; then echo "SKIP (name taken): $f -> $new"; continue; fi
    echo "$f  ->  $new"
    [ $dry = 0 ] && mv -n -- "$f" "$new"
  fi
done
