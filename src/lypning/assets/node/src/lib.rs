//! lypning for Node, and the one thing it must get right.
//!
//! **A refusal crosses into JS as a value, never as a thrown `Error`.**
//!
//! That is the whole invariant of this file. JS has one obvious channel for
//! "that did not work" and it is `throw`, so the lazy binding would map
//! `LYPNING_UNSUPPORTED` onto an exception and every harness in the world would
//! wrap `lypning.run()` in a `try/catch` that logs an error and stops. But a
//! refusal is not a failure: lypning ran *none* of the program, wrote nothing,
//! and the host is supposed to run it on CPython now. A binding that throws
//! there has converted a speedup into a bug, in the one place its users will
//! read as "lypning is broken" rather than "lypning declined".
//!
//! So `run()` always returns an object, and the field a harness branches on is
//! spelled out in full — `fallOnward` — because `r.status === 2` is a thing
//! somebody has to look up and `if (r.fallOnward)` is not:
//!
//!     const r = lypning.run(src);
//!     if (r.fallOnward) { runOnPython3(src); } else { use(r.stdout); }
//!
//! `throw` is left for the caller passing the wrong *type* — a number where
//! source belongs — which is a bug in their code, is nothing to do with the
//! subset, and must not be confusable with a refusal.
//!
//! Everything here goes through the C ABI in `capi.rs` rather than through
//! `lypning::embed` directly, even though both are in this process and the
//! direct call would be shorter. There are four hosts and one contract; a
//! second path to `embed::run` is a second place for the refusal contract to
//! drift, and it would drift silently, in the host nobody builds by accident.

mod napi;

use lypning::capi as c;
use napi::{Env, Value};
use std::ffi::{c_char, c_void};
use std::ptr;

/// `Status` as JS spells it. Derived from the `capi` constants so the names and
/// the numbers cannot disagree; JS never carries a copy of this table.
fn status_name(status: i32) -> &'static str {
    match status {
        c::LYPNING_OK => "ok",
        c::LYPNING_ERROR => "error",
        c::LYPNING_UNSUPPORTED => "unsupported",
        c::LYPNING_BUSY => "busy",
        c::LYPNING_PANIC => "panic",
        _ => "unknown",
    }
}

// ---- version ---------------------------------------------------------------

unsafe extern "C" fn js_version(env: Env, _info: napi::CbInfo) -> Value {
    napi::string(env, &napi::from_c(c::lypning_version()))
}

unsafe extern "C" fn js_abi_version(env: Env, _info: napi::CbInfo) -> Value {
    napi::uint32(env, c::lypning_abi_version())
}

// ---- route -----------------------------------------------------------------

/// `route(source)` → which interpreter should run it. One parse, no execution.
///
/// Cheap enough to call on every program: a harness that wants to know whether
/// a CPython spawn is worth paying for at all asks here, before it asks `run`.
unsafe extern "C" fn js_route(env: Env, info: napi::CbInfo) -> Value {
    let [src] = napi::args::<1>(env, info);
    let Some(source) = napi::as_string(env, src) else {
        return napi::type_error(env, "lypning.route(source): source must be a string");
    };

    let r = c::lypning_route_new(source.as_ptr() as *const c_char, source.len());
    if r.is_null() {
        // Unreachable from JS — node hands us UTF-8 — but a null here would
        // otherwise be read back as an empty route, which is a lie.
        return napi::type_error(env, "lypning.route(source): source is not valid UTF-8");
    }

    let n = c::lypning_route_import_count(r);
    let imports: Vec<Value> = (0..n)
        .map(|i| napi::string(env, &napi::from_c(c::lypning_route_import(r, i))))
        .collect();

    let out = napi::object(env);
    let engine = napi::string(env, &napi::from_c(c::lypning_route_engine(r)));
    let kind = napi::string(env, &napi::from_c(c::lypning_route_kind(r)));
    let detail = napi::string(env, &napi::from_c(c::lypning_route_detail(r)));
    let imports = napi::array(env, &imports);
    napi::set(env, out, "engine", engine);
    napi::set(env, out, "kind", kind);
    napi::set(env, out, "detail", detail);
    napi::set(env, out, "imports", imports);

    c::lypning_route_free(r);
    out
}

// ---- run -------------------------------------------------------------------

/// Fill a request from an options object. `Err(msg)` is a caller type error;
/// the request is freed by the caller either way.
unsafe fn apply_opts(env: Env, q: *mut c::lypning_request, opts: Value) -> Result<(), String> {
    if napi::is_undefined(env, opts) {
        return Ok(());
    }

    if let Some(v) = napi::get(env, opts, "filename") {
        let name = napi::as_string(env, v).ok_or("opts.filename must be a string")?;
        c::lypning_request_set_filename(q, name.as_ptr() as *const c_char, name.len());
    }

    if let Some(v) = napi::get(env, opts, "args") {
        let items = napi::as_array(env, v).ok_or("opts.args must be an array of strings")?;
        for item in items {
            let a = napi::as_string(env, item).ok_or("opts.args must be an array of strings")?;
            c::lypning_request_add_arg(q, a.as_ptr() as *const c_char, a.len());
        }
    }

    if let Some(v) = napi::get(env, opts, "stdin") {
        let b = napi::as_bytes(env, v).ok_or("opts.stdin must be a string or a Uint8Array")?;
        c::lypning_request_set_stdin(q, b.as_ptr() as *const c_void, b.len());
    }

    if let Some(v) = napi::get(env, opts, "filesystem") {
        let allow = napi::as_bool(env, v).ok_or("opts.filesystem must be a boolean")?;
        c::lypning_request_set_filesystem(q, allow as i32);
    }

    if let Some(v) = napi::get(env, opts, "outputLimit") {
        let n = napi::as_f64(env, v).ok_or("opts.outputLimit must be a number")?;
        // Saturating, never `as usize`. On a 32-bit node a truncating cast turns
        // a 4 GiB limit into 0, and 0 is the ONE value that means "no limit" —
        // the caller's cap would go missing in exactly the direction that hurts.
        let bytes = nonneg(n, "opts.outputLimit")?;
        c::lypning_request_set_output_limit(q, bytes.min(usize::MAX as u64) as usize);
    }

    if let Some(v) = napi::get(env, opts, "stepLimit") {
        let n = napi::as_f64(env, v).ok_or("opts.stepLimit must be a number")?;
        c::lypning_request_set_step_limit(q, nonneg(n, "opts.stepLimit")?);
    }

    Ok(())
}

/// A JS number as a count. Rejected rather than saturated: `-1` and `NaN` both
/// mean the caller computed something wrong, and silently reading either as "no
/// limit" is how a step limit meant to stop `while True: pass` goes missing.
fn nonneg(n: f64, what: &str) -> Result<u64, String> {
    if n.is_finite() && n >= 0.0 && n <= u64::MAX as f64 {
        Ok(n as u64)
    } else {
        Err(format!("{} must be a non-negative number", what))
    }
}

/// `run(source, opts)` → the outcome, always as an object.
///
/// Never spawns a process, never touches node's stdin, stdout or stderr: the
/// program's output is captured and comes back in `stdout`/`stderr`. On a
/// program lypning accepts, this call *is* the run — which is the reason to
/// load the addon rather than shell out to the `lypning` binary.
unsafe extern "C" fn js_run(env: Env, info: napi::CbInfo) -> Value {
    let [src, opts] = napi::args::<2>(env, info);
    let Some(source) = napi::as_string(env, src) else {
        return napi::type_error(env, "lypning.run(source, opts): source must be a string");
    };

    let q = c::lypning_request_new(source.as_ptr() as *const c_char, source.len());
    if q.is_null() {
        return napi::type_error(env, "lypning.run(source, opts): source is not valid UTF-8");
    }

    if let Err(msg) = apply_opts(env, q, opts) {
        c::lypning_request_free(q);
        return napi::type_error(env, &format!("lypning.run: {}", msg));
    }

    // The last point at which nothing has happened yet.
    //
    // Reading the options above can run the caller's own JS — an option that is
    // a getter, an object behind a Proxy — and if that threw, every Node-API
    // call since has been a silent no-op and the options we collected are not
    // the ones they asked for. Running now would execute the program, let node
    // throw their error over the answer, and hand a harness that catches and
    // falls onward a program it has ALREADY run: the double side effect the
    // commit barrier exists to prevent. So we run nothing and let their
    // exception be the answer.
    if napi::exception_pending(env) {
        c::lypning_request_free(q);
        return napi::undefined(env);
    }

    let r = c::lypning_run(q);
    c::lypning_request_free(q);
    if r.is_null() {
        return napi::type_error(env, "lypning.run: the runtime returned no result");
    }

    let mut out_len = 0usize;
    let out_ptr = c::lypning_result_stdout(r, &mut out_len);
    let mut err_len = 0usize;
    let err_ptr = c::lypning_result_stderr(r, &mut err_len);
    let stdout = slice(out_ptr, out_len);
    let stderr = slice(err_ptr, err_len);

    let status = c::lypning_result_status(r);
    let o = napi::object(env);
    let v = napi::int32(env, status);
    napi::set(env, o, "status", v);
    let v = napi::string(env, status_name(status));
    napi::set(env, o, "statusName", v);
    let v = napi::int32(env, c::lypning_result_exit_code(r));
    napi::set(env, o, "exitCode", v);
    let v = napi::buffer(env, stdout);
    napi::set(env, o, "stdout", v);
    let v = napi::buffer(env, stderr);
    napi::set(env, o, "stderr", v);
    let v = napi::string(env, &napi::from_c(c::lypning_result_kind(r)));
    napi::set(env, o, "kind", v);
    let v = napi::string(env, &napi::from_c(c::lypning_result_detail(r)));
    napi::set(env, o, "detail", v);
    let v = napi::boolean(env, c::lypning_result_committed(r) != 0);
    napi::set(env, o, "committed", v);
    // Last, and named the way the docs name it: this is the field a harness is
    // supposed to branch on, and the only one it must not get wrong.
    let v = napi::boolean(env, c::lypning_result_should_fall_onward(r) != 0);
    napi::set(env, o, "fallOnward", v);

    c::lypning_result_free(r);
    o
}

unsafe fn slice<'a>(p: *const u8, len: usize) -> &'a [u8] {
    if p.is_null() || len == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(p, len)
    }
}

// ---- fallOnward ------------------------------------------------------------

/// `fallOnward(exitCode, stderr)` — the dispatcher's predicate, for a harness
/// that chains other interpreters too.
///
/// Exported so a JS harness running `lypning-mp` or a sandboxed `python3` in a
/// child process can ask the same question about *their* exit codes instead of
/// re-deriving it from the exit-90 contract and getting the MemoryError case
/// wrong.
unsafe extern "C" fn js_fall_onward(env: Env, info: napi::CbInfo) -> Value {
    let [code, err] = napi::args::<2>(env, info);
    let Some(n) = napi::as_f64(env, code) else {
        return napi::type_error(env, "lypning.fallOnward(exitCode, stderr): exitCode must be a number");
    };
    let bytes = if napi::is_undefined(env, err) {
        Vec::new()
    } else {
        match napi::as_bytes(env, err) {
            Some(b) => b,
            None => {
                return napi::type_error(
                    env,
                    "lypning.fallOnward(exitCode, stderr): stderr must be a string or a Uint8Array",
                )
            }
        }
    };
    let yes = c::lypning_fall_onward(n as i32, bytes.as_ptr() as *const c_void, bytes.len());
    napi::boolean(env, yes != 0)
}

// ---- registration ----------------------------------------------------------

/// The symbol node looks for after `dlopen`. Nothing links it; node finds it by
/// name, which is why `index.js` can load this file with `process.dlopen` and
/// no loader of our own.
#[no_mangle]
pub unsafe extern "C" fn napi_register_module_v1(env: Env, exports: Value) -> Value {
    if env.is_null() || exports.is_null() {
        return ptr::null_mut();
    }
    napi::export(env, exports, "version", js_version);
    napi::export(env, exports, "abiVersion", js_abi_version);
    napi::export(env, exports, "route", js_route);
    napi::export(env, exports, "run", js_run);
    napi::export(env, exports, "fallOnward", js_fall_onward);
    exports
}
