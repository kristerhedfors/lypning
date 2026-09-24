//! `time` — the clocks, `sleep`, and one UTC stamp: the `cap-time` capability.
//!
//! # What is served
//!
//! | name | answer | clock |
//! |---|---|---|
//! | `time()` / `time_ns()` | `float` seconds / `int` ns since the epoch | `CLOCK_REALTIME` |
//! | `monotonic()` / `monotonic_ns()` | `float` s / `int` ns | CPython's own: `CLOCK_UPTIME_RAW` on macOS (`mach_absolute_time`), `CLOCK_MONOTONIC` elsewhere |
//! | `perf_counter()` / `perf_counter_ns()` | the same clock as `monotonic` | CPython 3.13+ reads one clock for both |
//! | `sleep(s)` | `None`, after `s` seconds | — |
//! | `strftime(<literal>, gmtime())` | the current UTC time, `%Y %m %d %H %M %S %%` only | `CLOCK_REALTIME` |
//!
//! Every clock is read through `clock_gettime(2)`, declared here `extern "C"`
//! against the libc `std` already links — no crate (invariant 6). A float is
//! `ns as f64 / 1e9`, which is `_PyTime_AsSecondsDouble` for every value that
//! is not a whole second, and the same double for every value that is.
//!
//! # Why so little of `time` is here
//!
//! **Local time is absent, on purpose.** `localtime`, `ctime`, `asctime`,
//! `mktime`, a one-argument `strftime`, `timezone`, `tzname`: every one of them
//! answers from the host's `TZ` database and its DST rules, which this engine
//! does not carry and cannot read without a crate. Every one is a STATIC
//! refusal, decided in `route.rs` before the interpreter exists — the core's
//! walk refuses the names out of `route::MODULE_ATTRS`, and lypning-l's walk
//! refuses every SHAPE below.
//!
//! **There is no `struct_time`.** `gmtime()` is served only as the second
//! argument of a `strftime` whose format is a string LITERAL the walk has read,
//! so its value never reaches anything but [`strftime`]: printing it, indexing
//! it, `gmtime(0)`, `gmtime()` held in a name, `from time import gmtime` — all
//! refuse statically — and the walk learns every name `time` is imported as
//! BEFORE it judges any call (`route::time_prescan`), because text order is not
//! run order. At runtime it is a plain 9-tuple, which is why the runtime
//! refuses every `gmtime()` but the one the evaluator hands to `strftime` in
//! that same fused shape ([`fused_gmtime`]): a call the walk did not see still
//! cannot print a tuple where CPython prints a `struct_time`. [`strftime`]
//! re-checks every field it formats on top of that.
//!
//! **A function is only ever called.** `f = time.time`, `print(time.time)`,
//! `print(time)`: CPython prints `<built-in function time>` and
//! `<module 'time' (built-in)>`, which this engine would have to fake. The walk
//! refuses every non-callee use of a served name and every use of the module
//! as a value.
//!
//! # The sleep policy: a sleep the program may pay TWICE
//!
//! A refusal is not free for a program that has already slept. The chain
//! answers a runtime refusal by running the WHOLE program again on CPython,
//! so every second lypning-l slept before the refusal is a second the caller
//! waits twice — and the corpus's sleeps are polling loops (`for _ in
//! range(590): … time.sleep(1)`) where that is ten minutes. Whether a program
//! will refuse at runtime is not decidable in the walk. What IS decidable is
//! how long a served sleep can be, so the walk bounds that instead:
//!
//!   * the argument is a LITERAL — a number of at most one second (a negative
//!     one raises before it sleeps), `True`/`False`, `None`, or a `str`/`bytes`
//!     (a `TypeError` before it sleeps). A computed argument refuses;
//!   * the call is not inside a loop, a `def`, a `lambda` or a comprehension,
//!     so each call site runs at most once.
//!
//! So the most a later refusal can cost is one second per `sleep(...)` spelled
//! in the source. There is nothing for a long or repeated sleep to gain here
//! anyway: the spawn this engine saves is milliseconds against seconds.
//!
//! The runtime below still implements the full argument rule (the backstop
//! for a program entered through the C ABI's own walk): CPython's exception
//! types and words for a negative, a NaN or a non-number, and a refusal for a
//! value past the range CPython itself overflows on.
//!
//! **Output is buffered to exit** (`io.rs`, the commit barrier), so a
//! `print(..., flush=True)` before a sleep appears when the program ends, not
//! when it is printed. Grading cannot see that — CPython block-buffers a pipe
//! too — but a person watching a terminal would, and it is one more reason the
//! policy above keeps sleeps short.
//!
//! # An uncaught error CPython would hint at refuses
//!
//! Every program this capability admits went to CPython before it, and CPython
//! ends an uncaught `NameError`, `AttributeError` or unexpected-keyword
//! `TypeError` with a `Did you mean` search this engine does not run. Once
//! `import time` has RUN, the exit path refuses those as `name-hint`
//! (`err::forgot_import`), and `io::hold` keeps the run reversible to its end
//! so it can: more than 8 MiB of output, or an `os.rmdir` of a directory the
//! run did not make, refuses instead of committing. A program that fails
//! before its import runs is answered exactly as the core answers it.

use crate::args::Args;
use crate::err::{type_err, unsupported, value_err, LypningError, R};
use crate::value::{type_name, Int, Value};
use std::os::raw::{c_int, c_long};
use std::rc::Rc;

/// The names this capability serves — `route::MODULE_ATTRS`'s `time` row is
/// held to this list by [`tests::the_route_table_names_exactly_what_is_served`].
pub const SERVED: &[&str] = &[
    "gmtime",
    "monotonic",
    "monotonic_ns",
    "perf_counter",
    "perf_counter_ns",
    "sleep",
    "strftime",
    "time",
    "time_ns",
];

/// `struct timespec` as the LEGACY `clock_gettime` symbol lays it out: `long`
/// seconds and `long` nanoseconds. On every 64-bit target that is `time_t`;
/// on 32-bit musl the unsuffixed symbol is the 32-bit-`time_t` ABI, which this
/// declaration matches rather than the `__clock_gettime64` a C header would
/// redirect to.
#[repr(C)]
struct Timespec {
    tv_sec: c_long,
    tv_nsec: c_long,
}

extern "C" {
    fn clock_gettime(clk: c_int, tp: *mut Timespec) -> c_int;
}

const CLOCK_REALTIME: c_int = 0;
/// The clock CPython's `time.monotonic` reads: `mach_absolute_time` on macOS,
/// which is `CLOCK_UPTIME_RAW` (8), and `CLOCK_MONOTONIC` (1) on Linux.
#[cfg(target_os = "macos")]
const CLOCK_MONO: c_int = 8;
#[cfg(not(target_os = "macos"))]
const CLOCK_MONO: c_int = 1;

fn now_ns(clk: c_int) -> R<i64> {
    let mut ts = Timespec { tv_sec: 0, tv_nsec: 0 };
    // SAFETY: `ts` is a live, writable `timespec` of the layout declared above.
    if unsafe { clock_gettime(clk, &mut ts) } != 0 {
        return Err(refuse("the host clock could not be read"));
    }
    Ok((ts.tv_sec as i64) * 1_000_000_000 + ts.tv_nsec as i64)
}

pub fn refuse(what: &str) -> LypningError {
    unsupported("time", what)
}

/// `time.<name>` as a value — the callee half of a call, which is the only
/// half the walk lets through. Every other name refuses as `module-attr`.
pub fn module_attr(name: &str) -> R<Value> {
    match SERVED.iter().find(|n| **n == name) {
        Some(n) => Ok(Value::Bound(Rc::new(Value::Module("time")), n)),
        None => Err(unsupported("module-attr", &format!("time.{name}"))),
    }
}

fn secs(ns: i64) -> Value {
    Value::Float(ns as f64 / 1e9)
}

pub fn call(_it: &mut crate::eval::Interp, name: &str, args: &mut Args, kw: &[(Rc<str>, Value)]) -> R<Value> {
    if !kw.is_empty() {
        return Err(refuse(&format!("time.{name}() with a keyword argument")));
    }
    let arity = match name {
        "sleep" => 1,
        "strftime" => 2,
        _ => 0,
    };
    if args.len() != arity {
        // CPython's TypeError, whose words this engine does not write.
        return Err(refuse(&format!("time.{name}() with {} arguments", args.len())));
    }
    Ok(match name {
        "time" => secs(now_ns(CLOCK_REALTIME)?),
        "time_ns" => Value::Int(Int::S(now_ns(CLOCK_REALTIME)?)),
        "monotonic" | "perf_counter" => secs(now_ns(CLOCK_MONO)?),
        "monotonic_ns" | "perf_counter_ns" => Value::Int(Int::S(now_ns(CLOCK_MONO)?)),
        "sleep" => {
            let d = sleep_duration(&args[0])?;
            std::thread::sleep(d);
            Value::None
        }
        "gmtime" if BLESSED.with(|b| b.replace(false)) => {
            gmtime(now_ns(CLOCK_REALTIME)?.div_euclid(1_000_000_000))
        }
        "gmtime" => {
            return Err(refuse(
                "time.gmtime() outside time.strftime(<literal>, time.gmtime()): there is no \
                 struct_time here",
            ))
        }
        "strftime" => strftime(&args[0], &args[1])?,
        _ => return Err(unsupported("module-attr", &format!("time.{name}"))),
    })
}

thread_local! {
    /// Set for exactly the evaluation of the one `gmtime()` a served
    /// `strftime` is about to consume, and taken by that call. A `gmtime()`
    /// that finds it clear refuses.
    static BLESSED: std::cell::Cell<bool> = const { std::cell::Cell::new(false) };
}

/// Is this call `time.strftime(<fmt>, <name>.gmtime())` — the callee already
/// evaluated to the module's `strftime`, two plain positionals, the second a
/// no-argument `.gmtime()` on a bare NAME? The runtime's half of the walk's
/// fused-shape rule, and the reason a `gmtime()` the walk never saw — a `def`
/// above `import time`, a module handed out of a function — cannot hand a bare
/// 9-tuple to anything but `strftime`: every other `gmtime()` refuses in
/// [`call`]. The base must be a name because evaluating a name runs no code,
/// so nothing can call `gmtime` between the blessing and the call it blesses.
pub fn fused_gmtime(
    f: &Value,
    args: &[crate::ast::Expr],
    star: &[usize],
    kwargs: &[(Rc<str>, crate::ast::Expr)],
    dstar: &[crate::ast::Expr],
) -> bool {
    use crate::ast::Expr;
    matches!(f, Value::Bound(r, "strftime") if matches!(**r, Value::Module("time")))
        && args.len() == 2
        && star.is_empty()
        && kwargs.is_empty()
        && dstar.is_empty()
        && matches!(&args[1], Expr::Call { func, args: a, star: s, kwargs: k, dstar: d }
            if a.is_empty() && s.is_empty() && k.is_empty() && d.is_empty()
                && matches!(&**func, Expr::Attr(b, n) if n.as_ref() == "gmtime" && matches!(**b, Expr::Name(_))))
}

/// Evaluate the `gmtime()` [`fused_gmtime`] found, with the blessing set for
/// it alone and cleared whatever happens.
pub fn eval_blessed(it: &mut crate::eval::Interp, x: &crate::ast::Expr) -> R<Value> {
    BLESSED.with(|b| b.set(true));
    let r = it.eval(x);
    BLESSED.with(|b| b.set(false));
    r
}

/// CPython's `time.sleep` argument rule, in its order: the TYPE, then a NaN,
/// then the range (`OverflowError` — refused here, its words differ between an
/// int and a float and between platforms), then the sign. `-0.0` is not
/// negative; `-1e-10` is, because CPython rounds a timeout away from zero.
fn sleep_duration(v: &Value) -> R<std::time::Duration> {
    // Past this many seconds CPython overflows its own nanosecond clock type.
    const LIMIT: f64 = 9.0e9;
    let s = match v {
        Value::Bool(b) => *b as i64 as f64,
        Value::Int(i) => match i.small() {
            Some(n) => n as f64,
            None => return Err(refuse("time.sleep() of an integer past 64 bits")),
        },
        Value::Float(f) => *f,
        other => {
            return Err(type_err(format!(
                "'{}' object cannot be interpreted as an integer or float",
                type_name(other)
            )))
        }
    };
    if s.is_nan() {
        return Err(value_err("Invalid value NaN (not a number)"));
    }
    if !(s.abs() < LIMIT) {
        return Err(refuse("time.sleep() past the range of CPython's clock type"));
    }
    if s < 0.0 {
        return Err(value_err("sleep length must be non-negative"));
    }
    Ok(std::time::Duration::from_secs_f64(s))
}

/// Days since 1970-01-01 to `(year, month, day)` — Howard Hinnant's
/// `civil_from_days`, exact over the whole proleptic Gregorian calendar.
fn civil(days: i64) -> (i64, i64, i64) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (yoe + era * 400 + (m <= 2) as i64, m, d)
}

/// `gmtime(secs)` as the 9-tuple `struct_time` is a subclass of:
/// `(year, mon, mday, hour, min, sec, wday, yday, isdst)`, Monday = 0.
fn gmtime(secs: i64) -> Value {
    let days = secs.div_euclid(86_400);
    let s = secs.rem_euclid(86_400);
    let (y, m, d) = civil(days);
    let jan1 = days - (d - 1) - {
        // days from Jan 1 to the first of month `m`
        const CUM: [i64; 12] = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334];
        let leap = (y % 4 == 0 && y % 100 != 0) || y % 400 == 0;
        CUM[(m - 1) as usize] + (leap && m > 2) as i64
    };
    let yday = days - jan1 + 1;
    // 1970-01-01 was a Thursday, which is 3 with Monday = 0.
    let wday = (days + 3).rem_euclid(7);
    let f = [y, m, d, s / 3600, s / 60 % 60, s % 60, wday, yday, 0];
    Value::Tuple(Rc::new(f.iter().map(|&n| Value::Int(Int::S(n))).collect()))
}

/// `strftime(fmt, t)` over the served directives, `%Y %m %d %H %M %S %%`.
///
/// The walk admits only a string literal it has already checked and a
/// `gmtime()` call, so everything refused here is the backstop: a directive
/// outside the set (`%a`, `%Z` and `%j` read the locale or the zone, and a
/// bare `%` is platform-defined), a non-ASCII format, and a tuple whose fields
/// are not ones `gmtime` of a present-day clock produces. Inside those bounds
/// CPython's answer is the fields themselves, zero-padded — `%Y` to four
/// digits, which is why a year outside 1000..=9999 refuses: macOS pads it and
/// glibc does not.
fn strftime(fmt: &Value, t: &Value) -> R<Value> {
    let Value::Str(f) = fmt else {
        return Err(refuse("time.strftime() over a format that is not a str"));
    };
    if let Some(why) = format_block(f) {
        return Err(refuse(why));
    }
    let Value::Tuple(items) = t else {
        return Err(refuse("time.strftime() over a time value other than gmtime()"));
    };
    const RANGE: [(i64, i64); 9] =
        [(1000, 9999), (1, 12), (1, 31), (0, 23), (0, 59), (0, 61), (0, 6), (1, 366), (-1, 1)];
    if items.len() != 9 {
        return Err(refuse("time.strftime() over a time tuple that is not gmtime()'s"));
    }
    let mut v = [0i64; 9];
    for (i, x) in items.iter().enumerate() {
        match x {
            Value::Int(n) if n.small().is_some_and(|n| RANGE[i].0 <= n && n <= RANGE[i].1) => {
                v[i] = n.small().unwrap_or(0)
            }
            _ => return Err(refuse("time.strftime() over a time tuple that is not gmtime()'s")),
        }
    }
    let mut out = String::with_capacity(f.len() + 8);
    let mut it = f.chars();
    while let Some(c) = it.next() {
        if c != '%' {
            out.push(c);
            continue;
        }
        match it.next() {
            Some('%') => out.push('%'),
            Some('Y') => out.push_str(&format!("{:04}", v[0])),
            Some(d) => {
                let i = match d {
                    'm' => 1,
                    'd' => 2,
                    'H' => 3,
                    'M' => 4,
                    _ => 5,
                };
                out.push_str(&format!("{:02}", v[i]));
            }
            None => return Err(refuse("time.strftime() with a trailing '%'")),
        }
    }
    Ok(Value::Str(out.into()))
}

/// Why this `strftime` format is not served, or `None` if it is — ASCII, and
/// every `%` followed by one of `Y m d H M S %`. One function, so the WALK and
/// the runtime refuse with the same words.
pub fn format_block(f: &str) -> Option<&'static str> {
    if !f.is_ascii() {
        return Some("time.strftime() over a non-ASCII format");
    }
    let b = f.as_bytes();
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' {
            match b.get(i + 1) {
                Some(b'Y' | b'm' | b'd' | b'H' | b'M' | b'S' | b'%') => i += 1,
                Some(_) => {
                    return Some(
                        "time.strftime() with a directive outside %Y %m %d %H %M %S %% \
                         (the rest read the locale or the zone, or are platform-defined)",
                    )
                }
                None => return Some("time.strftime() with a trailing '%'"),
            }
        }
        i += 1;
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `route::MODULE_ATTRS` is read by the CORE, which has none of this file
    /// compiled in — so the variant that DOES have `module_attr` holds the two
    /// lists to each other.
    #[test]
    fn the_route_table_names_exactly_what_is_served() {
        let table = crate::route::MODULE_ATTRS
            .iter()
            .find(|(m, _)| *m == "time")
            .expect("route::MODULE_ATTRS has no time row")
            .1;
        for name in table {
            assert!(module_attr(name).is_ok(), "route claims time.{name} and time.rs refuses it");
        }
        for name in SERVED {
            assert!(table.contains(name), "time.rs serves time.{name} and route::MODULE_ATTRS omits it");
        }
        assert!(table.windows(2).all(|w| w[0] < w[1]), "route::MODULE_ATTRS time row is unsorted");
        assert!(SERVED.windows(2).all(|w| w[0] < w[1]), "time::SERVED is unsorted");
    }

    fn fields(secs: i64) -> Vec<i64> {
        match gmtime(secs) {
            Value::Tuple(t) => t.iter().map(|v| match v {
                Value::Int(i) => i.small().unwrap(),
                _ => unreachable!(),
            }).collect(),
            _ => unreachable!(),
        }
    }

    /// Every row is `tuple(time.gmtime(s))` from CPython 3.14.5.
    #[test]
    fn gmtime_is_cpythons_on_the_boundaries() {
        assert_eq!(fields(0), vec![1970, 1, 1, 0, 0, 0, 3, 1, 0]);
        assert_eq!(fields(951_782_400), vec![2000, 2, 29, 0, 0, 0, 1, 60, 0]);
        assert_eq!(fields(978_307_199), vec![2000, 12, 31, 23, 59, 59, 6, 366, 0]);
        assert_eq!(fields(1_709_251_199), vec![2024, 2, 29, 23, 59, 59, 3, 60, 0]);
        assert_eq!(fields(1_790_273_482), vec![2026, 9, 24, 18, 11, 22, 3, 267, 0]);
        assert_eq!(fields(4_107_542_400), vec![2100, 3, 1, 0, 0, 0, 0, 60, 0]);
    }

    #[test]
    fn the_format_rule() {
        assert!(format_block("%Y-%m-%dT%H:%M:%SZ").is_none());
        assert!(format_block("").is_none());
        assert!(format_block("%%Y").is_none());
        assert!(format_block("%").is_some());
        assert!(format_block("%a").is_some());
        assert!(format_block("\u{e9}%Y").is_some());
        let s = strftime(&Value::Str("%Y-%m-%d %H:%M:%S %%".into()), &gmtime(951_782_400)).unwrap();
        assert!(matches!(s, Value::Str(ref x) if &**x == "2000-02-29 00:00:00 %"));
    }

    /// The runtime backstop, with the walk bypassed: the interpreter alone
    /// must refuse every `gmtime()` but the fused one, whatever shape reached it.
    #[test]
    fn gmtime_refuses_at_runtime_outside_the_fused_shape() {
        let run = |src: &str| {
            let body = crate::parse::parse(src).unwrap();
            crate::eval::Interp::new().run(&body)
        };
        for src in [
            "import time\nx = time.gmtime()\n",
            "def f():\n    return time.gmtime()\nimport time\nf()\n",
            "import time\nm = [time]\nx = m[0].strftime('%Y', m[0].gmtime())\n",
            "import time\ng = time.gmtime\nx = time.strftime('%Y', g())\n",
        ] {
            let e = run(src).expect_err(src);
            assert!(e.is_unsupported(), "{src}: {e}");
        }
        run("import time\nx = time.strftime('%Y', time.gmtime())\n").unwrap();
        // The blessing is consumed: a second, unblessed call still refuses.
        let e = run("import time\nx = time.strftime('%Y', time.gmtime())\ny = time.gmtime()\n").unwrap_err();
        assert!(e.is_unsupported());
    }

    #[test]
    fn the_clocks_move_forward() {
        let a = now_ns(CLOCK_MONO).unwrap();
        let b = now_ns(CLOCK_MONO).unwrap();
        assert!(b >= a && a > 0);
        assert!(now_ns(CLOCK_REALTIME).unwrap() > 1_600_000_000_000_000_000);
    }
}
