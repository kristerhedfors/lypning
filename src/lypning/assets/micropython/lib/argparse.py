# lypning-mp frozen shim: argparse — the subset an agent's one-liner reaches for.
# See micropython/lib/README.md.
#
# ArgumentParser/add_argument/parse_args, positionals with every nargs form,
# long/short/=-joined/bundled optionals, `--`, the six string actions, and the
# error paths (unrecognised argument, missing required, bad type=, bad choice)
# which exit 2 the way CPython does. The matcher is CPython's own
# _parse_known_args()/_get_values() transcribed; only _match_arguments_partial's
# regex is replaced, by the equivalent greedy assignment over the same
# min/max bounds, because importing re here would cost bytes and re1.5 cannot
# express the pattern anyway.
#
# Everything NOT implemented is refused BY NAME through _u() (exit 90), never
# accepted and ignored: -h/--help (our layout is not CPython's, so printing it
# would MISMATCH on stdout), subparsers, groups, parse_known_args, custom action
# classes, nargs=REMAINDER, FileType, formatters, fromfile_prefix_chars.
# Unknown ArgumentParser attributes go the same way via __getattr__.

import sys

SUPPRESS = "==SUPPRESS=="
OPTIONAL = "?"
ZERO_OR_MORE = "*"
ONE_OR_MORE = "+"
REMAINDER = "..."
PARSER = "A..."

_ACTS = ("store", "store_true", "store_false", "store_const", "append",
         "count", "help")
_KW = ("action", "nargs", "const", "default", "type", "choices", "required",
       "help", "metavar", "dest")


def _u(kind, what):
    raise NotImplementedError("lypning-mp: unsupported: " + kind + ": " + what)


class ArgumentError(Exception):
    pass


class ArgumentTypeError(Exception):
    pass


class _Refused:
    def __init__(self, *a, **k):
        _u("attribute", "argparse." + type(self).__name__)


class Action(_Refused): pass
class FileType(_Refused): pass
class HelpFormatter(_Refused): pass
class RawTextHelpFormatter(HelpFormatter): pass
class RawDescriptionHelpFormatter(HelpFormatter): pass
class ArgumentDefaultsHelpFormatter(HelpFormatter): pass
class MetavarTypeHelpFormatter(HelpFormatter): pass
class BooleanOptionalAction(_Refused): pass


def _isident(s):
    if not s or s[0].isdigit():
        return False
    for c in s:
        if not (c.isalpha() or c.isdigit() or c == "_"):
            return False
    return True


def _isneg(s):
    # CPython's _negative_number_matcher: ^-\d+$|^-\d*\.\d+$
    if len(s) < 2 or s[0] != "-":
        return False
    b = s[1:]
    if b.isdigit():
        return True
    i, sep, f = b.partition(".")
    return bool(sep) and f.isdigit() and (not i or i.isdigit())


def _name(a):
    if a.option_strings:
        return "/".join(a.option_strings)
    if a.metavar is not None:
        return a.metavar
    return a.dest


def _bounds(n):
    # how many 'A' tokens this nargs may consume: (min, max); -1 max = no limit
    if n is None:
        return (1, 1)
    if n == OPTIONAL:
        return (0, 1)
    if n == ZERO_OR_MORE:
        return (0, -1)
    if n == ONE_OR_MORE:
        return (1, -1)
    return (n, n)


# Namespace attribute ORDER. MicroPython's instance map is an open-addressing
# hash map — unlike the Python-level dict, it is NOT insertion ordered — so
# self.__dict__ cannot carry the order CPython's Namespace repr prints. Keeping
# the order in the instance would put an extra key in __dict__/vars(), which is
# a worse divergence than keeping it out here.
_ORD = []


def _ord(ns):
    for e in _ORD:
        if e[0] is ns:
            return e[1]
    e = (ns, [])
    _ORD.append(e)
    return e[1]


class Namespace:
    def __init__(self, **kw):
        for k in kw:
            setattr(self, k, kw[k])

    def __setattr__(self, k, v):
        o = _ord(self)
        if k not in o:
            o.append(k)
        object.__setattr__(self, k, v)

    def __eq__(self, other):
        return isinstance(other, Namespace) and self.__dict__ == other.__dict__

    def __contains__(self, k):
        return hasattr(self, k)

    def __repr__(self):
        parts = []
        star = {}
        for k in _ord(self):
            if not hasattr(self, k):
                continue
            if _isident(k):
                parts.append("%s=%r" % (k, getattr(self, k)))
            else:
                star[k] = getattr(self, k)
        if star:
            parts.append("**%r" % (star,))
        return "Namespace(" + ", ".join(parts) + ")"


class _Act:
    def __init__(self, opts, dest, act, nargs, const, default, type_, choices,
                 required, help_, metavar):
        self.option_strings = opts
        self.dest = dest
        self.act = act
        self.nargs = nargs
        self.const = const
        self.default = default
        self.type = type_
        self.choices = choices
        self.required = required
        self.help = help_
        self.metavar = metavar

    def __call__(self, parser, ns, values, ostr=None):
        a = self.act
        if a == "store":
            setattr(ns, self.dest, values)
        elif a == "append":
            items = getattr(ns, self.dest, None)
            if items is None:
                items = []
            elif type(items) is list:
                items = items[:]
            else:
                _u("argument", "argparse.add_argument(action=append, default=)")
            items.append(values)
            setattr(ns, self.dest, items)
        elif a == "count":
            c = getattr(ns, self.dest, None)
            setattr(ns, self.dest, (0 if c is None else c) + 1)
        elif a == "help":
            _u("argument", "argparse.add_argument(action=help)")
        else:
            setattr(ns, self.dest, self.const)


class ArgumentParser:
    def __init__(self, prog=None, description=None, add_help=True,
                 allow_abbrev=True, usage=None, **kw):
        for k in kw:
            _u("argument", "argparse.ArgumentParser(" + k + "=)")
        self.prog = sys.argv[0].rsplit("/", 1)[-1] if prog is None else prog
        self.description = description
        self.usage = usage
        self.allow_abbrev = allow_abbrev
        self._actions = []
        self._opts = {}
        self._defaults = {}
        self._neg = False
        if add_help:
            self.add_argument("-h", "--help", action="help", default=SUPPRESS,
                              help="show this help message and exit")

    def __getattr__(self, name):
        # add_subparsers, add_argument_group, add_mutually_exclusive_group,
        # parse_known_args, parse_intermixed_args, print_help, format_usage …
        if name[:2] == "__":
            raise AttributeError(name)
        _u("attribute", "argparse.ArgumentParser." + name)

    # ---- building -------------------------------------------------------
    def add_argument(self, *args, **kw):
        for k in kw:
            if k not in _KW:
                _u("argument", "argparse.add_argument(" + k + "=)")
        act = kw.get("action", "store")
        if not isinstance(act, str):
            _u("argument", "argparse.add_argument(action=<class>)")
        if act not in _ACTS:
            _u("argument", "argparse.add_argument(action=" + act + ")")
        nargs = kw.get("nargs")
        if nargs == REMAINDER or nargs == PARSER:
            _u("argument", "argparse.add_argument(nargs=REMAINDER)")
        if nargs is not None and not isinstance(nargs, int) and \
                nargs not in (OPTIONAL, ZERO_OR_MORE, ONE_OR_MORE):
            _u("argument", "argparse.add_argument(nargs=%r)" % (nargs,))
        if isinstance(kw.get("metavar"), tuple):
            _u("argument", "argparse.add_argument(metavar=<tuple>)")

        if not args or (len(args) == 1 and args[0][0] != "-"):
            if args and "dest" in kw:
                raise ValueError("dest supplied twice for positional argument")
            if "required" in kw:
                raise TypeError("'required' is an invalid argument for positionals")
            dest = args[0]
            opts = []
            required = nargs not in (OPTIONAL, ZERO_OR_MORE) or \
                (nargs == ZERO_OR_MORE and "default" not in kw)
        else:
            opts = list(args)
            for o in opts:
                if o[0] != "-":
                    raise ValueError("invalid option string %r: must start "
                                     "with a character '-'" % o)
            dest = kw.get("dest")
            if dest is None:
                longs = [o for o in opts if len(o) > 1 and o[1] == "-"]
                d = (longs[0] if longs else opts[0]).lstrip("-")
                if not d:
                    raise ValueError("dest= is required for options like %r" % opts[0])
                dest = d.replace("-", "_")
            required = kw.get("required", False)

        const = kw.get("const")
        default = kw.get("default")
        hasdef = "default" in kw
        if not hasdef and dest in self._defaults:
            default, hasdef = self._defaults[dest], True
        if act == "store_true":
            const, nargs = True, 0
            if not hasdef:
                default = False
        elif act == "store_false":
            const, nargs = False, 0
            if not hasdef:
                default = True
        elif act in ("store_const", "count", "help"):
            nargs = 0
        else:
            if nargs == 0:
                raise ValueError("nargs for store actions must be != 0")
            if const is not None and nargs != OPTIONAL:
                raise ValueError("nargs must be %r to supply const" % OPTIONAL)
        t = kw.get("type")
        if t is not None and not callable(t):
            raise ValueError("%r is not callable" % (t,))
        a = _Act(opts, dest, act, nargs, const, default, t, kw.get("choices"),
                 required, kw.get("help"), kw.get("metavar"))
        self._actions.append(a)
        for o in opts:
            if o in self._opts:
                raise ArgumentError("conflicting option string: " + o)
            self._opts[o] = a
            if _isneg(o):
                self._neg = True
        return a

    def set_defaults(self, **kw):
        self._defaults.update(kw)
        for a in self._actions:
            if a.dest in kw:
                a.default = kw[a.dest]

    def get_default(self, dest):
        for a in self._actions:
            if a.dest == dest and a.default is not None:
                return a.default
        return self._defaults.get(dest)

    # ---- reporting ------------------------------------------------------
    def _usage(self):
        if self.usage is not None:
            return "usage: " + self.usage.replace("%(prog)s", self.prog) + "\n"
        p = [self.prog]
        for a in self._actions:
            if a.option_strings:
                s = a.option_strings[0]
                if a.nargs != 0:
                    s += " " + (a.metavar or a.dest.upper())
                p.append(s if a.required else "[" + s + "]")
            else:
                m = a.metavar or a.dest
                if a.nargs == OPTIONAL:
                    m = "[" + m + "]"
                elif a.nargs == ZERO_OR_MORE:
                    m = "[" + m + " ...]"
                elif a.nargs == ONE_OR_MORE:
                    m = m + " [" + m + " ...]"
                p.append(m)
        return "usage: " + " ".join(p) + "\n"

    def exit(self, status=0, message=None):
        if message:
            sys.stderr.write(message)
        sys.exit(status)

    def error(self, message):
        sys.stderr.write(self._usage())
        self.exit(2, "%s: error: %s\n" % (self.prog, message))

    # ---- parsing --------------------------------------------------------
    def parse_args(self, args=None, namespace=None):
        ns, extras = self._parse(args, namespace)
        if extras:
            self.error("unrecognized arguments: " + " ".join(extras))
        return ns

    def _parse(self, args, ns):
        args = sys.argv[1:] if args is None else list(args)
        if ns is None:
            ns = Namespace()
        for a in self._actions:
            if a.dest is not SUPPRESS and not hasattr(ns, a.dest) \
                    and a.default is not SUPPRESS:
                setattr(ns, a.dest, a.default)
        for d in self._defaults:
            if not hasattr(ns, d):
                setattr(ns, d, self._defaults[d])
        try:
            return self._known(args, ns)
        except ArgumentError as e:
            self.error(str(e))

    def _known(self, argv, ns):
        opt_idx = {}
        pat = []
        i, n = 0, len(argv)
        while i < n:
            if argv[i] == "--":
                pat.append("-")
                for i in range(i + 1, n):
                    pat.append("A")
                break
            t = self._parse_optional(argv[i])
            if t is None:
                pat.append("A")
            else:
                opt_idx[i] = t
                pat.append("O")
            i += 1
        pat = "".join(pat)
        seen, extras = [], []

        def take(a, strs, ostr=None):
            if a not in seen:
                seen.append(a)
            v = self._get_values(a, strs)
            if v is not SUPPRESS:
                a(self, ns, v, ostr)

        pos = [a for a in self._actions if not a.option_strings]
        start = 0
        maxo = max(opt_idx) if opt_idx else -1
        while start <= maxo:
            nxt = min([k for k in opt_idx if k >= start])
            if start != nxt:
                end = self._eat_pos(pos, argv, pat, start, take)
                if end > start:
                    start = end
                    continue
                start = end
            if start not in opt_idx:
                extras.extend(argv[start:nxt])
                start = nxt
            start = self._eat_opt(opt_idx, argv, pat, start, take, extras)
        stop = self._eat_pos(pos, argv, pat, start, take)
        extras.extend(argv[stop:])

        missing = []
        for a in self._actions:
            if a not in seen:
                if a.required:
                    missing.append(_name(a))
                elif a.default is not None and isinstance(a.default, str) \
                        and hasattr(ns, a.dest) \
                        and a.default is getattr(ns, a.dest):
                    setattr(ns, a.dest, self._get_value(a, a.default))
        if missing:
            self.error("the following arguments are required: " +
                       ", ".join(missing))
        return ns, extras

    def _parse_optional(self, s):
        if not s or s[0] != "-":
            return None
        if s in self._opts:
            return (self._opts[s], s, None, None)
        if len(s) == 1:
            return None
        o, sep, ea = s.partition("=")
        if sep and o in self._opts:
            return (self._opts[o], o, sep, ea)
        tups = self._opt_tuples(s)
        if len(tups) > 1:
            self.error("ambiguous option: %s could match %s"
                       % (s, ", ".join([t[1] for t in tups])))
        elif len(tups) == 1:
            return tups[0]
        if _isneg(s) and not self._neg:
            return None
        if " " in s:
            return None
        return (None, s, None, None)

    def _opt_tuples(self, s):
        r = []
        if s[1] == "-":
            if self.allow_abbrev:
                pre, sep, ea = s.partition("=")
                if not sep:
                    sep = ea = None
                for o in self._opts:
                    if o.startswith(pre):
                        r.append((self._opts[o], o, sep, ea))
        else:
            for o in self._opts:
                if o == s[:2]:
                    r.append((self._opts[o], o, "", s[2:]))
                elif o.startswith(s):
                    r.append((self._opts[o], o, None, None))
        return r

    def _match_opt(self, a, pat):
        mn, mx = _bounds(a.nargs)
        k = 0
        while k < len(pat) and pat[k] == "A" and (mx < 0 or k < mx):
            k += 1
        if k < mn:
            raise ArgumentError("argument %s: expected %s argument(s)"
                                % (_name(a), mn))
        return k

    def _eat_opt(self, opt_idx, argv, pat, start, take, extras):
        a, ostr, sep, ea = opt_idx[start]
        tuples = []
        while True:
            if a is None:
                extras.append(argv[start])
                return start + 1
            if ea is not None:
                c = self._match_opt(a, "A")
                if c == 0 and ostr[1] != "-" and ea != "":
                    # -xyz is -x -y -z when the leading ones take no argument
                    if sep or ea[0] == "-":
                        raise ArgumentError("ignored explicit argument %r" % ea)
                    ch = ostr[0]
                    tuples.append((a, [], ostr))
                    ostr = ch + ea[0]
                    if ostr in self._opts:
                        a = self._opts[ostr]
                        ea = ea[1:]
                        if not ea:
                            sep = ea = None
                        elif ea[0] == "=":
                            sep, ea = "=", ea[1:]
                        else:
                            sep = ""
                    else:
                        extras.append(ch + ea)
                        stop = start + 1
                        break
                elif c == 1:
                    stop = start + 1
                    tuples.append((a, [ea], ostr))
                    break
                else:
                    raise ArgumentError("ignored explicit argument %r" % ea)
            else:
                s2 = start + 1
                c = self._match_opt(a, pat[s2:])
                stop = s2 + c
                tuples.append((a, argv[s2:stop], ostr))
                break
        for t in tuples:
            take(t[0], t[1], t[2])
        return stop

    def _eat_pos(self, pos, argv, pat, start, take):
        counts = self._match_partial(pos, pat[start:])
        for j in range(len(counts)):
            c = counts[j]
            take(pos[j], argv[start:start + c])
            start += c
        del pos[:len(counts)]
        return start

    def _match_partial(self, actions, pat):
        # CPython concatenates each action's nargs regex and re.match()es it
        # against the pattern, shortening the action list until one matches.
        # Every pattern here consumes a prefix of the leading A/- run, so the
        # greedy-with-backtracking result is exactly: give each action the most
        # it can take while reserving the minimum the later ones still need.
        run = 0
        while run < len(pat) and pat[run] != "O":
            run += 1
        seg = pat[:run]
        avail = seg.count("A")
        for i in range(len(actions), 0, -1):
            bs = [_bounds(a.nargs) for a in actions[:i]]
            need = 0
            for b in bs:
                need += b[0]
            if need > avail:
                continue
            out, left, p = [], avail, 0
            for b in bs:
                need -= b[0]
                t = left - need
                if b[1] >= 0 and t > b[1]:
                    t = b[1]
                left -= t
                # translate an A-count into a token count, swallowing the '--'
                # marker the way CPython's surrounding `-*` does
                q, got = p, 0
                while q < run and seg[q] == "-":
                    q += 1
                while got < t:
                    while q < run and seg[q] == "-":
                        q += 1
                    q += 1
                    got += 1
                while q < run and seg[q] == "-":
                    q += 1
                out.append(q - p)
                p = q
            return out
        return []

    def _get_values(self, a, strs):
        if not a.option_strings:
            try:
                strs.remove("--")
            except ValueError:
                pass
        if not strs and a.nargs == OPTIONAL:
            v = a.const if a.option_strings else a.default
            if isinstance(v, str):
                v = self._get_value(a, v)
                self._check(a, v)
        elif not strs and a.nargs == ZERO_OR_MORE and not a.option_strings:
            v = a.default if a.default is not None else strs
            self._check(a, v)
        elif len(strs) == 1 and a.nargs in (None, OPTIONAL):
            v = self._get_value(a, strs[0])
            self._check(a, v)
        else:
            v = [self._get_value(a, x) for x in strs]
            for x in v:
                self._check(a, x)
        return v

    def _get_value(self, a, s):
        if a.type is None:
            return s
        try:
            return a.type(s)
        except ArgumentTypeError as e:
            raise ArgumentError("argument %s: %s" % (_name(a), e))
        except (TypeError, ValueError):
            raise ArgumentError("argument %s: invalid %s value: %r"
                                % (_name(a),
                                   getattr(a.type, "__name__", "?"), s))

    def _check(self, a, v):
        if a.choices is not None and v not in a.choices:
            raise ArgumentError("argument %s: invalid choice: %r (choose from %s)"
                                % (_name(a), v,
                                   ", ".join([repr(c) for c in a.choices])))
