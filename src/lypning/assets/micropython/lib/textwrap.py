# lypning-mp frozen shim: textwrap (greedy word wrap, no hyphenation, no
# break_long_words). See micropython/lib/README.md.


def dedent(text):
    margin = None
    for line in text.split("\n"):
        stripped = line.lstrip()
        if not stripped:
            continue
        ind = line[: len(line) - len(stripped)]
        if margin is None:
            margin = ind
        elif ind.startswith(margin):
            pass
        elif margin.startswith(ind):
            margin = ind
        else:
            k = 0
            while k < len(margin) and k < len(ind) and margin[k] == ind[k]:
                k += 1
            margin = margin[:k]
    if not margin:
        return text
    out = []
    for line in text.split("\n"):
        if not line.strip():
            out.append("")
        else:
            out.append(line[len(margin) :])
    return "\n".join(out)


def wrap(text, width=70, **kw):
    lines = []
    cur = ""
    for w in text.split():
        if not cur:
            cur = w
        elif len(cur) + 1 + len(w) <= width:
            cur += " " + w
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def fill(text, width=70, **kw):
    return "\n".join(wrap(text, width))


def shorten(text, width, placeholder=" [...]"):
    cur = ""
    words = text.split()
    for i in range(len(words)):
        cand = words[i] if not cur else cur + " " + words[i]
        if len(cand) <= width:
            cur = cand
            continue
        while cur and len(cur) + len(placeholder) > width:
            cur = cur.rsplit(" ", 1)[0] if " " in cur else ""
        return cur + placeholder if cur else placeholder.lstrip()
    return cur


def indent(text, prefix, predicate=None):
    out = []
    for line in text.splitlines(True):
        keep = predicate(line) if predicate else line.strip()
        out.append(prefix + line if keep else line)
    return "".join(out)
