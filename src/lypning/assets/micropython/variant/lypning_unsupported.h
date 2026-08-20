/*
 * lypning-mp — the unsupported-module half of the fallback contract.
 *
 * docs/SUBSET.md §7 rule 4 draws a line that matters more than it looks:
 *
 *   - A module CPython itself does not have in this image (PIL, numpy) must
 *     raise ModuleNotFoundError and exit 1, exactly as CPython does. That is a
 *     program that RAN CORRECTLY and found its dependency missing.
 *
 *   - A module that exists in CPython but not in lypning-mp (subprocess, argparse,
 *     zlib, csv) must exit 90 with one greppable stderr line. That is lypning-mp
 *     being too small, and it is what lets an agent loop — or a shell wrapper —
 *     retry the same line with real python3 instead of rewriting the program.
 *
 * Collapsing those two into one exit code would make the retry undecidable, so
 * this header carries the only thing that can tell them apart: the list of
 * top-level module names CPython ships.
 *
 * The list is CPython 3.11's `sys.stdlib_module_names` in full — all 305 names,
 * packed as one \0-separated blob (~2.5 KB of .rodata) rather than an array of
 * pointers, because on i386 the pointer array alone would cost half as much
 * again.
 *
 * IN FULL is load-bearing, and it used to be 217. The blob previously dropped
 * the 88 leading-underscore internals (_abc, _ast, _json, __future__ ...) on the
 * grounds that nobody imports them by name. Two things made that wrong. First,
 * §7 rule 4 is about what CPython HAS, and CPython has `_json`: `import _json`
 * was exiting 1 with ModuleNotFoundError when it should have been exiting 90.
 * Second, this blob is now also the VALUE of sys.stdlib_module_names, and a
 * frozenset of 217 names would report len() 217 against CPython's 305 and answer
 * `'_ast' in sys.stdlib_module_names` with False — a constant that does not
 * honour its own name, which is the one bug class this contract exists to stop.
 * Restoring the 88 costs 859 B of .rodata and closes both at once.
 *
 * Nothing lypning-mp provides has a leading-underscore module __name__ (the shims
 * alias as `import uhashlib as _hashlib`, whose __name__ is still "hashlib"), so
 * widening the table cannot make a working import start refusing.
 *
 * Modules lypning-mp DOES provide never reach this code: the import resolves and
 * nothing is raised. So the table needs no exclusions and no maintenance when
 * a frozen shim lands in micropython/lib.
 *
 * MIT, same as MicroPython.
 */

#ifndef LYPNING_UNSUPPORTED_H
#define LYPNING_UNSUPPORTED_H

#include <stdlib.h>
#include <string.h>
#include <unistd.h>

// mp_obj_is_instance_type() lives here and py/builtinimport.c does not pull it
// in on its own; including it from this header rather than from the port patch
// keeps the patch to the two call sites.
#include "py/obj.h"
#include "py/objtype.h"
#include "py/runtime.h"

// CPython 3.11 sys.stdlib_module_names, complete: all 305 names, 2,515 B.
static const char lypning_cpython_stdlib[] =
    "__future__\0_abc\0_aix_support\0_ast\0_asyncio\0_bisect\0_blake2\0"
    "_bootsubprocess\0_bz2\0_codecs\0_codecs_cn\0_codecs_hk\0"
    "_codecs_iso2022\0_codecs_jp\0_codecs_kr\0_codecs_tw\0_collections\0"
    "_collections_abc\0_compat_pickle\0_compression\0_contextvars\0_crypt\0"
    "_csv\0_ctypes\0_curses\0_curses_panel\0_datetime\0_dbm\0_decimal\0"
    "_elementtree\0_frozen_importlib\0_frozen_importlib_external\0"
    "_functools\0_gdbm\0_hashlib\0_heapq\0_imp\0_io\0_json\0_locale\0"
    "_lsprof\0_lzma\0_markupbase\0_md5\0_msi\0_multibytecodec\0"
    "_multiprocessing\0_opcode\0_operator\0_osx_support\0_overlapped\0"
    "_pickle\0_posixshmem\0_posixsubprocess\0_py_abc\0_pydecimal\0_pyio\0"
    "_queue\0_random\0_scproxy\0_sha1\0_sha256\0_sha3\0_sha512\0_signal\0"
    "_sitebuiltins\0_socket\0_sqlite3\0_sre\0_ssl\0_stat\0_statistics\0"
    "_string\0_strptime\0_struct\0_symtable\0_thread\0_threading_local\0"
    "_tkinter\0_tokenize\0_tracemalloc\0_typing\0_uuid\0_warnings\0"
    "_weakref\0_weakrefset\0_winapi\0_zoneinfo\0abc\0aifc\0antigravity\0"
    "argparse\0array\0ast\0asynchat\0asyncio\0asyncore\0atexit\0audioop\0"
    "base64\0bdb\0binascii\0bisect\0builtins\0bz2\0cProfile\0calendar\0"
    "cgi\0cgitb\0chunk\0cmath\0cmd\0code\0codecs\0codeop\0collections\0"
    "colorsys\0compileall\0concurrent\0configparser\0contextlib\0"
    "contextvars\0copy\0copyreg\0crypt\0csv\0ctypes\0curses\0dataclasses\0"
    "datetime\0dbm\0decimal\0difflib\0dis\0distutils\0doctest\0email\0"
    "encodings\0ensurepip\0enum\0errno\0faulthandler\0fcntl\0filecmp\0"
    "fileinput\0fnmatch\0fractions\0ftplib\0functools\0gc\0genericpath\0"
    "getopt\0getpass\0gettext\0glob\0graphlib\0grp\0gzip\0hashlib\0heapq\0"
    "hmac\0html\0http\0idlelib\0imaplib\0imghdr\0imp\0importlib\0inspect\0"
    "io\0ipaddress\0itertools\0json\0keyword\0lib2to3\0linecache\0locale\0"
    "logging\0lzma\0mailbox\0mailcap\0marshal\0math\0mimetypes\0mmap\0"
    "modulefinder\0msilib\0msvcrt\0multiprocessing\0netrc\0nis\0nntplib\0"
    "nt\0ntpath\0nturl2path\0numbers\0opcode\0operator\0optparse\0os\0"
    "ossaudiodev\0pathlib\0pdb\0pickle\0pickletools\0pipes\0pkgutil\0"
    "platform\0plistlib\0poplib\0posix\0posixpath\0pprint\0profile\0"
    "pstats\0pty\0pwd\0py_compile\0pyclbr\0pydoc\0pydoc_data\0pyexpat\0"
    "queue\0quopri\0random\0re\0readline\0reprlib\0resource\0rlcompleter\0"
    "runpy\0sched\0secrets\0select\0selectors\0shelve\0shlex\0shutil\0"
    "signal\0site\0smtpd\0smtplib\0sndhdr\0socket\0socketserver\0spwd\0"
    "sqlite3\0sre_compile\0sre_constants\0sre_parse\0ssl\0stat\0"
    "statistics\0string\0stringprep\0struct\0subprocess\0sunau\0symtable\0"
    "sys\0sysconfig\0syslog\0tabnanny\0tarfile\0telnetlib\0tempfile\0"
    "termios\0textwrap\0this\0threading\0time\0timeit\0tkinter\0token\0"
    "tokenize\0tomllib\0trace\0traceback\0tracemalloc\0tty\0turtle\0"
    "turtledemo\0types\0typing\0unicodedata\0unittest\0urllib\0uu\0uuid\0"
    "venv\0warnings\0wave\0weakref\0webbrowser\0winreg\0winsound\0wsgiref\0"
    "xdrlib\0xml\0xmlrpc\0zipapp\0zipfile\0zipimport\0zlib\0zoneinfo\0";

// Is `name` (a top-level module name, no dots) one CPython ships?
static inline bool lypning_is_cpython_stdlib(const char *name, size_t len) {
    for (const char *p = lypning_cpython_stdlib; *p; p += strlen(p) + 1) {
        if (strlen(p) == len && memcmp(p, name, len) == 0) {
            return true;
        }
    }
    return false;
}

#if MICROPY_PY_BUILTINS_FROZENSET
#include "py/objlist.h"

/*
 * sys.stdlib_module_names, built on demand from the blob above.
 *
 * The value costs no new .rodata: the module-refusal check already carries the
 * exact list, and this walks it. What it costs is the walk itself, once, on the
 * first read of the attribute.
 *
 * A FROZENSET AND NOT A TUPLE, deliberately. CPython 3.11 returns a frozenset
 * (measured, not remembered), this variant has one, and a tuple would change
 * both repr() and == on a value whose whole purpose is to be compared against.
 * Returning a tuple to save the type would be the silent divergence §6 exists
 * to prevent, and there would be no way for a caller to notice.
 *
 * What still differs is ITERATION ORDER, so `print(sys.stdlib_module_names)`
 * does not match CPython. That is not introduced here — it is true of every set
 * in this runtime (`print({1, 2})` gives `{2, 1}`) — and it is the reason the
 * corpus reads this value through sorted(). len(), `in` and sorted() are exact.
 *
 * No caching and no root pointer: a one-liner reads this at most once, and a
 * cached mp_obj_t would need a GC root registered in mpstate for no measured
 * gain.
 */
static inline mp_obj_t lypning_sys_stdlib_module_names(void) {
    mp_obj_t names = mp_obj_new_list(0, NULL);
    for (const char *p = lypning_cpython_stdlib; *p; p += strlen(p) + 1) {
        mp_obj_list_append(names, mp_obj_new_str(p, strlen(p)));
    }
    return mp_call_function_1(MP_OBJ_FROM_PTR(&mp_type_frozenset), names);
}
#endif // MICROPY_PY_BUILTINS_FROZENSET

// CPython 3.11's public builtins (dir(builtins)), 149 names, ~1563 bytes. Same
// packing, same purpose: NameError on one of these is lypning-mp missing a builtin
// (FileNotFoundError, frozenset, complex), while NameError on anything else is
// the program's own undefined name and keeps CPython's exit 1.
static const char lypning_cpython_builtins[] =
    "ArithmeticError\0AssertionError\0AttributeError\0BaseException\0"
    "BaseExceptionGroup\0BlockingIOError\0BrokenPipeError\0BufferError\0"
    "BytesWarning\0ChildProcessError\0ConnectionAbortedError\0"
    "ConnectionError\0ConnectionRefusedError\0ConnectionResetError\0"
    "DeprecationWarning\0EOFError\0Ellipsis\0EncodingWarning\0"
    "EnvironmentError\0Exception\0ExceptionGroup\0False\0FileExistsError\0"
    "FileNotFoundError\0FloatingPointError\0FutureWarning\0GeneratorExit\0"
    "IOError\0ImportError\0ImportWarning\0IndentationError\0IndexError\0"
    "InterruptedError\0IsADirectoryError\0KeyError\0KeyboardInterrupt\0"
    "LookupError\0MemoryError\0ModuleNotFoundError\0NameError\0None\0"
    "NotADirectoryError\0NotImplemented\0NotImplementedError\0OSError\0"
    "OverflowError\0PendingDeprecationWarning\0PermissionError\0"
    "ProcessLookupError\0RecursionError\0ReferenceError\0ResourceWarning\0"
    "RuntimeError\0RuntimeWarning\0StopAsyncIteration\0StopIteration\0"
    "SyntaxError\0SyntaxWarning\0SystemError\0SystemExit\0TabError\0"
    "TimeoutError\0True\0TypeError\0UnboundLocalError\0UnicodeDecodeError\0"
    "UnicodeEncodeError\0UnicodeError\0UnicodeTranslateError\0"
    "UnicodeWarning\0UserWarning\0ValueError\0Warning\0ZeroDivisionError\0"
    "abs\0aiter\0all\0anext\0any\0ascii\0bin\0bool\0breakpoint\0bytearray\0"
    "bytes\0callable\0chr\0classmethod\0compile\0complex\0copyright\0"
    "credits\0delattr\0dict\0dir\0divmod\0enumerate\0eval\0exec\0exit\0"
    "filter\0float\0format\0frozenset\0getattr\0globals\0hasattr\0hash\0"
    "help\0hex\0id\0input\0int\0isinstance\0issubclass\0iter\0len\0"
    "license\0list\0locals\0map\0max\0memoryview\0min\0next\0object\0oct\0"
    "open\0ord\0pow\0print\0property\0quit\0range\0repr\0reversed\0round\0"
    "set\0setattr\0slice\0sorted\0staticmethod\0str\0sum\0super\0tuple\0"
    "type\0vars\0zip\0";

static inline bool lypning_is_cpython_builtin(const char *name) {
    size_t len = strlen(name);
    for (const char *p = lypning_cpython_builtins; *p; p += strlen(p) + 1) {
        if (strlen(p) == len && memcmp(p, name, len) == 0) {
            return true;
        }
    }
    return false;
}

/*
 * Write the one contract line and exit 90.
 *
 * Exiting from inside the VM rather than raising a catchable exception is
 * deliberate. The whole point of the 90 is that the CALLER — the shell, the
 * agent loop — sees it; a program that wrapped `import csv` in try/except
 * ImportError would not have taken that branch under CPython either, because
 * under CPython the import succeeds. Suppressing the signal to emulate a
 * branch CPython never takes would be the silent divergence §6 exists to
 * prevent.
 *
 * write(2) rather than mp_printf keeps this callable from anywhere in the VM.
 * stdout in the unix port is unbuffered (write(2) per chunk, see
 * ports/unix/unix_mphal.c), so exiting cannot lose output the program already
 * produced — which §7 rule 3 allows a runtime 90 to leave behind.
 */
static inline NORETURN void lypning_exit_unsupported(const char *kind, const char *a, const char *b) {
    char line[160];
    static const char prefix[] = "lypning: unsupported: ";
    size_t n = 0;
    const char *piece[5] = { prefix, kind, ": ", a, b };
    for (int i = 0; i < 5; i++) {
        if (piece[i] == NULL) {
            continue;
        }
        size_t l = strlen(piece[i]);
        if (l > sizeof(line) - n - 2) {
            l = sizeof(line) - n - 2;
        }
        memcpy(line + n, piece[i], l);
        n += l;
    }
    line[n++] = '\n';
    // One line, nothing else (§7 rule 5). A short write here would only ever
    // happen on a closed stderr, where there is nothing useful left to do.
    ssize_t ignored = write(STDERR_FILENO, line, n);
    (void)ignored;
    exit(LYPNING_UNSUPPORTED_EXIT);
}

/*
 * Called from py/builtinimport.c at the two points where a failed import is
 * about to become an ImportError. If the module is one CPython has, this exits
 * 90; otherwise it returns and the normal ImportError is raised.
 *
 * The name reported is the full dotted path that failed to resolve
 * ("http.server"), while the table lookup uses its top-level package — so
 * `import os.path` is recognised as unsupported even though the table holds
 * only "os".
 */
static inline void lypning_check_unsupported_module(const char *name) {
    const char *dot = strchr(name, '.');
    size_t top = dot ? (size_t)(dot - name) : strlen(name);
    if (lypning_is_cpython_stdlib(name, top)) {
        lypning_exit_unsupported("module", name, NULL);
    }
}

/*
 * Called from py/runtime.c at the point where a failed attribute lookup is
 * about to become an AttributeError.
 *
 * Two of the three cases are lypning-mp being too small rather than the program
 * being wrong, and §7 says so with `attribute: str.casefold` as its example:
 *
 *   - a missing attribute on a STDLIB MODULE (`re.findall`), and
 *   - a missing method on a BUILT-IN TYPE (`str.casefold`).
 *
 * The third — a missing attribute on a user-defined class — is an ordinary
 * program error and keeps CPython's exit 1. MP_TYPE_FLAG_INSTANCE_TYPE is what
 * separates them: it marks a type created by a `class` statement.
 *
 * KNOWN IMPRECISION, stated rather than hidden: lypning-mp cannot tell a method
 * CPython has from a typo. `"x".casefold()` and `"x".casefld()` both report
 * unsupported, where CPython gives AttributeError and exit 1 for the second.
 * Closing that would mean carrying CPython's full attribute table for every
 * stdlib module and built-in type — tens of KB of .rodata against a 700 KB
 * budget — to improve the diagnosis of a typo. The cost of being wrong here is
 * bounded: the caller retries with real python3 and gets the accurate error.
 */
static inline void lypning_check_unsupported_attr(mp_obj_t base, qstr attr) {
    const char *attr_str = qstr_str(attr);
    // Dunders are protocol probes, not library surface: reporting
    // `attribute: str.__aiter__` would be noise, and several of them are
    // looked up speculatively by the runtime itself.
    if (attr_str[0] == '_') {
        return;
    }
    if (mp_obj_is_type(base, &mp_type_module)) {
        mp_obj_t dest[2];
        mp_load_method_maybe(base, MP_QSTR___name__, dest);
        if (dest[0] == MP_OBJ_NULL || !mp_obj_is_qstr(dest[0])) {
            return;
        }
        const char *mod = qstr_str(mp_obj_str_get_qstr(dest[0]));
        const char *dot = strchr(mod, '.');
        size_t top = dot ? (size_t)(dot - mod) : strlen(mod);
        if (!lypning_is_cpython_stdlib(mod, top)) {
            return;
        }
        char qualified[96];
        size_t l = strlen(mod);
        if (l > sizeof(qualified) - 2) {
            l = sizeof(qualified) - 2;
        }
        memcpy(qualified, mod, l);
        qualified[l] = '.';
        qualified[l + 1] = '\0';
        lypning_exit_unsupported("attribute", qualified, attr_str);
    }
    const mp_obj_type_t *type = mp_obj_get_type(base);
    if (type == NULL || mp_obj_is_instance_type(type) || mp_obj_is_type(base, &mp_type_type)) {
        return;
    }
    char qualified[96];
    const char *tname = qstr_str(type->name);
    size_t l = strlen(tname);
    if (l > sizeof(qualified) - 2) {
        l = sizeof(qualified) - 2;
    }
    memcpy(qualified, tname, l);
    qualified[l] = '.';
    qualified[l + 1] = '\0';
    lypning_exit_unsupported("attribute", qualified, attr_str);
}

/*
 * Called from py/runtime.c where an undefined global becomes a NameError.
 * A name CPython HAS in builtins is lypning-mp being too small; anything else is
 * the program's own bug and keeps exit 1.
 */
static inline void lypning_check_unsupported_name(const char *name) {
    if (lypning_is_cpython_builtin(name)) {
        lypning_exit_unsupported("builtin", name, NULL);
    }
}

/*
 * Called from py/runtime.c's mp_raise_NotImplementedError().
 *
 * Inside MicroPython that exception means exactly one thing — this VM does not
 * implement the construct — which is the definition of the 90. The message is
 * plain text rather than a compressed ROM string because mpconfigvariant.mk
 * turns MICROPY_ROM_TEXT_COMPRESSION off, and this is one of the two reasons it
 * does: an unsupported line has to name the precise thing (§7), and a
 * compressed message cannot be printed from here.
 */
static inline NORETURN void lypning_exit_not_implemented(const char *msg) {
    lypning_exit_unsupported("syntax", msg, NULL);
}

/*
 * Called from py/argcheck.c when a C-level function is handed a keyword it does
 * not accept, and from extmod/modre.c when the regex engine cannot compile a
 * pattern. Both are the `argument` kind from §7: the program is fine, the
 * argument is outside what lypning-mp implements (json.dumps(indent=),
 * print(flush=), a named group `(?P<k>...)`).
 */
static inline NORETURN void lypning_exit_unsupported_kwarg(const char *name) {
    lypning_exit_unsupported("argument", "keyword ", name);
}

/*
 * Called from ports/unix/main.c's handle_uncaught_exception(), just before it
 * prints a traceback.
 *
 * THE HALF OF THE CONTRACT THAT WAS MISSING. lypning_exit_not_implemented()
 * above is reached from py/runtime.c's mp_raise_NotImplementedError(), which is
 * a C function — so only the C layer could ever produce the 90. The FROZEN
 * SHIMS raise NotImplementedError from Python (`re._unsupported()`,
 * `csv._check_kw()`), and a Python-level raise never passes through that C
 * helper. The message was right and everything around it was wrong: exit 1
 * instead of 90, and a multi-line traceback instead of the single greppable
 * line, so a caller branching on 90 to retry with real python3 never fired for
 * ANY shim-level gap — regex backreferences, named-group references,
 * re.VERBOSE, csv quoting. Discovered 2026-08-14 by adding corpus entries for
 * two silent divergences and finding they landed on exit 1.
 *
 * The shim already writes the complete line ("lypning: unsupported: argument:
 * re(VERBOSE)"), so this prints it verbatim rather than rebuilding it. Anything
 * that is not a NotImplementedError carrying the marker is left completely
 * alone and still gets its ordinary traceback and exit 1 — a program's own
 * NotImplementedError must stay the program's own error.
 */
static inline void lypning_check_unsupported_exc(mp_obj_t exc) {
    static const char marker[] = "lypning: unsupported: ";
    const size_t mlen = sizeof(marker) - 1;

    if (!mp_obj_is_exception_instance(exc)) {
        return;
    }
    if (!mp_obj_is_subclass_fast(MP_OBJ_FROM_PTR(mp_obj_get_type(exc)),
        MP_OBJ_FROM_PTR(&mp_type_NotImplementedError))) {
        return;
    }
    mp_obj_t val = mp_obj_exception_get_value(exc);
    if (!mp_obj_is_str(val)) {
        return;
    }
    size_t len;
    const char *s = mp_obj_str_get_data(val, &len);
    if (len < mlen || memcmp(s, marker, mlen) != 0) {
        return;
    }

    // One line, nothing else, stdout untouched (§7 rule 5).
    ssize_t ignored = write(STDERR_FILENO, s, len);
    (void)ignored;
    ignored = write(STDERR_FILENO, "\n", 1);
    (void)ignored;
    exit(LYPNING_UNSUPPORTED_EXIT);
}

#endif // LYPNING_UNSUPPORTED_H
