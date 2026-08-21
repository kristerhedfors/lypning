//! Node-API, declared rather than linked.
//!
//! The invariant this file exists to hold: **nothing here is ever resolved at
//! link time.** These are the C entry points of the `node` executable itself,
//! and a Node addon is a plain shared object that node `dlopen`s into its own
//! address space — so the loader binds them, from the host process, at load.
//! Declaring them `extern "C"` and leaving them undefined is not a trick; it is
//! how every addon in existence works, and `node-gyp` and `napi-rs` exist to
//! generate this file, not to make it possible.
//!
//! That is the whole reason `assets/node/` has zero npm dependencies and zero
//! build steps beyond `cargo build` (CLAUDE.md invariant 6). It costs us the
//! ~30 declarations below, once. It buys a binding that a user with a Rust
//! toolchain can build offline, and one that cannot acquire a transitive
//! dependency later without somebody editing this file by hand.
//!
//! Two rules for everything below:
//!
//!   * **Every call's status is ignored on purpose, and every helper answers
//!     with a value.** A failing Node-API call has already queued a JS
//!     exception in the environment; node throws it the moment our callback
//!     returns. Checking each status would only let us throw a second, worse
//!     one over the top of the real error.
//!   * **A missing argument is `undefined`, not a panic.** `args()` prefills,
//!     because JS callers omit trailing arguments and that is not an error
//!     anywhere else in JS.

#![allow(dead_code)]

use std::ffi::{c_char, c_int, c_void, CStr, CString};
use std::ptr;

pub type Env = *mut c_void;
pub type Value = *mut c_void;
pub type CbInfo = *mut c_void;
pub type Callback = unsafe extern "C" fn(Env, CbInfo) -> Value;

/// `napi_valuetype`. Only the two we branch on are named.
pub const TYPE_UNDEFINED: c_int = 0;
pub const TYPE_NULL: c_int = 1;

/// `napi_typedarray_type`, the three whose element is one byte.
///
/// Named because the *element count* is what `napi_get_typedarray_info` reports,
/// and reading it as a byte count is only true for these. See [`as_bytes`].
pub const TA_INT8: c_int = 0;
pub const TA_UINT8: c_int = 1;
pub const TA_UINT8_CLAMPED: c_int = 2;

extern "C" {
    fn napi_get_undefined(env: Env, out: *mut Value) -> c_int;
    fn napi_get_boolean(env: Env, v: bool, out: *mut Value) -> c_int;
    fn napi_create_object(env: Env, out: *mut Value) -> c_int;
    fn napi_create_string_utf8(env: Env, s: *const c_char, len: usize, out: *mut Value) -> c_int;
    fn napi_create_int32(env: Env, v: i32, out: *mut Value) -> c_int;
    fn napi_create_uint32(env: Env, v: u32, out: *mut Value) -> c_int;
    fn napi_create_array_with_length(env: Env, len: usize, out: *mut Value) -> c_int;
    fn napi_set_element(env: Env, obj: Value, i: u32, v: Value) -> c_int;
    fn napi_set_named_property(env: Env, obj: Value, name: *const c_char, v: Value) -> c_int;
    fn napi_get_named_property(env: Env, obj: Value, name: *const c_char, out: *mut Value)
        -> c_int;
    fn napi_create_function(
        env: Env,
        name: *const c_char,
        len: usize,
        cb: Callback,
        data: *mut c_void,
        out: *mut Value,
    ) -> c_int;
    fn napi_get_cb_info(
        env: Env,
        info: CbInfo,
        argc: *mut usize,
        argv: *mut Value,
        this: *mut Value,
        data: *mut *mut c_void,
    ) -> c_int;
    fn napi_typeof(env: Env, v: Value, out: *mut c_int) -> c_int;
    fn napi_get_value_string_utf8(
        env: Env,
        v: Value,
        buf: *mut c_char,
        size: usize,
        written: *mut usize,
    ) -> c_int;
    fn napi_get_value_bool(env: Env, v: Value, out: *mut bool) -> c_int;
    fn napi_get_value_double(env: Env, v: Value, out: *mut f64) -> c_int;
    fn napi_is_array(env: Env, v: Value, out: *mut bool) -> c_int;
    fn napi_get_array_length(env: Env, v: Value, out: *mut u32) -> c_int;
    fn napi_get_element(env: Env, v: Value, i: u32, out: *mut Value) -> c_int;
    fn napi_is_typedarray(env: Env, v: Value, out: *mut bool) -> c_int;
    fn napi_get_typedarray_info(
        env: Env,
        v: Value,
        ty: *mut c_int,
        len: *mut usize,
        data: *mut *mut c_void,
        buf: *mut Value,
        offset: *mut usize,
    ) -> c_int;
    /// Copies. The alternative hands JS a pointer into a `Vec` that dies at the
    /// end of the call, which is a use-after-free that only shows up under GC
    /// pressure — i.e. in someone else's production, never in our example.
    fn napi_create_buffer_copy(
        env: Env,
        len: usize,
        data: *const c_void,
        out_data: *mut *mut c_void,
        out: *mut Value,
    ) -> c_int;
    fn napi_throw_type_error(env: Env, code: *const c_char, msg: *const c_char) -> c_int;
    fn napi_is_exception_pending(env: Env, out: *mut bool) -> c_int;
}

// ---- making values ---------------------------------------------------------

pub unsafe fn undefined(env: Env) -> Value {
    let mut v = ptr::null_mut();
    napi_get_undefined(env, &mut v);
    v
}

pub unsafe fn boolean(env: Env, b: bool) -> Value {
    let mut v = ptr::null_mut();
    napi_get_boolean(env, b, &mut v);
    v
}

pub unsafe fn int32(env: Env, n: i32) -> Value {
    let mut v = ptr::null_mut();
    napi_create_int32(env, n, &mut v);
    v
}

pub unsafe fn uint32(env: Env, n: u32) -> Value {
    let mut v = ptr::null_mut();
    napi_create_uint32(env, n, &mut v);
    v
}

pub unsafe fn string(env: Env, s: &str) -> Value {
    let mut v = ptr::null_mut();
    napi_create_string_utf8(env, s.as_ptr() as *const c_char, s.len(), &mut v);
    v
}

/// A NUL-terminated C string from the runtime, as a `&str`. Lossy because the
/// alternative is to drop a diagnostic on the floor; every string the C ABI
/// hands back is one of our own messages and is already UTF-8.
pub unsafe fn from_c(p: *const c_char) -> String {
    if p.is_null() {
        String::new()
    } else {
        CStr::from_ptr(p).to_string_lossy().into_owned()
    }
}

/// Program output crosses as a `Buffer`, never as a string.
///
/// This is the one conversion in the binding that is not a convenience: a
/// one-liner's stdout is whatever it printed, and `String::from_utf8_lossy` on
/// the way out would hand JS a U+FFFD where the program wrote a byte. That is a
/// wrong answer of exactly the kind lypning refuses rather than guesses at.
pub unsafe fn buffer(env: Env, b: &[u8]) -> Value {
    let mut v = ptr::null_mut();
    let mut data = ptr::null_mut();
    napi_create_buffer_copy(env, b.len(), b.as_ptr() as *const c_void, &mut data, &mut v);
    v
}

pub unsafe fn object(env: Env) -> Value {
    let mut v = ptr::null_mut();
    napi_create_object(env, &mut v);
    v
}

pub unsafe fn array(env: Env, items: &[Value]) -> Value {
    let mut v = ptr::null_mut();
    napi_create_array_with_length(env, items.len(), &mut v);
    for (i, item) in items.iter().enumerate() {
        napi_set_element(env, v, i as u32, *item);
    }
    v
}

pub unsafe fn set(env: Env, obj: Value, name: &str, v: Value) {
    if let Ok(n) = CString::new(name) {
        napi_set_named_property(env, obj, n.as_ptr(), v);
    }
}

// ---- reading values --------------------------------------------------------

/// A property, or `None` when it is absent, `undefined` or `null`.
///
/// `null` counts as absent so `{stdin: null}` means the same as leaving it out;
/// a caller building an options object from other data should not have to strip
/// its own nulls to get the defaults.
pub unsafe fn get(env: Env, obj: Value, name: &str) -> Option<Value> {
    let n = CString::new(name).ok()?;
    let mut v = ptr::null_mut();
    if napi_get_named_property(env, obj, n.as_ptr(), &mut v) != 0 {
        return None;
    }
    let mut t: c_int = 0;
    napi_typeof(env, v, &mut t);
    if t == TYPE_UNDEFINED || t == TYPE_NULL {
        None
    } else {
        Some(v)
    }
}

pub unsafe fn is_undefined(env: Env, v: Value) -> bool {
    let mut t: c_int = 0;
    napi_typeof(env, v, &mut t);
    t == TYPE_UNDEFINED || t == TYPE_NULL
}

/// A JS string as UTF-8, or `None` if it is not a string. Never coerces: a
/// number where source was expected is a caller bug worth a TypeError, not a
/// program named `42`.
pub unsafe fn as_string(env: Env, v: Value) -> Option<String> {
    let mut len = 0usize;
    if napi_get_value_string_utf8(env, v, ptr::null_mut(), 0, &mut len) != 0 {
        return None;
    }
    let mut buf = vec![0u8; len + 1];
    let mut written = 0usize;
    if napi_get_value_string_utf8(env, v, buf.as_mut_ptr() as *mut c_char, len + 1, &mut written)
        != 0
    {
        return None;
    }
    buf.truncate(written);
    String::from_utf8(buf).ok()
}

pub unsafe fn as_bool(env: Env, v: Value) -> Option<bool> {
    let mut b = false;
    if napi_get_value_bool(env, v, &mut b) != 0 {
        return None;
    }
    Some(b)
}

pub unsafe fn as_f64(env: Env, v: Value) -> Option<f64> {
    let mut d = 0.0;
    if napi_get_value_double(env, v, &mut d) != 0 {
        return None;
    }
    Some(d)
}

/// Bytes from a string (UTF-8) or from any `Uint8Array`, `Buffer` included —
/// `Buffer` *is* a `Uint8Array`, so one branch covers both and there is no way
/// for the two to be handled differently.
///
/// A wider typed array — `Uint16Array`, `Float64Array` — is `None`, which the
/// caller turns into a `TypeError`, and that refusal is the whole point of the
/// `ty` check. `napi_get_typedarray_info` reports an ELEMENT COUNT, not a byte
/// count, so reading `len` bytes out of a `Uint16Array` hands the program the
/// first half of its data and calls it the whole thing — a silently wrong
/// answer, of exactly the kind this project exists not to give. The bug is in
/// the caller's types, so it belongs on the `throw` channel with the other
/// caller type errors; it is not a refusal and must not be routable.
pub unsafe fn as_bytes(env: Env, v: Value) -> Option<Vec<u8>> {
    let mut ta = false;
    napi_is_typedarray(env, v, &mut ta);
    if ta {
        let mut ty: c_int = 0;
        let mut len = 0usize;
        let mut data: *mut c_void = ptr::null_mut();
        let mut buf = ptr::null_mut();
        let mut off = 0usize;
        if napi_get_typedarray_info(env, v, &mut ty, &mut len, &mut data, &mut buf, &mut off) != 0 {
            return None;
        }
        if ty != TA_INT8 && ty != TA_UINT8 && ty != TA_UINT8_CLAMPED {
            return None;
        }
        if data.is_null() || len == 0 {
            return Some(Vec::new());
        }
        return Some(std::slice::from_raw_parts(data as *const u8, len).to_vec());
    }
    as_string(env, v).map(|s| s.into_bytes())
}

pub unsafe fn as_array(env: Env, v: Value) -> Option<Vec<Value>> {
    let mut is = false;
    napi_is_array(env, v, &mut is);
    if !is {
        return None;
    }
    let mut n: u32 = 0;
    napi_get_array_length(env, v, &mut n);
    let mut out = Vec::with_capacity(n as usize);
    for i in 0..n {
        let mut e = ptr::null_mut();
        napi_get_element(env, v, i, &mut e);
        out.push(e);
    }
    Some(out)
}

// ---- calls -----------------------------------------------------------------

/// Exactly `N` arguments, missing ones as `undefined`.
///
/// Prefilled rather than trusting node to fill the tail: the contract is
/// documented, but a null `napi_value` reaching `napi_typeof` is a segfault in
/// the host process, and that is too expensive a way to find out.
pub unsafe fn args<const N: usize>(env: Env, info: CbInfo) -> [Value; N] {
    let u = undefined(env);
    let mut argv = [u; N];
    let mut argc = N;
    napi_get_cb_info(
        env,
        info,
        &mut argc,
        argv.as_mut_ptr(),
        ptr::null_mut(),
        ptr::null_mut(),
    );
    for slot in argv.iter_mut().skip(argc) {
        *slot = u;
    }
    argv
}

/// Is a JS exception already queued in this environment?
///
/// Reading an option can run the caller's own code — a getter, a `Proxy` trap —
/// and that code may throw. When it does, every later Node-API call is a no-op
/// returning `napi_pending_exception`, so a binding that only looks at values
/// sees an absent option and carries on into the run. **Ask this before doing
/// anything irreversible**: the program would execute, node would throw the
/// caller's error over the top of the answer, and a harness that caught it and
/// fell onward would run the same program a second time. That is the one
/// failure the commit barrier exists to make impossible.
pub unsafe fn exception_pending(env: Env) -> bool {
    let mut p = false;
    if napi_is_exception_pending(env, &mut p) != 0 {
        // The call itself only fails on a dead env; treat that as "do not run".
        return true;
    }
    p
}

/// Queue a `TypeError` and answer `undefined`. Reserved for a caller who passed
/// the wrong *type* — never for a refusal, which is a value.
pub unsafe fn type_error(env: Env, msg: &str) -> Value {
    if let Ok(m) = CString::new(msg) {
        napi_throw_type_error(env, ptr::null(), m.as_ptr());
    }
    undefined(env)
}

/// Attach one function to `exports`.
pub unsafe fn export(env: Env, exports: Value, name: &str, cb: Callback) {
    let mut f = ptr::null_mut();
    let n = match CString::new(name) {
        Ok(n) => n,
        Err(_) => return,
    };
    napi_create_function(env, n.as_ptr(), name.len(), cb, ptr::null_mut(), &mut f);
    napi_set_named_property(env, exports, n.as_ptr(), f);
}
