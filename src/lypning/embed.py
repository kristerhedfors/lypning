"""The library tier, from Python: ``liblypning`` loaded in this process.

Everything else in this package reaches an engine by spawning it. This module
is the exception, and it exists for three reasons that are worth keeping
straight:

  * It is **one of the hosts**. Every host in the table in ``docs/EMBEDDING.md``
    section 4 calls the same symbols; a Python one written against the same
    header is what lets the test suite exercise the ABI without a compiler in
    the loop.
  * It is what **asserts the library's refusal contract**. A shared object has
    no exit code, so the pinned check that :mod:`lypning.build` runs on the
    binary has no meaning here until something calls into the ABI and looks at
    the status, the two buffers and the one line. That something is
    :func:`check_refusal_contract`, below, and a build is not allowed to report
    ``ok`` until it passes.
  * It is the **honest measurement** of what embedding buys. ``lypning bench``
    times process spawns because that is what the CLI costs; the library's cost
    is a function call, and only a caller inside the process can time it.

Stdlib only, like everything else here: :mod:`ctypes` ships with CPython, and a
binding that needed a wheel to be compiled would defeat the purpose of shipping
a C ABI in the first place.

Nothing here prints, and nothing here raises for a *refusal* — a refusal is an
outcome, not an error. :class:`LibraryError` is for the library being absent or
unusable, which is the caller's problem to solve and never the program's.
"""

from __future__ import annotations

import ctypes
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

from . import paths

#: Mirrors the enum in ``assets/include/lypning.h``. Asserted against
#: ``lypning_abi_version()`` on load, so a header and a library that have
#: drifted apart are caught at the door.
ABI_VERSION = 1

OK = 0
ERROR = 1
UNSUPPORTED = 2
BUSY = 3
PANIC = 4

STATUS_NAMES = {OK: "ok", ERROR: "error", UNSUPPORTED: "unsupported",
                BUSY: "busy", PANIC: "panic"}

#: The same pinned refusal the binary's contract check uses. One program, two
#: shapes, one expected answer — that is the point of pinning it.
REFUSAL_PROGRAM = "import subprocess"
REFUSAL_LINE = b"lypning: unsupported: module: import subprocess\n"


def shared_library_name(platform: str = sys.platform) -> str:
    """``liblypning.dylib`` on macOS, ``liblypning.so`` everywhere else.

    The ONE place the file name is decided. Every other spelling in the package
    — the build's output, the installer's destination, discovery, the help text
    — reads :data:`LIB_NAME`, because two spellings is a build that succeeds on
    one platform and installs a file nothing will ever find on the other.
    """
    return "liblypning.dylib" if platform == "darwin" else "liblypning.so"


#: The shared library's file name on THIS platform.
LIB_NAME = shared_library_name()


class LibraryError(Exception):
    """The shared library is missing or is not the one this module speaks to."""


# --- discovery ---------------------------------------------------------------


def find_library() -> Optional[Path]:
    """``$LYPNING_LIB``, the state lib dir, then a cargo ``release-lib`` target.

    The same order and the same reasoning as :func:`lypning.engines.find_lypning`:
    an explicit override wins, an installed artefact beats a build tree, and a
    build tree is still better than nothing in a checkout that has not run
    ``lypning build --lib`` yet.
    """
    env = os.environ.get("LYPNING_LIB", "").strip()
    if env:
        p = Path(env).expanduser()
        if not p.is_file():
            # The same rule :func:`lypning.engines._override` holds for the
            # binaries: an override that silently falls back to discovery — or
            # here, to "not built" — sends the caller to build something that
            # already exists, forever, because the answer never changes.
            raise LibraryError(
                "$LYPNING_LIB points at %s, which does not exist — unset it or "
                "point it at a `lypning build --lib` library" % p)
        return p
    candidates = [
        lib_dir() / LIB_NAME,
        paths.build_dir() / "rust" / "target" / "release-lib" / LIB_NAME,
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def lib_dir() -> Path:
    """Where ``lypning build --lib`` installs the shared and static libraries."""
    return paths.state_dir() / "lib"


def include_dir() -> Path:
    """Where the same command installs ``lypning.h`` and ``lypning.hpp``."""
    return paths.state_dir() / "include"


_ELF_MAGIC = b"\x7fELF"
#: MH_MAGIC_64 as it sits on disk (little-endian), and its byte-swapped twin.
_MACHO_64_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf")
#: A universal ("fat") image: several Mach-O slices behind one big-endian table.
_FAT_MAGIC = b"\xca\xfe\xba\xbe"
_LC_SEGMENT_64 = 0x19
_MACHO_64_HEADER = 32


def _looks_like_a_library(path: Path) -> str:
    """``""`` if the file can be handed to ``dlopen``, else the reason it cannot.

    Deliberately shallow: the ELF or Mach-O magic and the size the file's own
    headers imply. It cannot prove a library is loadable — only the loader can
    — but it catches the case that would otherwise be fatal rather than
    reportable, a file truncated mid-write.

    Each format says where its last byte is in its own way. ELF puts the
    section table last, so its end is the file's end. A Mach-O image ends with
    its ``__LINKEDIT`` segment (symbol and relocation tables), so the check
    walks the load commands to that segment and asks whether it fits. A fat
    image is a table of slices, and every slice has to fit.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            head = fh.read(64)
            if head[:4] in _MACHO_64_MAGICS or head[:4] == _FAT_MAGIC:
                # The load commands sit right after the 32-byte header, and
                # `sizeofcmds` (offset 20) says how far they run.
                order = "little" if head[:4] == _MACHO_64_MAGICS[0] else "big"
                want = 8 + 20 * 64 if head[:4] == _FAT_MAGIC else \
                    _MACHO_64_HEADER + int.from_bytes(head[20:24], order)
                head += fh.read(max(0, want - len(head)))
    except OSError as e:
        return str(e)
    if head[:4] == _ELF_MAGIC:
        return _elf_truncation(head, size)
    if head[:4] in _MACHO_64_MAGICS:
        return _macho_truncation(head, size)
    if head[:4] == _FAT_MAGIC:
        return _fat_truncation(head, size)
    return "not an ELF or Mach-O file"


def _elf_truncation(head: bytes, size: int) -> str:
    if len(head) < 64:
        return "the ELF header is truncated (%d bytes)" % len(head)
    if head[4] != 2:
        return "not a 64-bit ELF file"
    little = head[5] == 1
    order = "little" if little else "big"
    # e_shoff (8 bytes at 0x28) plus e_shentsize * e_shnum: the end of the
    # section table, which is the last thing a complete object file contains.
    shoff = int.from_bytes(head[0x28:0x30], order)
    shentsize = int.from_bytes(head[0x3A:0x3C], order)
    shnum = int.from_bytes(head[0x3C:0x3E], order)
    end = shoff + shentsize * shnum
    if end > size:
        return "truncated — its headers describe %d bytes and the file is %d" % (end, size)
    return ""


def _macho_truncation(head: bytes, size: int) -> str:
    order = "little" if head[:4] == _MACHO_64_MAGICS[0] else "big"
    if len(head) < _MACHO_64_HEADER:
        return "the Mach-O header is truncated (%d bytes)" % len(head)
    ncmds = int.from_bytes(head[16:20], order)
    sizeofcmds = int.from_bytes(head[20:24], order)
    if _MACHO_64_HEADER + sizeofcmds > size:
        return "truncated — its load commands run to byte %d and the file is %d" % (
            _MACHO_64_HEADER + sizeofcmds, size)
    off = _MACHO_64_HEADER
    for _ in range(ncmds):
        if off + 8 > len(head):
            return "truncated — the load commands end before %d were read" % ncmds
        cmd = int.from_bytes(head[off:off + 4], order)
        cmdsize = int.from_bytes(head[off + 4:off + 8], order)
        if cmdsize < 8 or off + cmdsize > len(head):
            return "a load command at byte %d claims %d bytes the file lacks" % (off, cmdsize)
        # segment_command_64: cmd, cmdsize, segname[16], vmaddr, vmsize,
        # fileoff, filesize, …  — the last segment in the file is __LINKEDIT.
        if cmd == _LC_SEGMENT_64 and head[off + 8:off + 24].rstrip(b"\0") == b"__LINKEDIT":
            fileoff = int.from_bytes(head[off + 40:off + 48], order)
            filesize = int.from_bytes(head[off + 48:off + 56], order)
            end = fileoff + filesize
            if end > size:
                return "truncated — its headers describe %d bytes and the file is %d" % (end, size)
            return ""
        off += cmdsize
    return "no __LINKEDIT segment — not a linked Mach-O image"


def _fat_truncation(head: bytes, size: int) -> str:
    nfat = int.from_bytes(head[4:8], "big")
    if nfat == 0 or nfat > 64:
        return "a fat header naming %d slices is not a library" % nfat
    for i in range(nfat):
        # fat_arch: cputype, cpusubtype, offset, size, align — five big-endian u32.
        entry = head[8 + 20 * i:8 + 20 * (i + 1)]
        if len(entry) < 20:
            return "the fat header is truncated (%d bytes)" % len(head)
        offset = int.from_bytes(entry[8:12], "big")
        length = int.from_bytes(entry[12:16], "big")
        if offset + length > size:
            return "truncated — slice %d ends at byte %d and the file is %d" % (
                i, offset + length, size)
    return ""


# --- results -----------------------------------------------------------------


@dataclass
class Route:
    """Which tier should run a program, and the construct that decided it."""

    engine: str
    kind: str = ""
    detail: str = ""
    imports: List[str] = field(default_factory=list)


@dataclass
class Outcome:
    """One in-process run. ``stdout``/``stderr`` are bytes, as the program wrote them."""

    status: int
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""
    kind: str = ""
    detail: str = ""
    committed: bool = False
    fall_onward: bool = False

    @property
    def refused(self) -> bool:
        """Was this run a refusal? Informational — branch on :attr:`fall_onward`.

        A refusal is not a failure: it means run it on CPython. But it is not
        the only outcome that means that. A :data:`BUSY` that executed nothing
        and a :data:`PANIC` that reached no commit want the same treatment, and
        :attr:`fall_onward` is the one predicate that folds all three in, so it
        is the one a host branches on. This property answers the narrower
        question, for a host that wants to log why.
        """
        return self.status == UNSUPPORTED

    @property
    def status_name(self) -> str:
        return STATUS_NAMES.get(self.status, "status-%d" % self.status)


# --- the binding -------------------------------------------------------------


def _c(fn, restype, argtypes):
    fn.restype = restype
    fn.argtypes = argtypes
    return fn


class Library:
    """A loaded ``liblypning``. Cheap to hold, expensive to reload — keep one.

    Thread rules come straight from the ABI: a run is confined to one thread,
    two threads may run two programs at once, and one thread may not run two at
    once (:data:`BUSY` rather than an interleaved answer). This object adds no
    locking of its own, deliberately — a lock here would serialise the callers
    the ABI is happy to run in parallel.
    """

    def __init__(self, path: Optional[Path | str] = None) -> None:
        resolved = Path(path) if path else find_library()
        if resolved is None:
            raise LibraryError(
                "%s not found — run `lypning build --lib` "
                "(or point $LYPNING_LIB at a built library)" % LIB_NAME)
        if not Path(resolved).is_file():
            raise LibraryError("%s does not exist" % resolved)
        self.path = Path(resolved).resolve()
        why = _looks_like_a_library(self.path)
        if why:
            # Checked BEFORE `dlopen`, because `dlopen` is where a truncated
            # file stops being recoverable: the loader maps it, and the first
            # call reads a page past the end of the file and takes SIGBUS —
            # which no `except` can see. A link killed halfway through leaves
            # exactly such a file in a build tree.
            raise LibraryError("%s is not a usable shared library: %s" % (self.path, why))
        try:
            self._lib = ctypes.CDLL(str(self.path))
        except OSError as e:
            raise LibraryError("cannot load %s: %s" % (self.path, e)) from e
        try:
            self._bind()
        except AttributeError as e:
            raise LibraryError(
                "%s is missing a symbol this binding needs (%s) — it is not a "
                "lypning C ABI library, or it is an older one" % (self.path, e)) from e
        #: What the LIBRARY says, not what this module remembers. Kept as an
        #: attribute so a caller reporting the number reports the one that came
        #: out of the shared object — the two are equal here only because the
        #: check below refuses to build a `Library` when they are not.
        self.abi = self._lib.lypning_abi_version()
        if self.abi != ABI_VERSION:
            raise LibraryError(
                "%s speaks ABI %d, this binding speaks %d" % (self.path, self.abi, ABI_VERSION))

    # ctypes defaults every argument and return value to `int`, which is 32 bits
    # and silently truncates every pointer this ABI hands back. Declaring all of
    # them is not boilerplate; it is the difference between a binding and a
    # segfault on the first call.
    def _bind(self) -> None:
        L = self._lib
        c, cp, sz, u8p = ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_ubyte)
        _c(L.lypning_abi_version, ctypes.c_uint32, [])
        _c(L.lypning_version, c, [])
        _c(L.lypning_route_new, cp, [c, sz])
        _c(L.lypning_route_engine, c, [cp])
        _c(L.lypning_route_kind, c, [cp])
        _c(L.lypning_route_detail, c, [cp])
        _c(L.lypning_route_import_count, sz, [cp])
        _c(L.lypning_route_import, c, [cp, sz])
        _c(L.lypning_route_free, None, [cp])
        _c(L.lypning_request_new, cp, [c, sz])
        _c(L.lypning_request_set_filename, ctypes.c_int, [cp, c, sz])
        _c(L.lypning_request_add_arg, ctypes.c_int, [cp, c, sz])
        _c(L.lypning_request_set_stdin, ctypes.c_int, [cp, c, sz])
        _c(L.lypning_request_set_filesystem, None, [cp, ctypes.c_int])
        _c(L.lypning_request_set_step_limit, None, [cp, ctypes.c_uint64])
        _c(L.lypning_request_set_output_limit, None, [cp, sz])
        _c(L.lypning_request_free, None, [cp])
        _c(L.lypning_run, cp, [cp])
        _c(L.lypning_result_status, ctypes.c_int32, [cp])
        _c(L.lypning_result_exit_code, ctypes.c_int32, [cp])
        _c(L.lypning_result_stdout, u8p, [cp, ctypes.POINTER(sz)])
        _c(L.lypning_result_stderr, u8p, [cp, ctypes.POINTER(sz)])
        _c(L.lypning_result_kind, c, [cp])
        _c(L.lypning_result_detail, c, [cp])
        _c(L.lypning_result_committed, ctypes.c_int, [cp])
        _c(L.lypning_result_should_fall_onward, ctypes.c_int, [cp])
        _c(L.lypning_result_free, None, [cp])
        _c(L.lypning_fall_onward, ctypes.c_int, [ctypes.c_int32, c, sz])

    # --- the API -------------------------------------------------------------

    @property
    def version(self) -> str:
        return (self._lib.lypning_version() or b"").decode("utf-8", "replace")

    def route(self, source: str) -> Route:
        """Which tier should run this? One parse, no execution."""
        raw = source.encode("utf-8")
        h = self._lib.lypning_route_new(raw, len(raw))
        if not h:
            # Only non-UTF-8 source gets here, which `str.encode` cannot produce
            # — but a caller passing bytes through would, and cpython is the
            # honest answer for a program this runtime cannot even read.
            return Route(engine="cpython", kind="encoding", detail="source is not UTF-8")
        try:
            n = self._lib.lypning_route_import_count(h)
            return Route(
                engine=_s(self._lib.lypning_route_engine(h)),
                kind=_s(self._lib.lypning_route_kind(h)),
                detail=_s(self._lib.lypning_route_detail(h)),
                imports=[_s(self._lib.lypning_route_import(h, i)) for i in range(n)],
            )
        finally:
            self._lib.lypning_route_free(h)

    def run(self, source: str, *, args: Sequence[str] = (), filename: Optional[str] = None,
            stdin: bytes = b"", filesystem: bool = True, step_limit: int = 0,
            output_limit: int = 0) -> Outcome:
        """Run the program in this thread and return everything it produced.

        No process is spawned. A ``status`` of :data:`UNSUPPORTED` means lypning
        refused and wrote nothing — hand the program to CPython.

        ``step_limit`` is the only bound an in-process run can be given: there
        is no process to kill, so a program that will not stop is a hang in the
        caller's own thread. Pass one for anything a model wrote.
        """
        raw = source.encode("utf-8")
        q = self._lib.lypning_request_new(raw, len(raw))
        if not q:
            raise LibraryError("lypning_request_new rejected the source (not UTF-8?)")
        try:
            if filename is not None:
                fn = filename.encode("utf-8")
                self._lib.lypning_request_set_filename(q, fn, len(fn))
            for a in args:
                ab = a.encode("utf-8")
                self._lib.lypning_request_add_arg(q, ab, len(ab))
            self._lib.lypning_request_set_stdin(q, stdin, len(stdin))
            self._lib.lypning_request_set_filesystem(q, 1 if filesystem else 0)
            self._lib.lypning_request_set_step_limit(q, int(step_limit))
            self._lib.lypning_request_set_output_limit(q, int(output_limit))
            r = self._lib.lypning_run(q)
            if not r:
                raise LibraryError("lypning_run returned NULL")
            try:
                return Outcome(
                    status=self._lib.lypning_result_status(r),
                    exit_code=self._lib.lypning_result_exit_code(r),
                    stdout=_b(self._lib.lypning_result_stdout, r),
                    stderr=_b(self._lib.lypning_result_stderr, r),
                    kind=_s(self._lib.lypning_result_kind(r)),
                    detail=_s(self._lib.lypning_result_detail(r)),
                    committed=bool(self._lib.lypning_result_committed(r)),
                    fall_onward=bool(self._lib.lypning_result_should_fall_onward(r)),
                )
            finally:
                self._lib.lypning_result_free(r)
        finally:
            self._lib.lypning_request_free(q)

    def fall_onward(self, exit_code: int, stderr: bytes) -> bool:
        """The dispatcher's predicate, for a caller chaining other engines."""
        return bool(self._lib.lypning_fall_onward(exit_code, stderr, len(stderr)))


def _s(p: Optional[bytes]) -> str:
    return (p or b"").decode("utf-8", "replace")


def _b(fn, handle) -> bytes:
    """Copy a result buffer out, in one memcpy.

    ``bytes(bytearray(p[:n]))`` is the obvious spelling and is forty times
    slower: slicing a ``POINTER(c_ubyte)`` builds a Python list with one int
    object per byte before any bytes object exists — 17 ms for a megabyte,
    inside the window :func:`lypning.engines.run_library` reports as the run's
    cost. ``string_at`` is a single memcpy and is NUL-safe: it takes the length.
    """
    n = ctypes.c_size_t(0)
    p = fn(handle, ctypes.byref(n))
    if not p or n.value == 0:
        return b""
    return ctypes.string_at(p, n.value)


# --- the pinned contract -----------------------------------------------------


def check_refusal_contract(path: Optional[Path | str] = None) -> tuple[bool, str]:
    """``(ok, why)`` for the refusal contract, translated into library terms.

    The binary's version of this check (``build.check_refusal_contract``) asserts
    exit 90, the exact line on stderr, and **nothing on stdout**. All three have
    an exact counterpart here, and the third is again the one that hurts: an
    embedded runtime that let a refused program's partial output through would
    hand a harness a wrong answer with no way to notice.

    The fourth assertion has no counterpart in the binary at all, and is the one
    embedding adds: ``fall_onward`` must be true, because in-process it is the
    HOST that owns the retry, and a refusal a host does not act on is a program
    that simply never ran.
    """
    try:
        lib = Library(path)
    except LibraryError as e:
        return False, str(e)
    out = lib.run(REFUSAL_PROGRAM)
    if out.status != UNSUPPORTED:
        return False, "status %s, expected unsupported (%r)" % (
            out.status_name, out.stderr[:160])
    if out.exit_code != 90:
        return False, "exit code %d, expected 90" % out.exit_code
    if out.stdout != b"":
        return False, "a refused run produced stdout: %r" % out.stdout[:120]
    if out.stderr != REFUSAL_LINE:
        return False, "stderr was %r, expected %r" % (out.stderr[:160], REFUSAL_LINE)
    if out.committed:
        return False, "a refused run reported that it committed"
    if not out.fall_onward:
        return False, "a refused run did not ask to be routed onward"
    return True, ""
