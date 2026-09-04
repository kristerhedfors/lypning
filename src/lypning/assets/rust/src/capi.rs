//! The C ABI — the surface every other language binds to.
//!
//! Every host in this repository binds to it (the table in docs/EMBEDDING.md
//! section 4 is the list) and only one of them speaks Rust. So this file is the
//! real API, and everything else is a convenience over it: a C header, a
//! header-only C++ wrapper, a Node addon, a ctypes module and every binding
//! after them call exactly these symbols. Adding a capability here adds it
//! everywhere; adding it anywhere else adds it in one place and quietly makes
//! the hosts disagree.
//!
//! Five rules, and they are what make the surface safe to hand to a stranger:
//!
//! 1. **Opaque handles only.** No struct layout crosses the boundary, so a host
//!    compiled against v1 keeps working when a field is added.
//! 2. **Nothing unwinds.** Every entry point catches, because an unwind across
//!    an `extern "C"` frame is undefined behaviour and an abort inside a host's
//!    process is a crash it cannot explain.
//! 3. **The callee owns what it returns.** Every pointer handed out stays valid
//!    until the matching `_free`, and a returned string is NUL-terminated so C
//!    can use it without a length.
//! 4. **A null in is a null or a zero out.** Never a crash: the most common
//!    integration bug is a missed allocation failure, and it must not become
//!    ours.
//! 5. **Bytes, not strings, for program output.** A one-liner's stdout is
//!    whatever the program printed — invalid UTF-8 included — and re-encoding
//!    it would be a wrong answer of exactly the kind this project exists to
//!    refuse.

// The library must be built with `panic = "unwind"` (the `release-lib` profile).
// This is enforced rather than documented because the failure it prevents is
// invisible until it happens in someone else's process: with `abort`, rule 2
// above cannot be implemented at all — `catch_unwind` never runs, and the first
// bug in the interpreter takes the host down with it.
#[cfg(panic = "abort")]
compile_error!(
    "the C ABI needs panic=unwind so it can catch at the boundary; build the library with \
     `--profile release-lib` (see Cargo.toml), not `--release`"
);

use crate::embed::{self, Outcome, Request, Status};
use crate::route;
use std::ffi::{c_char, c_int, c_void, CString};
use std::panic::catch_unwind;
use std::ptr;

/// Values of [`lypning_result_status`]. Kept in sync by hand with the enum in
/// `lypning.h`, and asserted equal by the C example's own assertions.
pub const LYPNING_OK: i32 = 0;
pub const LYPNING_ERROR: i32 = 1;
pub const LYPNING_UNSUPPORTED: i32 = 2;
pub const LYPNING_BUSY: i32 = 3;
pub const LYPNING_PANIC: i32 = 4;

fn status_code(s: Status) -> i32 {
    match s {
        Status::Ok => LYPNING_OK,
        Status::Error => LYPNING_ERROR,
        Status::Unsupported => LYPNING_UNSUPPORTED,
        Status::Busy => LYPNING_BUSY,
        Status::Panic => LYPNING_PANIC,
    }
}

/// A `CString` that cannot fail: an interior NUL is dropped rather than turned
/// into an error nobody could act on. Only the runtime's own messages reach
/// this, and none of them contain one.
fn cstr(s: &str) -> CString {
    CString::new(s.replace('\0', "")).unwrap_or_default()
}

/// One readable NUL, so the pointer returned for an empty buffer or a NULL
/// handle is a real address and C code that treats it as a string finds it
/// terminated.
static EMPTY: [u8; 1] = [0];

/// `""` for every `const char *` accessor handed a NULL handle. A host that
/// forgot the NULL check and did `strcmp(lypning_route_kind(r), "module")`
/// would otherwise crash inside libc, far from the line that was wrong; an
/// empty string is the answer a live handle gives when there is nothing to
/// say, and the same answer is safe here.
fn empty_cstr() -> *const c_char {
    EMPTY.as_ptr() as *const c_char
}

/// Borrow `len` bytes as `&str`, or `None` if they are not UTF-8.
///
/// Not lossy on purpose. Python source that is not UTF-8 is a program CPython
/// would reject, and silently repairing it here would run something the caller
/// did not write.
unsafe fn as_str<'a>(p: *const c_char, len: usize) -> Option<&'a str> {
    if p.is_null() {
        return None;
    }
    std::str::from_utf8(std::slice::from_raw_parts(p as *const u8, len)).ok()
}

/// Run `f`, and turn any panic inside it into `fallback`.
fn guard<T>(fallback: T, f: impl FnOnce() -> T) -> T {
    catch_unwind(std::panic::AssertUnwindSafe(f)).unwrap_or(fallback)
}

// ---- version ---------------------------------------------------------------

/// The ABI version. Bumped only when a symbol changes shape; a host should
/// refuse to run against a number it does not know.
#[no_mangle]
pub extern "C" fn lypning_abi_version() -> u32 {
    crate::ABI_VERSION
}

/// The runtime version — the same string `lypning --version` prints. Static:
/// never freed.
#[no_mangle]
pub extern "C" fn lypning_version() -> *const c_char {
    concat!(env!("CARGO_PKG_VERSION"), "\0").as_ptr() as *const c_char
}

// ---- routing ---------------------------------------------------------------

/// The classifier's answer, owned by the caller until `lypning_route_free`.
pub struct lypning_route {
    engine: CString,
    kind: CString,
    detail: CString,
    imports: Vec<CString>,
}

/// Which interpreter should run this program? One parse, no execution.
///
/// This is the call a harness makes when it wants the decision without the
/// answer — to decide whether spawning CPython is worth it, to log why, or to
/// refuse a program that imports something it does not want run.
#[no_mangle]
pub unsafe extern "C" fn lypning_route_new(src: *const c_char, len: usize) -> *mut lypning_route {
    guard(ptr::null_mut(), || {
        let Some(s) = as_str(src, len) else {
            return ptr::null_mut();
        };
        let r = route::route(s);
        Box::into_raw(Box::new(lypning_route {
            engine: cstr(r.engine.as_str()),
            kind: cstr(&r.kind),
            detail: cstr(&r.detail),
            imports: r.imports.iter().map(|i| cstr(i)).collect(),
        }))
    })
}

/// `"lypning"`, `"lypning-mp"` or `"cpython"` — the tier that should run it.
/// `""` for a NULL handle, as for every string accessor here: a route the host
/// never got names no tier, and a host that failed to check must not crash in
/// `strcmp` for it.
#[no_mangle]
pub unsafe extern "C" fn lypning_route_engine(r: *const lypning_route) -> *const c_char {
    match r.as_ref() {
        Some(r) => r.engine.as_ptr(),
        None => empty_cstr(),
    }
}

/// The construct that pushed the program past lypning — `"module"`, `"async"`,
/// `""` when nothing did.
#[no_mangle]
pub unsafe extern "C" fn lypning_route_kind(r: *const lypning_route) -> *const c_char {
    match r.as_ref() {
        Some(r) => r.kind.as_ptr(),
        None => empty_cstr(),
    }
}

/// The human-readable half of the same answer: `"import re"`.
#[no_mangle]
pub unsafe extern "C" fn lypning_route_detail(r: *const lypning_route) -> *const c_char {
    match r.as_ref() {
        Some(r) => r.detail.as_ptr(),
        None => empty_cstr(),
    }
}

/// How many modules the program imports.
#[no_mangle]
pub unsafe extern "C" fn lypning_route_import_count(r: *const lypning_route) -> usize {
    r.as_ref().map_or(0, |r| r.imports.len())
}

/// The `i`th import — **sorted and deduplicated**, not in source order — or
/// NULL when `i` is out of range. Use `lypning_route_detail` for the one import
/// that actually decided the tier.
///
/// NULL is the loop terminator and only that: a NULL *handle* answers `""`
/// like every other string accessor, so a host that indexes a route it never
/// got does not read a NULL it was not testing for.
#[no_mangle]
pub unsafe extern "C" fn lypning_route_import(
    r: *const lypning_route,
    i: usize,
) -> *const c_char {
    match r.as_ref() {
        Some(r) => match r.imports.get(i) {
            Some(s) => s.as_ptr(),
            None => ptr::null(),
        },
        None => empty_cstr(),
    }
}

#[no_mangle]
pub unsafe extern "C" fn lypning_route_free(r: *mut lypning_route) {
    if !r.is_null() {
        drop(Box::from_raw(r));
    }
}

// ---- requests --------------------------------------------------------------

/// A program and everything the host decides about it. Reusable: a host may
/// build one, run it, change the stdin and run it again.
pub struct lypning_request {
    req: Request,
}

/// A new request over `len` bytes of UTF-8 Python source. NULL if the bytes are
/// not UTF-8 — a program CPython would reject too.
#[no_mangle]
pub unsafe extern "C" fn lypning_request_new(
    src: *const c_char,
    len: usize,
) -> *mut lypning_request {
    guard(ptr::null_mut(), || match as_str(src, len) {
        Some(s) => Box::into_raw(Box::new(lypning_request {
            req: Request::new(s),
        })),
        None => ptr::null_mut(),
    })
}

/// `sys.argv[0]`. Unset — the default — gives CPython's `-c` shape.
#[no_mangle]
pub unsafe extern "C" fn lypning_request_set_filename(
    q: *mut lypning_request,
    name: *const c_char,
    len: usize,
) -> c_int {
    guard(-1, || match (q.as_mut(), as_str(name, len)) {
        (Some(q), Some(s)) => {
            q.req.filename = Some(s.to_string());
            0
        }
        _ => -1,
    })
}

/// Append one entry to `sys.argv[1:]`.
#[no_mangle]
pub unsafe extern "C" fn lypning_request_add_arg(
    q: *mut lypning_request,
    arg: *const c_char,
    len: usize,
) -> c_int {
    guard(-1, || match (q.as_mut(), as_str(arg, len)) {
        (Some(q), Some(s)) => {
            q.req.args.push(s.to_string());
            0
        }
        _ => -1,
    })
}

/// The program's stdin. Bytes, not a string: the largest cluster in the corpus
/// is `stdin -> transform -> stdout`, and some of it is not text.
///
/// Unset means an empty stream. The library never reads the host's fd 0.
#[no_mangle]
pub unsafe extern "C" fn lypning_request_set_stdin(
    q: *mut lypning_request,
    data: *const c_void,
    len: usize,
) -> c_int {
    guard(-1, || {
        let Some(q) = q.as_mut() else { return -1 };
        q.req.stdin = Some(if data.is_null() || len == 0 {
            Vec::new()
        } else {
            std::slice::from_raw_parts(data as *const u8, len).to_vec()
        });
        0
    })
}

/// May the program touch the filesystem? Non-zero (the default) yes.
///
/// Zero turns every file operation into a **refusal** — never a lie. The
/// program is not told the file is missing; the host is told lypning would not
/// run this, and decides for itself whether to hand it to CPython.
#[no_mangle]
pub unsafe extern "C" fn lypning_request_set_filesystem(q: *mut lypning_request, allow: c_int) {
    if let Some(q) = q.as_mut() {
        q.req.filesystem = allow != 0;
    }
}

/// Refuse once the program has taken `steps` statements or loop iterations.
/// `0` (the default) is no limit.
///
/// This is how an embedded run is bounded, and a host that runs model-written
/// programs wants it set. A process can be killed; a function call in your own
/// thread cannot, so `while True: pass` with no limit is a hang you cannot
/// recover from. Passing the limit is a REFUSAL — routable like every other, so
/// the program still gets its answer from CPython, under whatever timeout you
/// already apply to spawning it.
#[no_mangle]
pub unsafe extern "C" fn lypning_request_set_step_limit(q: *mut lypning_request, steps: u64) {
    if let Some(q) = q.as_mut() {
        q.req.step_limit = steps;
    }
}

/// Refuse once captured output passes `bytes`. `0` (the default) is no limit.
#[no_mangle]
pub unsafe extern "C" fn lypning_request_set_output_limit(q: *mut lypning_request, bytes: usize) {
    if let Some(q) = q.as_mut() {
        q.req.output_limit = bytes;
    }
}

#[no_mangle]
pub unsafe extern "C" fn lypning_request_free(q: *mut lypning_request) {
    if !q.is_null() {
        drop(Box::from_raw(q));
    }
}

// ---- running ---------------------------------------------------------------

/// Everything one run produced. Owns its buffers.
pub struct lypning_result {
    out: Outcome,
    kind: CString,
    detail: CString,
}

fn wrap(out: Outcome) -> *mut lypning_result {
    let kind = cstr(&out.kind);
    let detail = cstr(&out.detail);
    Box::into_raw(Box::new(lypning_result { out, kind, detail }))
}

/// Run the program in **this thread**, with its output captured.
///
/// Never spawns anything. On a program lypning accepts this is the whole cost
/// of the run — no fork, no exec, no pipe — which is the reason to link the
/// library instead of calling the binary.
///
/// One run per thread at a time; a re-entrant call returns `LYPNING_BUSY`
/// having executed nothing.
#[no_mangle]
pub unsafe extern "C" fn lypning_run(q: *const lypning_request) -> *mut lypning_result {
    guard(ptr::null_mut(), || match q.as_ref() {
        Some(q) => wrap(embed::run(&q.req)),
        None => ptr::null_mut(),
    })
}

/// `LYPNING_OK` / `_ERROR` / `_UNSUPPORTED` / `_BUSY` / `_PANIC`, or `-1` for a
/// NULL result.
///
/// **`LYPNING_UNSUPPORTED` is not a failure.** It means the program is outside
/// the subset, nothing was written, and the host should run it on CPython.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_status(r: *const lypning_result) -> i32 {
    r.as_ref().map_or(-1, |r| status_code(r.out.status))
}

/// The exit code the `lypning` binary would have returned: the program's own,
/// `1` for an uncaught exception, `90` for a refusal, `-1` for a NULL result or
/// the library's own failure.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_exit_code(r: *const lypning_result) -> i32 {
    r.as_ref().map_or(-1, |r| r.out.exit_code)
}

/// The program's stdout. Empty after a refusal — the commit barrier discards
/// staged output, which is exactly what makes the retry on another interpreter
/// safe. `len` may be NULL.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_stdout(
    r: *const lypning_result,
    len: *mut usize,
) -> *const u8 {
    bytes(r.as_ref().map(|r| &r.out.stdout), len)
}

/// The program's stderr — its traceback, or, after a refusal, exactly the one
/// `<engine>: unsupported: <kind>: <detail>` line the binary would have printed.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_stderr(
    r: *const lypning_result,
    len: *mut usize,
) -> *const u8 {
    bytes(r.as_ref().map(|r| &r.out.stderr), len)
}

unsafe fn bytes(b: Option<&Vec<u8>>, len: *mut usize) -> *const u8 {
    match b {
        Some(b) => {
            if !len.is_null() {
                *len = b.len();
            }
            // Never NULL for an empty buffer: a host that checks the pointer
            // before the length would read "no result" from "printed nothing".
            // A zero-length slice's pointer is the type's ALIGNMENT — literally
            // `0x1` — which is non-NULL as promised but not an address anything
            // may read. A host that memcpy's length 0 from it is fine and one
            // that peeks a byte is not, so the empty case points at a real byte
            // instead.
            if b.is_empty() {
                EMPTY.as_ptr()
            } else {
                b.as_ptr()
            }
        }
        None => {
            if !len.is_null() {
                *len = 0;
            }
            ptr::null()
        }
    }
}

/// The refusal's kind — `"module"`, `"async"`, `"sandbox"` — or `""`.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_kind(r: *const lypning_result) -> *const c_char {
    match r.as_ref() {
        Some(r) => r.kind.as_ptr(),
        None => empty_cstr(),
    }
}

/// The refusal's detail — `"import re"` — or `""`.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_detail(r: *const lypning_result) -> *const c_char {
    match r.as_ref() {
        Some(r) => r.detail.as_ptr(),
        None => empty_cstr(),
    }
}

/// Did the run pass the commit point, after which it is no longer undoable?
/// `0` on a refusal, which is what makes the program safe to run elsewhere.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_committed(r: *const lypning_result) -> c_int {
    r.as_ref().map_or(0, |r| r.out.committed as c_int)
}

/// Should the host run this program on CPython now?
///
/// The one call a harness needs to get right. True exactly when lypning refused
/// and left nothing behind.
#[no_mangle]
pub unsafe extern "C" fn lypning_result_should_fall_onward(r: *const lypning_result) -> c_int {
    r.as_ref().map_or(0, |r| r.out.should_fall_onward() as c_int)
}

#[no_mangle]
pub unsafe extern "C" fn lypning_result_free(r: *mut lypning_result) {
    if !r.is_null() {
        drop(Box::from_raw(r));
    }
}

/// The dispatcher's own predicate, for a host that runs OTHER interpreters too.
///
/// Exit 90 is the declared refusal; the other two signals came out of
/// measurement rather than design (a `MemoryError` is a property of the
/// engine's heap, not the program's answer; a traceback reported with exit 0
/// would hand the caller empty stdout and a success status). A harness that
/// chains lypning -> lypning-mp -> CPython should ask this rather than reinvent
/// it.
#[no_mangle]
pub unsafe extern "C" fn lypning_fall_onward(
    exit_code: i32,
    stderr: *const c_void,
    len: usize,
) -> c_int {
    guard(0, || {
        let e = if stderr.is_null() || len == 0 {
            &[][..]
        } else {
            std::slice::from_raw_parts(stderr as *const u8, len)
        };
        embed::fall_onward(exit_code, e) as c_int
    })
}
