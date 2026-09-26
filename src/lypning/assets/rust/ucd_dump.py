"""Build-time dump of the reference CPython's own `unicodedata` tables.

Run by build.rs only for a build that carries `cap-re` (lypning-l), on the
interpreter that answered reference_probe.py, and only when that interpreter
is the named reference (REF_PY_EXACT). Every answer lypning-l's `unicodedata`
gives is read from what THIS interpreter says, so it is exact for that
reference whatever its Unicode version; nothing is remembered from another.
Writes one binary file, the path in argv[1] (read back by `src/ucd.rs`):

    b"UCD1"
    str   unidata_version
    u8    Hangul decomposition(): 0 = '' (3.9-3.12), 1 = full L V [T] (3.13+)
    list  category names;          runs  (delta start, u8 name index)
    runs  combining class          (delta start, u8 ccc)
    list  compatibility tags;      decompositions of every non-Hangul code
          point that has one       (delta cp, tag index + 1 or 0, n, n cps)
    list  composition exclusions:  two-character canonical decompositions
          that NFC does not recompose (delta cp)

Every integer is an unsigned LEB128 varint. stdlib only, and 3.9-safe.
"""
import sys
import unicodedata as u

MAX = 0x110000
SBASE, SCOUNT = 0xAC00, 11172


def varint(out, n):
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return


def text(out, s):
    b = s.encode("utf-8")
    varint(out, len(b))
    out.extend(b)


def runs(out, value):
    starts = []
    prev = None
    for cp in range(MAX):
        v = value(chr(cp))
        if v != prev:
            starts.append((cp, v))
            prev = v
    varint(out, len(starts))
    last = 0
    for cp, v in starts:
        varint(out, cp - last)
        out.append(v)
        last = cp


def main(path):
    out = bytearray(b"UCD1")
    text(out, u.unidata_version)
    lv, lvt = u.decomposition("가"), u.decomposition("각")
    if (lv, lvt) == ("", ""):
        out.append(0)
    elif (lv, lvt) == ("1100 1161", "1100 1161 11A8"):
        out.append(1)
    else:
        raise SystemExit("unknown Hangul decomposition(): %r %r" % (lv, lvt))
    cats = sorted({u.category(chr(cp)) for cp in range(MAX)})
    varint(out, len(cats))
    for c in cats:
        text(out, c)
    index = {c: i for i, c in enumerate(cats)}
    runs(out, lambda ch: index[u.category(ch)])
    runs(out, u.combining)
    decomps = []
    tags = set()
    for cp in range(MAX):
        if SBASE <= cp < SBASE + SCOUNT:
            continue
        d = u.decomposition(chr(cp))
        if not d:
            continue
        parts = d.split()
        tag = parts.pop(0) if parts[0].startswith("<") else ""
        if tag:
            tags.add(tag)
        decomps.append((cp, tag, [int(p, 16) for p in parts]))
    tags = sorted(tags)
    varint(out, len(tags))
    for t in tags:
        text(out, t)
    tag_index = {t: i + 1 for i, t in enumerate(tags)}
    tag_index[""] = 0
    varint(out, len(decomps))
    last = 0
    exclusions = []
    for cp, tag, cps in decomps:
        varint(out, cp - last)
        last = cp
        varint(out, tag_index[tag])
        varint(out, len(cps))
        for c in cps:
            varint(out, c)
        if not tag and len(cps) == 2 and u.normalize("NFC", chr(cps[0]) + chr(cps[1])) != chr(cp):
            exclusions.append(cp)
    varint(out, len(exclusions))
    last = 0
    for cp in exclusions:
        varint(out, cp - last)
        last = cp
    with open(path, "wb") as fh:
        fh.write(bytes(out))


if __name__ == "__main__":
    main(sys.argv[1])
