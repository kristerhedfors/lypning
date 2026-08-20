# lypning-mp frozen shim: csv — the excel dialect plus every format parameter that
# changes what a row looks like: delimiter, quotechar, escapechar, doublequote,
# skipinitialspace, lineterminator, quoting, strict. See micropython/lib/README.md.
#
# The writer is CPython's Modules/_csv.c join_append_data() transcribed and the
# reader is its parse_process_char() state machine, both diffed against CPython
# 3.11 over a parameter grid. They are transcribed rather than approximated
# because "declared but ignored" is the bug class this file already shipped
# once: quoting= and escapechar= were swallowed by **kw, so
# csv.writer(f, quoting=csv.QUOTE_ALL) wrote a,b instead of "a","b" at exit 0.
# Anything still not implemented — any other dialect, any other keyword — is
# refused BY NAME through _unsupported() rather than accepted and ignored.

QUOTE_MINIMAL = 0
QUOTE_ALL = 1
QUOTE_NONNUMERIC = 2
QUOTE_NONE = 3

# Reader states, following _csv.c: start of record, start of field, escaped
# char, in field, in quoted field, escape in quoted field, quote in quoted
# field, eat \r\n, after an escaped \r\n.
_SR, _SF, _EC, _IF, _IQ, _EQ, _QQ, _CR, _AC = 0, 1, 2, 3, 4, 5, 6, 7, 8

# What QUOTE_NONNUMERIC leaves unquoted: CPython asks PyNumber_Check(). bool is
# listed explicitly because MicroPython's bool is NOT an int subclass, so
# isinstance(True, int) is False here and True would otherwise come out quoted.
_NUM = (int, float, bool, complex)


class Error(Exception):
    pass


def _unsupported(what):
    raise NotImplementedError("lypning-mp: unsupported: argument: csv(" + what + ")")


def _one(name, v, none_ok=False):
    if v is None and none_ok:
        return v
    if not isinstance(v, str):
        raise TypeError('"' + name + '" must be string, not ' + type(v).__name__)
    if len(v) != 1:
        raise TypeError('"' + name + '" must be a 1-character string')
    return v


class _Dialect:
    def __init__(self, dialect="excel", delimiter=",", quotechar='"',
                 escapechar=None, doublequote=True, skipinitialspace=False,
                 lineterminator="\r\n", quoting=None, strict=False, **kw):
        for k in kw:
            _unsupported(k)
        if dialect != "excel":
            _unsupported("dialect=" + str(dialect))
        if quoting is None:
            # CPython: an explicit quotechar=None with no quoting= means
            # QUOTE_NONE; otherwise the excel default.
            quoting = QUOTE_NONE if quotechar is None else QUOTE_MINIMAL
        if quoting not in (QUOTE_MINIMAL, QUOTE_ALL, QUOTE_NONNUMERIC,
                           QUOTE_NONE):
            raise TypeError('bad "quoting" value')
        if quoting != QUOTE_NONE and quotechar is None:
            raise TypeError("quotechar must be set if quoting enabled")
        if not isinstance(lineterminator, str):
            raise TypeError('"lineterminator" must be a string')
        self.delimiter = _one("delimiter", delimiter)
        self.quotechar = _one("quotechar", quotechar, True)
        self.escapechar = _one("escapechar", escapechar, True)
        self.doublequote = doublequote
        self.skipinitialspace = skipinitialspace
        self.lineterminator = lineterminator
        self.quoting = quoting
        self.strict = strict


class _Writer:
    def __init__(self, f, dialect):
        self._f = f
        self.dialect = dialect

    def _record(self, row):
        d = self.dialect
        # The characters _csv.c treats as special: delimiter, escapechar,
        # quotechar, every character of the line terminator, and \r and \n
        # unconditionally (measured: lineterminator="|" still quotes "a\nb").
        special = d.delimiter + d.lineterminator + "\r\n"
        if d.escapechar is not None:
            special += d.escapechar
        if d.quotechar is not None:
            special += d.quotechar
        parts = []
        for v in row:
            if d.quoting == QUOTE_ALL:
                quoted = True
            elif d.quoting == QUOTE_NONNUMERIC:
                quoted = not isinstance(v, _NUM)
            else:
                quoted = False
            if v is None:
                s = ""
            elif isinstance(v, str):
                s = v
            elif isinstance(v, float):
                s = repr(v)          # CPython: repr() for floats, str() else
            else:
                s = str(v)
            buf = []
            for c in s:
                if c in special:
                    esc = d.quoting == QUOTE_NONE
                    if not esc:
                        if c == d.quotechar:
                            if d.doublequote:
                                buf.append(c)   # "" — and the field is quoted
                            else:
                                esc = True
                        elif c == d.escapechar:
                            esc = True          # escaped, but not quote-worthy
                        if not esc:
                            quoted = True
                    if esc:
                        if d.escapechar is None:
                            raise Error(
                                "need to escape, but no escapechar set")
                        buf.append(d.escapechar)
                buf.append(c)
            s = "".join(buf)
            if quoted:
                s = d.quotechar + s + d.quotechar
            parts.append(s)
        rec = d.delimiter.join(parts)
        if parts and not rec:
            # A record that came out empty is a single empty field; CPython
            # writes it as "" so the line is not mistaken for a blank one.
            if d.quoting == QUOTE_NONE:
                raise Error("single empty field record must be quoted")
            rec = d.quotechar * 2
        return rec + d.lineterminator

    def writerow(self, row):
        rec = self._record(row)
        self._f.write(rec)
        return len(rec)

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)


class _Reader:
    def __init__(self, f, dialect):
        self._it = iter(f)
        self.dialect = dialect
        self.line_num = 0
        self._state = _SR
        self._field = []
        self._row = []
        self._numeric = False

    def __iter__(self):
        return self

    def __next__(self):
        self._row = []
        self._state = _SR
        while True:
            try:
                line = next(self._it)
            except StopIteration:
                if self._field or self._state == _IQ:
                    if self.dialect.strict:
                        raise Error("unexpected end of data")
                    self._save()
                    return self._row
                raise
            self.line_num += 1
            for c in line:
                self._feed(c)
            self._feed("")           # end of line — _csv.c's '\0' sentinel
            if self._state == _SR:
                return self._row

    def _save(self):
        s = "".join(self._field)
        self._field = []
        if self._numeric:
            self._numeric = False
            try:
                s = float(s)
            except ValueError:
                raise ValueError(
                    "could not convert string to float: " + repr(s))
        self._row.append(s)

    def _feed(self, c):
        d = self.dialect
        st = self._state
        if st == _SR:
            if c == "":
                return
            if c == "\n" or c == "\r":
                self._state = _CR
                return
            st = self._state = _SF          # fall through to _SF
        elif st == _AC:
            if c == "":
                return
            st = self._state = _IF          # fall through to _IF
        if st == _SF:
            if c == "" or c == "\n" or c == "\r":
                self._save()
                self._state = _SR if c == "" else _CR
            elif c == d.quotechar and d.quoting != QUOTE_NONE:
                self._state = _IQ
            elif c == d.escapechar:
                self._state = _EC
            elif c == " " and d.skipinitialspace:
                pass
            elif c == d.delimiter:
                self._save()
            else:
                if d.quoting == QUOTE_NONNUMERIC:
                    self._numeric = True
                self._field.append(c)
                self._state = _IF
        elif st == _EC:
            if c == "\n" or c == "\r":
                self._field.append(c)
                self._state = _AC
            else:
                self._field.append("\n" if c == "" else c)
                self._state = _IF
        elif st == _IF:
            if c == "" or c == "\n" or c == "\r":
                self._save()
                self._state = _SR if c == "" else _CR
            elif c == d.escapechar:
                self._state = _EC
            elif c == d.delimiter:
                self._save()
                self._state = _SF
            else:
                self._field.append(c)
        elif st == _IQ:
            if c == "":
                pass                        # a newline inside a quoted field
            elif c == d.escapechar:
                self._state = _EQ
            elif c == d.quotechar and d.quoting != QUOTE_NONE:
                self._state = _QQ if d.doublequote else _IF
            else:
                self._field.append(c)
        elif st == _EQ:
            self._field.append("\n" if c == "" else c)
            self._state = _IQ
        elif st == _QQ:
            if c == d.quotechar and d.quoting != QUOTE_NONE:
                self._field.append(c)
                self._state = _IQ
            elif c == d.delimiter:
                self._save()
                self._state = _SF
            elif c == "" or c == "\n" or c == "\r":
                self._save()
                self._state = _SR if c == "" else _CR
            elif d.strict:
                raise Error("'" + d.delimiter + "' expected after '"
                            + d.quotechar + "'")
            else:
                self._field.append(c)
                self._state = _IF
        else:                               # _CR
            if c == "":
                self._state = _SR
            elif c != "\n" and c != "\r":
                raise Error("new-line character seen in unquoted field - do "
                            "you need to open the file with newline=''?")


def reader(f, dialect="excel", **kw):
    return _Reader(f, _Dialect(dialect, **kw))


def writer(f, dialect="excel", **kw):
    return _Writer(f, _Dialect(dialect, **kw))


class DictReader:
    def __init__(self, f, fieldnames=None, restkey=None, restval=None,
                 dialect="excel", **kw):
        self._fieldnames = fieldnames
        self.restkey = restkey
        self.restval = restval
        self.reader = reader(f, dialect, **kw)
        self.line_num = 0

    def __iter__(self):
        return self

    @property
    def fieldnames(self):
        if self._fieldnames is None:
            try:
                self._fieldnames = next(self.reader)
            except StopIteration:
                pass
        self.line_num = self.reader.line_num
        return self._fieldnames

    def __next__(self):
        if self.line_num == 0:
            self.fieldnames                 # read the header, for its effect
        row = next(self.reader)
        self.line_num = self.reader.line_num
        while row == []:                    # blank lines are skipped here
            row = next(self.reader)
        names = self.fieldnames
        d = {}
        for i in range(len(names)):
            d[names[i]] = row[i] if i < len(row) else self.restval
        if len(row) > len(names):
            d[self.restkey] = row[len(names):]
        return d


class DictWriter:
    def __init__(self, f, fieldnames, restval="", extrasaction="raise",
                 dialect="excel", **kw):
        if extrasaction != "raise" and extrasaction != "ignore":
            raise ValueError("extrasaction (" + str(extrasaction)
                             + ") must be 'raise' or 'ignore'")
        self.fieldnames = fieldnames
        self.restval = restval
        self.extrasaction = extrasaction
        self.writer = writer(f, dialect, **kw)

    def writeheader(self):
        return self.writer.writerow(self.fieldnames)

    def writerow(self, d):
        if self.extrasaction == "raise":
            extra = [k for k in d if k not in self.fieldnames]
            if extra:
                raise ValueError("dict contains fields not in fieldnames: "
                                 + ", ".join([repr(k) for k in extra]))
        return self.writer.writerow(
            [d.get(k, self.restval) for k in self.fieldnames])

    def writerows(self, rows):
        for r in rows:
            self.writerow(r)
