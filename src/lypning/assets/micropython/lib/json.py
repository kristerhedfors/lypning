# lypning-mp frozen shim: json. Parsing stays on MicroPython's C json (aliased as
# `_json` by the variant); serialisation is reimplemented because the C
# dumps() has no indent/sort_keys/ensure_ascii and does not escape non-ASCII.
# See micropython/lib/README.md.
import ujson as _json


class JSONDecodeError(ValueError):
    pass


def loads(s, **kw):
    try:
        return _json.loads(s)
    except ValueError as e:
        raise JSONDecodeError(str(e))


def load(fp, **kw):
    try:
        return _json.load(fp)
    except ValueError as e:
        raise JSONDecodeError(str(e))


_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
    "\b": "\\b",
    "\f": "\\f",
}


def _quote(s, ensure_ascii):
    out = ['"']
    for c in s:
        e = _ESCAPES.get(c)
        if e is not None:
            out.append(e)
            continue
        o = ord(c)
        if o < 0x20:
            out.append("\\u%04x" % o)
        elif o < 0x7F or not ensure_ascii:
            out.append(c)
        elif o <= 0xFFFF:
            out.append("\\u%04x" % o)
        else:
            o -= 0x10000
            out.append("\\u%04x\\u%04x" % (0xD800 + (o >> 10), 0xDC00 + (o & 0x3FF)))
    out.append('"')
    return "".join(out)


def _key(k):
    if isinstance(k, str):
        return k
    if k is True:
        return "true"
    if k is False:
        return "false"
    if k is None:
        return "null"
    if isinstance(k, int) or isinstance(k, float):
        return _num(k)
    raise TypeError("keys must be str, int, float, bool or None")


def _num(o):
    if isinstance(o, float):
        if o != o:
            return "NaN"
        if o == float("inf"):
            return "Infinity"
        if o == float("-inf"):
            return "-Infinity"
    return repr(o)


def _indent_str(indent):
    return indent if isinstance(indent, str) else " " * indent


def _write(out, o, indent, sort_keys, isep, ksep, ea, default, cur):
    if o is True:
        out.append("true")
    elif o is False:
        out.append("false")
    elif o is None:
        out.append("null")
    elif isinstance(o, str):
        out.append(_quote(o, ea))
    elif isinstance(o, int) or isinstance(o, float):
        out.append(_num(o))
    elif isinstance(o, dict):
        if not o:
            out.append("{}")
            return
        keys = sorted(o) if sort_keys else list(o)
        if indent is None:
            out.append("{")
            sep = ""
            for k in keys:
                out.append(sep)
                out.append(_quote(_key(k), ea))
                out.append(ksep)
                _write(out, o[k], indent, sort_keys, isep, ksep, ea, default, cur)
                sep = isep
            out.append("}")
        else:
            inner = cur + _indent_str(indent)
            out.append("{\n" + inner)
            sep = ""
            for k in keys:
                out.append(sep)
                out.append(_quote(_key(k), ea))
                out.append(ksep)
                _write(out, o[k], indent, sort_keys, isep, ksep, ea, default, inner)
                sep = isep + "\n" + inner
            out.append("\n" + cur + "}")
    elif isinstance(o, list) or isinstance(o, tuple):
        if not o:
            out.append("[]")
            return
        if indent is None:
            out.append("[")
            sep = ""
            for v in o:
                out.append(sep)
                _write(out, v, indent, sort_keys, isep, ksep, ea, default, cur)
                sep = isep
            out.append("]")
        else:
            inner = cur + _indent_str(indent)
            out.append("[\n" + inner)
            sep = ""
            for v in o:
                out.append(sep)
                _write(out, v, indent, sort_keys, isep, ksep, ea, default, inner)
                sep = isep + "\n" + inner
            out.append("\n" + cur + "]")
    elif default is not None:
        _write(out, default(o), indent, sort_keys, isep, ksep, ea, default, cur)
    else:
        raise TypeError(
            "Object of type " + type(o).__name__ + " is not JSON serializable"
        )


def dumps(
    obj,
    indent=None,
    sort_keys=False,
    separators=None,
    ensure_ascii=True,
    default=None,
    **kw
):
    if separators is None:
        isep = "," if indent is not None else ", "
        ksep = ": "
    else:
        isep, ksep = separators
    out = []
    _write(out, obj, indent, sort_keys, isep, ksep, ensure_ascii, default, "")
    return "".join(out)


def dump(obj, fp, **kw):
    fp.write(dumps(obj, **kw))
