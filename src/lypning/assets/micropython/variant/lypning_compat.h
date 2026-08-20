/*
 * lypning-mp — CPython-semantics corrections that are NOT about the unsupported
 * contract.
 *
 * lypning_unsupported.h owns the exit-90 fallback: "lypning-mp is too small, retry
 * with real python3". This header owns the other half of the same promise —
 * places where MicroPython runs a program happily and returns an answer that
 * CPython would not, at exit 0. docs/SUBSET.md calls that the one
 * outcome that makes a subset runtime worse than nothing, because the agent
 * that typed the one-liner has no way to notice.
 *
 * A correction belongs here rather than in the unsupported header when the
 * right behaviour is KNOWN and CHEAP. If it is neither, the honest move is the
 * 90, not an approximation.
 *
 * MIT, same as MicroPython.
 */

#ifndef LYPNING_COMPAT_H
#define LYPNING_COMPAT_H

#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "py/mperrno.h"
#include "py/obj.h"
#include "py/objtype.h"
#include "py/runtime.h"

#include "lypning_unsupported.h"

/*
 * Does `str(x)` equal `repr(x)` for this native type?
 *
 * CPython resolves `str(x)` to `type(x).__str__`, and `object.__str__` calls
 * `__repr__`. So a class that subclasses a native type and defines `__repr__`
 * but not `__str__` gets its own repr from `print()` and `str()` — unless the
 * native base defines a `__str__` of its own.
 *
 * MicroPython's instance_print() cannot ask that question: its native types
 * have ONE print slot serving both PRINT_STR and PRINT_REPR, and the class
 * lookup stops at that slot, so the subclass's `__repr__` was never consulted.
 * The observable damage was two SHIPPED shims — collections.defaultdict printed
 * `{}` and Counter printed `{'a': 2}`, where CPython prints
 * `defaultdict(<class 'list'>, {})` and `Counter({'a': 2})` — plus any user
 * program that subclasses list/dict/tuple to give it a repr. `repr()` was
 * right the whole time, which is what kept it hidden.
 *
 * So the question has to be answered from a table. It is short and it is not a
 * guess: measured against CPython 3.11 with `t.__str__ is not object.__str__`,
 * the ONLY built-in types that define their own `__str__` are str, bytes,
 * bytearray and the exceptions. dict, list, tuple, set, frozenset, int, float,
 * complex, bool, NoneType, range, slice and memoryview all inherit
 * object.__str__ and therefore route str() to repr().
 *
 * Getting the direction wrong is a divergence either way, which is why the
 * exclusions are explicit rather than a default:
 *   - `class S(str)` with a `__repr__`: CPython's `print(S("x"))` writes `x`,
 *     NOT the repr, because str defines `__str__`.
 *   - `class E(Exception)` with a `__repr__`: `str(e)` is the args, not the
 *     repr, for the same reason.
 */
static inline bool lypning_native_str_is_repr(mp_obj_t native) {
    if (mp_obj_is_native_exception_instance(native)) {
        return false;
    }
    const mp_obj_type_t *t = mp_obj_get_type(native);
    if (t == &mp_type_str || t == &mp_type_bytes) {
        return false;
    }
    #if MICROPY_PY_BUILTINS_BYTEARRAY
    if (t == &mp_type_bytearray) {
        return false;
    }
    #endif
    return true;
}


/*
 * ---------------------------------------------------------------------------
 * Syntax CPython accepts and MicroPython's parser does not
 * ---------------------------------------------------------------------------
 *
 * A `match` statement is a SyntaxError here. So is `{**a, "b": 1}`, `except*`,
 * a positional-only `/` parameter, a keyword in a class header, and
 * parenthesized with-items. Every one of those is a program CPython RUNS, so
 * lypning-mp exiting 1 with "SyntaxError: invalid syntax" is a MISMATCH — and the
 * worst-shaped one, because an agent reading that line concludes its own
 * correct program is broken and edits it.
 *
 * These belong to the `syntax` kind of the exit-90 contract, exactly like the
 * constructs mp_raise_NotImplementedError() already routes there. The problem
 * is that the parser cannot tell "syntax lypning-mp lacks" from "the program has a
 * typo": both arrive as the same SyntaxError.
 *
 * So the check runs ONLY AFTER a parse has already failed, and asks a
 * different question — does the source CONTAIN one of the constructs we know
 * MicroPython cannot parse? Nothing is scanned on the happy path, so a program
 * that parses pays nothing, and a valid program can never reach a false
 * positive because it never reaches the scan at all.
 *
 * KNOWN IMPRECISION, stated rather than hidden, and the same shape as the one
 * lypning_check_unsupported_attr() documents: a program that has a typo AND
 * uses `match` reports the match rather than the typo, so it exits 90 where
 * CPython exits 1. The cost is bounded and self-correcting — the caller retries
 * with real python3 and gets the accurate SyntaxError. The reverse error is
 * not: a correct program told it is malformed gets rewritten.
 *
 * The table is evidence, not a survey of the language. Each entry was measured
 * against CPython 3.11 and this binary; anything CPython 3.12+ introduced
 * (`type X = int`, `def f[T]()`) is deliberately absent, because the reference
 * interpreter rejects it too and it is therefore not a divergence.
 */

// Scanner state: which bracket we are inside, so `**` can be told apart —
// `f(**kw)` is fine and MicroPython supports it, `{**d}` is not.
#define LYPNING_SYNTAX_DEPTH 24

static inline bool lypning_word_at(const char *s, const char *p, const char *word) {
    size_t n = strlen(word);
    if (strncmp(p, word, n) != 0) {
        return false;
    }
    // must not be part of a longer identifier on either side
    if (p > s && (isalnum((unsigned char)p[-1]) || p[-1] == '_')) {
        return false;
    }
    char after = p[n];
    return !(isalnum((unsigned char)after) || after == '_');
}

// Is `p` at the start of a logical line (only whitespace before it)?
static inline bool lypning_line_start(const char *s, const char *p) {
    while (p > s) {
        char c = *--p;
        if (c == '\n') {
            return true;
        }
        if (c != ' ' && c != '\t') {
            return false;
        }
    }
    return true;
}

// Does the line containing `p` end with a `:` (ignoring a trailing comment and
// trailing whitespace)? This is what separates the `match` STATEMENT from a
// variable called `match`, which is a common name and must not be flagged.
static inline bool lypning_line_ends_colon(const char *p) {
    char last = 0;
    bool in_s = false;
    char q = 0;
    for (; *p && *p != '\n'; p++) {
        if (in_s) {
            if (*p == '\\' && p[1]) {
                p++;
            } else if (*p == q) {
                in_s = false;
            }
            continue;
        }
        if (*p == '#') {
            break;
        }
        if (*p == '\'' || *p == '"') {
            in_s = true;
            q = *p;
            last = *p;
            continue;
        }
        if (*p != ' ' && *p != '\t' && *p != '\r') {
            last = *p;
        }
    }
    return last == ':';
}

/*
 * Return a description of the first construct in `src` that CPython accepts and
 * this parser does not, or NULL if none is present.
 *
 * Comments and string literals are skipped, so a docstring mentioning `match`
 * or a regex containing `{**` cannot trigger it.
 */
static inline const char *lypning_missing_syntax(const char *src) {
    if (src == NULL) {
        return NULL;
    }
    char stack[LYPNING_SYNTAX_DEPTH];
    int depth = 0;
    const char *p = src;
    while (*p) {
        char c = *p;
        // comment to end of line
        if (c == '#') {
            while (*p && *p != '\n') {
                p++;
            }
            continue;
        }
        // string literal, including the triple-quoted forms
        if (c == '\'' || c == '"') {
            char q = c;
            bool triple = (p[1] == q && p[2] == q);
            p += triple ? 3 : 1;
            while (*p) {
                if (*p == '\\' && p[1]) {
                    p += 2;
                    continue;
                }
                if (*p == q && (!triple || (p[1] == q && p[2] == q))) {
                    p += triple ? 3 : 1;
                    break;
                }
                p++;
            }
            continue;
        }
        if (c == '(' || c == '[' || c == '{') {
            if (depth < LYPNING_SYNTAX_DEPTH) {
                stack[depth] = c;
            }
            depth++;
            p++;
            continue;
        }
        if (c == ')' || c == ']' || c == '}') {
            if (depth > 0) {
                depth--;
            }
            p++;
            continue;
        }
        if (c == '\n') {
            p++;
            continue;
        }

        // `{**d}` — dict unpacking in a display. `f(**kw)` is supported, so the
        // innermost bracket has to be a brace for this to be the missing form.
        if (c == '*' && p[1] == '*' && depth > 0 && depth <= LYPNING_SYNTAX_DEPTH
            && stack[depth - 1] == '{') {
            return "dict unpacking in a literal ({**d})";
        }

        // `def f(a, /, b)` — a positional-only marker. Only inside a paren, and
        // only when it stands alone between separators, so division is safe.
        if (c == '/' && depth > 0 && depth <= LYPNING_SYNTAX_DEPTH && stack[depth - 1] == '(') {
            const char *b = p - 1;
            while (b > src && (*b == ' ' || *b == '\t')) {
                b--;
            }
            const char *a = p + 1;
            while (*a == ' ' || *a == '\t') {
                a++;
            }
            if ((*b == ',' || *b == '(') && (*a == ',' || *a == ')')) {
                return "positional-only parameter (def f(a, /, b))";
            }
        }

        if (isalpha((unsigned char)c) || c == '_') {
            if (lypning_word_at(src, p, "match") && lypning_line_start(src, p)
                && lypning_line_ends_colon(p)) {
                return "match statement";
            }
            if (lypning_word_at(src, p, "case") && lypning_line_start(src, p)
                && lypning_line_ends_colon(p)) {
                return "match statement (case clause)";
            }
            if (lypning_word_at(src, p, "except")) {
                const char *a = p + 6;
                while (*a == ' ' || *a == '\t') {
                    a++;
                }
                if (*a == '*') {
                    return "except* (exception groups)";
                }
            }
            if (lypning_word_at(src, p, "with") && lypning_line_start(src, p)) {
                const char *a = p + 4;
                while (*a == ' ' || *a == '\t') {
                    a++;
                }
                // `with (a as f, b as g):` is the 3.10 form MicroPython lacks.
                // `with (expr) as f:` is ordinary and parses fine — so the test
                // is an `as` INSIDE the parentheses, not the parentheses alone.
                if (*a == '(') {
                    // Scan to the CLOSING paren, not to the end of the line:
                    // the parenthesized form exists precisely to break a long
                    // `with` across lines, so the `as` is usually on the next
                    // one. Bounding this at the newline missed every real use
                    // of it and only caught the single-line toy case.
                    int d = 0;
                    for (const char *q = a; *q; q++) {
                        if (*q == '(' || *q == '[' || *q == '{') {
                            d++;
                        } else if (*q == ')' || *q == ']' || *q == '}') {
                            if (--d == 0) {
                                break;
                            }
                        } else if (d > 0 && lypning_word_at(src, q, "as")) {
                            return "parenthesized with-items";
                        }
                    }
                }
            }
            // step over the identifier so a name like `matching` is not
            // re-examined character by character
            while (isalnum((unsigned char)*p) || *p == '_') {
                p++;
            }
            continue;
        }

        p++;
    }
    return NULL;
}


/*
 * ---------------------------------------------------------------------------
 * The two exception classes that are lypning-mp being too small, not the program
 * ---------------------------------------------------------------------------
 *
 * Both are reached from the SAME two uncaught-exception call sites as
 * lypning_check_unsupported_exc() — `-c` and a script file leave through
 * shared/runtime/pyexec.c, a program on stdin leaves through
 * ports/unix/main.c. docs/MICROPYTHON.md records what happens when only one is
 * wired: that path silently returns to exit 1 with a traceback.
 */

// Set by ports/unix/main.c so the SyntaxError path can see what was compiled.
// The parser consumes the lexer, so the source has to be kept aside; there is
// exactly one program per process, so one slot is enough. `-c` gives a string
// directly; a script file gives a path that is read only on the error path, so
// the zero-file-opens property of a successful run is untouched.
extern const char *lypning_src_text;
extern const char *lypning_src_file;

/*
 * The keyword name of the call in flight, for py/argcheck.c.
 *
 * mp_arg_check_num_sig() rejects a keyword handed to a C function that takes
 * none, and it is given COUNTS, not names — so the line it can produce on its
 * own is "a keyword", which §7 says is not enough: an unsupported line has to
 * name the precise thing. mp_call_function_n_kw() is the single funnel every
 * call passes through and it HAS the names, so it leaves the first one here.
 *
 * One slot is safe because a nested call completes before its caller's own
 * check runs: in `f(a=g(b=1))`, g's call sets the slot and returns, then f's
 * call overwrites it, and f is the one that raises. The count is stored beside
 * the name so argcheck can refuse to quote a stale slot if it is ever reached
 * from a call that did not come through the funnel.
 */
extern const char *lypning_call_kw;
extern size_t lypning_call_n_kw;

// A file big enough to be worth scanning but bounded, because this runs while
// the process is already failing and must not itself run the heap out.
#define LYPNING_SRC_MAX (256 * 1024)

static inline const char *lypning_source_for_scan(void) {
    if (lypning_src_text != NULL) {
        return lypning_src_text;
    }
    if (lypning_src_file == NULL) {
        return NULL;
    }
    FILE *f = fopen(lypning_src_file, "rb");
    if (f == NULL) {
        return NULL;
    }
    static char buf[8192];
    char *big = NULL;
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    if (n == sizeof(buf) - 1) {
        // longer than the static buffer — take the whole thing, bounded
        big = (char *)malloc(LYPNING_SRC_MAX);
        if (big != NULL) {
            rewind(f);
            size_t m = fread(big, 1, LYPNING_SRC_MAX - 1, f);
            big[m] = '\0';
        }
    }
    buf[n] = '\0';
    fclose(f);
    return big != NULL ? big : buf;
}

/*
 * A SyntaxError for a construct CPython accepts is lypning-mp being too small.
 *
 * Runs only when a parse has ALREADY failed, so a program that compiles pays
 * nothing and can never be misdiagnosed. See lypning_missing_syntax() above for
 * the table and for the one imprecision this accepts.
 */
static inline void lypning_check_missing_syntax(mp_obj_t exc) {
    if (!mp_obj_is_exception_instance(exc)) {
        return;
    }
    if (!mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(mp_obj_get_type(exc)),
                                 MP_OBJ_FROM_PTR(&mp_type_SyntaxError))) {
        return;
    }
    const char *desc = lypning_missing_syntax(lypning_source_for_scan());
    if (desc != NULL) {
        lypning_exit_unsupported("syntax", desc, NULL);
    }
}

/*
 * An uncaught MemoryError is the GC heap running out, and that is never the
 * program's answer.
 *
 * This was found as the single MISMATCH in a 472-entry conformance run:
 *
 *     import json
 *     d = json.load(open('public/introspect/docs-corpus.json'))   # ~2.4 MB
 *
 * CPython prints a result; lypning-mp printed a traceback and exited 1, so the
 * caller could not tell "your program is wrong" from "this input is bigger
 * than lypning-mp" and had no reason to retry with real python3. That is the exact
 * decision the 90 exists to make possible.
 *
 * Unlike NotImplementedError — where hijacking a program's OWN exception would
 * be worse than the original bug, because a program raising it means something
 * specific — a hand-written `raise MemoryError` in a one-liner is vanishingly
 * rare, and an interpreter that ran out of heap has produced no answer either
 * way. So this does not try to distinguish them.
 *
 * The detail is fixed text rather than the runtime's message, which carries the
 * failed allocation size and would make the same program report differently on
 * different inputs.
 */
static inline void lypning_check_memory_exc(mp_obj_t exc) {
    if (!mp_obj_is_exception_instance(exc)) {
        return;
    }
    if (mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(mp_obj_get_type(exc)),
                                MP_OBJ_FROM_PTR(&mp_type_MemoryError))) {
        lypning_exit_unsupported("memory", "heap exhausted (this input is larger than lypning-mp's heap)", NULL);
    }
}

// One call for both, so a call site cannot pick up one and miss the other.
static inline void lypning_check_compat_exc(mp_obj_t exc) {
    lypning_check_missing_syntax(exc);
    lypning_check_memory_exc(exc);
}

/*
 * ---------------------------------------------------------------------------
 * What CPython puts in an OSError, and what a failing open() looks like
 * ---------------------------------------------------------------------------
 *
 * CPython raises OSError(errno, strerror, filename) for a real OS failure, and
 * then hides the third argument: `.args` is the first TWO, and the filename
 * comes back through `.filename` and through `str(e)`:
 *
 *     >>> open("nope.txt")
 *     FileNotFoundError: [Errno 2] No such file or directory: 'nope.txt'
 *     >>> e.args
 *     (2, 'No such file or directory')
 *
 * MicroPython raises OSError(errno) with one argument and no message at all, so
 * `str(e)` was the bare number `2` — which is the least useful form of the most
 * common error a sandbox program hits, and a corpus entry printing it diverged
 * on every line.
 *
 * The table is the ~20 errnos a file-touching one-liner can actually produce,
 * with CPython's exact strerror text. It is NOT the full errno space: an errno
 * outside it falls back to a one-argument OSError, which is what MicroPython
 * did for everything before. Roughly 500 B of .rodata for the most-read error
 * message in the runtime.
 */
typedef struct {
    int err;
    const char *msg;
} lypning_strerror_t;

static const lypning_strerror_t lypning_strerror_table[] = {
    { MP_EPERM, "Operation not permitted" },
    { MP_ENOENT, "No such file or directory" },
    { MP_EIO, "Input/output error" },
    { MP_EBADF, "Bad file descriptor" },
    { MP_EAGAIN, "Resource temporarily unavailable" },
    { MP_EACCES, "Permission denied" },
    { MP_EBUSY, "Device or resource busy" },
    { MP_EEXIST, "File exists" },
    { MP_EXDEV, "Invalid cross-device link" },
    { MP_ENODEV, "No such device" },
    { MP_ENOTDIR, "Not a directory" },
    { MP_EISDIR, "Is a directory" },
    { MP_EINVAL, "Invalid argument" },
    { MP_EMFILE, "Too many open files" },
    { MP_EFBIG, "File too large" },
    { MP_ENOSPC, "No space left on device" },
    { MP_EROFS, "Read-only file system" },
    { MP_EPIPE, "Broken pipe" },
    { MP_ERANGE, "Numerical result out of range" },
    // Not in MicroPython's mperrno.h; the numbers are the Linux ones the
    // guest's syscalls actually return.
    { 36, "File name too long" },
    { 39, "Directory not empty" },
};

static inline const char *lypning_strerror(int err) {
    for (size_t i = 0; i < MP_ARRAY_SIZE(lypning_strerror_table); i++) {
        if (lypning_strerror_table[i].err == err) {
            return lypning_strerror_table[i].msg;
        }
    }
    return NULL;
}

#endif // LYPNING_COMPAT_H
