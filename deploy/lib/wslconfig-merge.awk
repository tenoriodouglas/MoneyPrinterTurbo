# Merge WSL2 resource settings into a .wslconfig, preserving everything else.
#
# Usage: awk -f wslconfig-merge.awk -v memory=8GB -v swap=2GB -v processors=8 file
#
# Only keys given a non-empty value are touched. Existing keys are rewritten in
# place, missing ones are appended at the end of the [wsl2] section, and a
# missing section is created. Other sections, comments and spacing survive
# untouched, because this file is the user's, not ours.

BEGIN {
    split("memory swap processors", order, " ")
    wanted["memory"] = memory
    wanted["swap"] = swap
    wanted["processors"] = processors
    in_wsl2 = 0
    seen_wsl2 = 0
}

# Emit the keys that the [wsl2] section did not already contain.
function flush_pending(   i, key) {
    for (i = 1; i <= 3; i++) {
        key = order[i]
        if (wanted[key] != "" && !written[key]) {
            print key "=" wanted[key]
            written[key] = 1
        }
    }
}

/^[ \t]*\[/ {
    # A new section starts: close out [wsl2] before leaving it.
    if (in_wsl2) {
        flush_pending()
        # Keep a blank line between sections so the file stays readable.
        print ""
        in_wsl2 = 0
    }
    section = $0
    sub(/^[ \t]*\[[ \t]*/, "", section)
    sub(/[ \t]*\].*$/, "", section)
    if (tolower(section) == "wsl2") {
        in_wsl2 = 1
        seen_wsl2 = 1
    }
    print
    next
}

{
    if (in_wsl2) {
        line = $0
        key = line
        sub(/^[ \t]*/, "", key)
        sub(/[ \t]*=.*$/, "", key)
        key = tolower(key)
        if (key in wanted && wanted[key] != "") {
            # Keep only the first occurrence; drop later duplicates so the
            # file cannot end up with two conflicting values.
            if (!written[key]) {
                print key "=" wanted[key]
                written[key] = 1
            }
            next
        }
    }
    print
}

END {
    if (in_wsl2) {
        flush_pending()
    } else if (!seen_wsl2) {
        # Only separate from existing content; an empty file needs no blank.
        if (NR > 0) {
            print ""
        }
        print "[wsl2]"
        flush_pending()
    }
}
