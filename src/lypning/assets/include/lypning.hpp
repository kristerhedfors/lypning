/*
 * lypning.hpp — the C ABI as C++17 values. Header-only: no .cpp, no build step,
 * no dependency beyond the standard library and `lypning.h` beside it.
 *
 * ---------------------------------------------------------------------------
 * The one invariant this file exists to hold
 * ---------------------------------------------------------------------------
 *
 * A REFUSAL IS A VALUE. `Status::Unsupported` arrives inside the returned
 * `Result`, never as a thrown exception, and `Result::should_fall_onward()` is
 * an ordinary `bool` you branch on. A harness that wraps nothing in
 * `try`/`catch` still routes refusals correctly; the only way to get it wrong
 * is to ignore the field.
 *
 * That is not a stylistic preference. lypning refuses a large share of the
 * one-liners a coding agent types, by design — refusing is the cheap, always
 * safe half of the contract. Modelling the common, expected, *correct* outcome
 * as an exception would put it on the path a C++ programmer reserves for things
 * that went wrong, and the first thing a harness does with that path is log it
 * as a failure. That turns a speedup into a bug report.
 *
 * So the rule here is narrow and absolute: this header throws only for
 * PROGRAMMER ERROR — source that is not UTF-8, a handle the library could not
 * allocate, an ABI number this header does not describe. Those are bugs in the
 * calling program, unrecoverable at the call site, and all of them are
 * `lypning::error`, which derives from `std::logic_error` to say exactly that.
 * Nothing the Python program does ever throws: a traceback is an answer, a
 * refusal is a routing signal, and `Status::Busy` and `Status::Panic` both mean
 * "run it on CPython", not "abort" — which is exactly what
 * `should_fall_onward()` reports for all three, so the branch below covers them
 * without a second test.
 *
 *     lypning::Result r = lypning::Request(src).run();
 *     if (r.should_fall_onward())
 *         run_it_on_python3(src);          // your existing path, unchanged
 *     else
 *         use(r.stdout_bytes(), r.exit_code());
 *
 * ---------------------------------------------------------------------------
 * What this wrapper adds over the C header, and what it costs
 * ---------------------------------------------------------------------------
 *
 *   * RAII on all three handles. `Route`, `Request` and `Result` free
 *     themselves; there is no `_free` to forget and no leak on a throw.
 *     `Request` owns a handle and is therefore MOVE-ONLY — a copy would free
 *     the same pointer twice. `Route` and `Result` own no handle at all and so
 *     copy freely.
 *   * `Result` OWNS its bytes. The C accessors hand back pointers that die with
 *     the result handle, and what a harness always wants is to free the run and
 *     keep the output — cache it, log it, return it to a caller. So the four
 *     buffers are copied out once, at construction, and the handle is released
 *     before `run()` returns. That copy is the price of the type; against the
 *     process spawn it replaced, for one-liner output, it is nothing.
 *   * Program output is `std::string` used as a byte container, not as text. It
 *     is whatever the program printed, invalid UTF-8 included, and this header
 *     will not re-encode it — re-encoding would be a wrong answer of exactly
 *     the kind lypning exists to refuse.
 *
 * Exceptions ARE used, for the one case above; do not compile this with
 * `-fno-exceptions`. RTTI is not: nothing here does `dynamic_cast` or `typeid`,
 * so `-fno-rtti` is fine. C++17 is required, for `std::string_view`.
 *
 * Threading is the C library's rule unchanged: two threads may each run a
 * program, one thread may not run two at once and gets `Status::Busy` having
 * executed nothing.
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef LYPNING_HPP
#define LYPNING_HPP

#include "lypning.h"

#include <cstddef>
#include <cstdint>
#include <initializer_list>
#include <memory>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#if !defined(_MSVC_LANG) && __cplusplus < 201703L
#error "lypning.hpp requires C++17 (std::string_view)"
#endif

namespace lypning {

/* --- the one exception --------------------------------------------------- */

/// A bug in the calling program: source that is not UTF-8, a handle the library
/// could not allocate, an ABI it does not implement.
///
/// `std::logic_error`, not `std::runtime_error`, on purpose. Nothing a Python
/// program does can produce this — a refusal, a traceback, a panic inside the
/// interpreter are all values on `Result`. If you are catching this, the fix is
/// in your code; a retry will not help.
class error : public std::logic_error {
public:
    using std::logic_error::logic_error;
};

/* --- internals ----------------------------------------------------------- */

namespace impl {

/// Written out three times rather than as one function-pointer template: the
/// language linkage of `extern "C"` is part of a function's type on a strict
/// reading, and a wrapper whose portability depends on how a compiler reads
/// that is not worth the six saved lines.
struct route_deleter {
    void operator()(::lypning_route *p) const noexcept { ::lypning_route_free(p); }
};
struct request_deleter {
    void operator()(::lypning_request *p) const noexcept { ::lypning_request_free(p); }
};
struct result_deleter {
    void operator()(::lypning_result *p) const noexcept { ::lypning_result_free(p); }
};

/// Copy a C string the library owns. NULL becomes empty: every accessor in
/// `lypning.h` answers NULL for a NULL handle, and no caller of this wrapper
/// can hold one — so "absent" and "empty" would be the same value anyway, and
/// only one of them is safe to print.
inline std::string own(const char *s) { return s ? std::string(s) : std::string(); }

/// Copy `n` bytes out of the library's buffer while the handle is still alive.
/// Deliberately not a `string_view` — see the header comment.
inline std::string own_bytes(const std::uint8_t *p, std::size_t n) {
    return (p && n) ? std::string(reinterpret_cast<const char *>(p), n) : std::string();
}

/// The pointer to hand the C ABI for `s`, never NULL.
///
/// A `std::string_view` may carry a NULL `data()`: that is what a
/// default-constructed one holds, and what an unset field or a
/// `string_view(nullptr, 0)` from somebody's own container hands you. In C++ it
/// is an EMPTY value, and every empty `string_view` compares equal to every
/// other one. The C ABI cannot see it that way — a NULL means "no bytes I can
/// decode", so `lypning_request_new(NULL, 0)` answers NULL and every setter
/// answers -1, which would arrive here as `lypning::error`.
///
/// That would break the rule the top of this file states: this header throws
/// only for PROGRAMMER ERROR. An empty program, an empty `sys.argv` entry, an
/// empty filename are ordinary values, and a caller who wrote
/// `Request(std::string_view())` would get an exception claiming their source
/// is not UTF-8 — while `Request("")`, the same value, ran. One value must not
/// mean two things depending on which constructor made it, so the empty view
/// becomes a pointer to an empty string here and the two agree.
inline const char *bytes_of(std::string_view s) noexcept { return s.data() ? s.data() : ""; }

} // namespace impl

/* --- status -------------------------------------------------------------- */

/// Whose outcome the exit code is. Scoped, so `Status::Error` cannot decay into
/// a bool, and numbered by hand from the C values so the two cannot drift.
enum class Status : std::int32_t {
    /// The program ran. `exit_code()` is its own.
    Ok = LYPNING_OK,
    /// The program raised. `stderr_bytes()` is the traceback, `exit_code()` is
    /// 1. An answer, not a wrapper failure — it does not throw.
    Error = LYPNING_ERROR,
    /// **lypning refused.** Not a failure: nothing ran, nothing was written,
    /// run the program on CPython.
    Unsupported = LYPNING_UNSUPPORTED,
    /// This thread was already running a program. Nothing was executed.
    Busy = LYPNING_BUSY,
    /// The interpreter itself failed. Report it, then run on CPython.
    Panic = LYPNING_PANIC
};

static_assert(static_cast<std::int32_t>(Status::Unsupported) == LYPNING_UNSUPPORTED,
              "Status must keep the C ABI's numbering");

/// For logs and tables. Not for control flow: branch on `Status`, or better on
/// `Result::should_fall_onward()`.
inline const char *to_string(Status s) noexcept {
    switch (s) {
    case Status::Ok:
        return "ok";
    case Status::Error:
        return "error";
    case Status::Unsupported:
        return "unsupported";
    case Status::Busy:
        return "busy";
    case Status::Panic:
        return "panic";
    }
    return "unknown";
}

/* --- version ------------------------------------------------------------- */

inline std::uint32_t abi_version() noexcept { return ::lypning_abi_version(); }

/// The runtime version, e.g. "0.1.0" — the string `lypning --version` prints.
inline std::string version() { return impl::own(::lypning_version()); }

/// Throw unless the library we are linked against speaks the ABI this header
/// describes. Worth one call at startup if you `dlopen` instead of linking: a
/// mismatch there is a deployment bug, which is what `error` is for.
inline void require_abi() {
    if (::lypning_abi_version() != LYPNING_ABI_VERSION)
        throw error("liblypning implements a different ABI than lypning.hpp was compiled against");
}

/* --- routing ------------------------------------------------------------- */

/// The classifier's answer: one parse, no execution, no handle left over.
///
/// A value and not a wrapper class because the C handle exists only to own four
/// strings, and a harness that routes a program keeps the answer far longer
/// than it wants to keep a pointer.
struct Route {
    /// Exactly one of "lypning", "lypning-mp", "cpython".
    std::string engine;
    /// The construct that pushed it past lypning ("module", "async"), or "".
    std::string kind;
    /// Its detail ("import re"), or "".
    std::string detail;
    /// Every module the program imports, as the library enumerates them.
    /// Ordering is the library's business and not part of this wrapper's
    /// promise — a host that cares which import stopped it wants `kind` and
    /// `detail`, which name exactly one.
    std::vector<std::string> imports;

    /// Can this run in our own process? A prediction, not a promise: only
    /// running it can catch the rest, so `run()` may still refuse — and when it
    /// does, the `Result` is the truth and this was merely cheap.
    bool runs_in_process() const noexcept { return engine == "lypning"; }
};

/// Route `src`. Throws `error` if it is not UTF-8 — so is a program CPython
/// would reject, and guessing at the bytes would run something nobody wrote.
inline Route route(std::string_view src) {
    std::unique_ptr<::lypning_route, impl::route_deleter> h(
        ::lypning_route_new(impl::bytes_of(src), src.size()));
    if (!h)
        throw error("lypning::route: source is not UTF-8 (or the route could not be allocated)");

    Route r;
    r.engine = impl::own(::lypning_route_engine(h.get()));
    r.kind = impl::own(::lypning_route_kind(h.get()));
    r.detail = impl::own(::lypning_route_detail(h.get()));
    const std::size_t n = ::lypning_route_import_count(h.get());
    r.imports.reserve(n);
    for (std::size_t i = 0; i < n; ++i)
        r.imports.push_back(impl::own(::lypning_route_import(h.get(), i)));
    return r;
}

/* --- results ------------------------------------------------------------- */

class Request;

/// Everything one run produced, owning all of it.
///
/// Copyable, movable, and outlives the `Request` that made it — a harness
/// collects these into a vector and reports at the end of a batch.
class Result {
public:
    Status status() const noexcept { return status_; }

    /// What the `lypning` binary would have exited with: the program's own
    /// code, 1 for an uncaught exception, 90 for a refusal.
    int exit_code() const noexcept { return exit_code_; }

    /// The program's stdout, as BYTES. Empty after a refusal, by the commit
    /// barrier — that emptiness is what makes the retry on CPython safe.
    ///
    /// `stdout_bytes` and not `stdout` because `<cstdio>` makes `stdout` a
    /// macro, and a member by that name breaks every translation unit that
    /// includes the two headers in the wrong order.
    const std::string &stdout_bytes() const noexcept { return stdout_; }

    /// The program's stderr: its traceback, or after a refusal exactly the one
    /// `lypning: unsupported: <kind>: <detail>` line the binary would print.
    const std::string &stderr_bytes() const noexcept { return stderr_; }

    /// The refusal's two halves, so you can branch on the kind without parsing
    /// the line apart: "module" / "import re". Empty when it was not a refusal.
    const std::string &kind() const noexcept { return kind_; }
    const std::string &detail() const noexcept { return detail_; }

    /// Did the run pass the point where its effects stop being reversible?
    /// True for any run that finished, whether or not it touched a file. A refusal with `false` here is observably a
    /// no-op, which is the entire basis of the retry.
    bool committed() const noexcept { return committed_; }

    /// **The call to branch on.** True exactly when lypning refused and left
    /// nothing behind: run the program on CPython now, and report nothing to
    /// your user — this is the design working, not failing.
    bool should_fall_onward() const noexcept { return fall_onward_; }

    /// The program ran and said so. False for a refusal AND for a program that
    /// exited non-zero on purpose, so never use it to decide whether to fall
    /// onward; that is `should_fall_onward()`'s job and only its job.
    bool ok() const noexcept { return status_ == Status::Ok && exit_code_ == 0; }

private:
    friend class Request;

    /// Copies everything while the handle is alive. `run()` frees the handle
    /// immediately after, and every pointer read here dies with it.
    explicit Result(const ::lypning_result *h) {
        switch (::lypning_result_status(h)) {
        case LYPNING_OK:
            status_ = Status::Ok;
            break;
        case LYPNING_ERROR:
            status_ = Status::Error;
            break;
        case LYPNING_UNSUPPORTED:
            status_ = Status::Unsupported;
            break;
        case LYPNING_BUSY:
            status_ = Status::Busy;
            break;
        default:
            // A status this header does not know means a library newer than it.
            // `Panic` is the only honest reading of "we do not know what
            // happened" — and the routing decision does not depend on this
            // guess anyway: `fall_onward_` below is the library's own answer.
            status_ = Status::Panic;
            break;
        }
        exit_code_ = static_cast<int>(::lypning_result_exit_code(h));
        std::size_t n = 0;
        const std::uint8_t *p = ::lypning_result_stdout(h, &n);
        stdout_ = impl::own_bytes(p, n);
        n = 0;
        p = ::lypning_result_stderr(h, &n);
        stderr_ = impl::own_bytes(p, n);
        kind_ = impl::own(::lypning_result_kind(h));
        detail_ = impl::own(::lypning_result_detail(h));
        committed_ = ::lypning_result_committed(h) != 0;
        fall_onward_ = ::lypning_result_should_fall_onward(h) != 0;
    }

    Status status_ = Status::Panic;
    int exit_code_ = -1;
    std::string stdout_;
    std::string stderr_;
    std::string kind_;
    std::string detail_;
    bool committed_ = false;
    bool fall_onward_ = false;
};

/* --- requests ------------------------------------------------------------ */

/// A program and everything you decide about it, built by chaining.
///
/// Move-only: it owns a `lypning_request *` and a copy would free it twice.
/// Reusable — set a stdin, run, set another stdin, run again.
///
///     auto r = lypning::Request("print(sum(range(10)))")
///                  .filename("count.py")
///                  .arg("--verbose")
///                  .step_limit(10'000'000)
///                  .run();
class Request {
public:
    /// Throws `error` if `source` is not UTF-8, or if the handle could not be
    /// allocated. Neither can happen to a program that is merely outside the
    /// subset — that one runs, and comes back as a refusal.
    explicit Request(std::string_view source)
        : q_(::lypning_request_new(impl::bytes_of(source), source.size())) {
        if (!q_)
            throw error("lypning::Request: source is not UTF-8 (or it could not be allocated)");
    }

    /// `sys.argv[0]`. Unset gives CPython's `-c` shape, which is what a
    /// one-liner from a harness actually is.
    Request &filename(std::string_view name) {
        check(::lypning_request_set_filename(q_.get(), impl::bytes_of(name), name.size()), "filename");
        return *this;
    }

    /// Append one entry to `sys.argv[1:]`.
    Request &arg(std::string_view a) {
        check(::lypning_request_add_arg(q_.get(), impl::bytes_of(a), a.size()), "arg");
        return *this;
    }

    /// Append several, in order.
    Request &args(std::initializer_list<std::string_view> as) {
        for (std::string_view a : as)
            arg(a);
        return *this;
    }

    /// Append several from any range of things a `string_view` can be built
    /// from — a `vector<string>`, a slice of `argv`. Templated because this
    /// header may not take a dependency, not even on somebody's string type.
    template <class It>
    Request &args(It first, It last) {
        for (; first != last; ++first)
            arg(std::string_view(*first));
        return *this;
    }

    /// The program's stdin, as BYTES. Unset is an empty stream; the library
    /// never reads your fd 0.
    ///
    /// `stdin_bytes` and not `stdin` for the same reason as `Result`'s
    /// accessors: `<cstdio>` owns that identifier.
    Request &stdin_bytes(std::string_view data) {
        check(::lypning_request_set_stdin(q_.get(), impl::bytes_of(data), data.size()), "stdin");
        return *this;
    }

    /// May the program touch the filesystem? True by default.
    ///
    /// False makes every file operation a REFUSAL rather than a lie: the
    /// program is never told a file is missing, you are told lypning would not
    /// run this — and because a refusal is routable, you still decide whether
    /// it goes to CPython. The policy stays yours.
    Request &filesystem(bool allow) noexcept {
        ::lypning_request_set_filesystem(q_.get(), allow ? 1 : 0);
        return *this;
    }

    /// Refuse once the program has taken `steps` statements or loop iterations.
    /// 0 (the default) is no limit.
    ///
    /// SET THIS if you run programs a language model wrote. It buys back the
    /// one safety the process boundary gave you for free: a runaway `lypning`
    /// process can be killed, a runaway call on your own thread cannot, so
    /// `while True: pass` with no limit is a hang with no way out of it.
    /// Passing the limit is a refusal like any other — routable — so the
    /// program still gets its answer from CPython, under whatever timeout you
    /// already apply to spawning it.
    Request &step_limit(std::uint64_t steps) noexcept {
        ::lypning_request_set_step_limit(q_.get(), steps);
        return *this;
    }

    /// Refuse once captured output passes `bytes`. 0 (the default) is no limit.
    Request &output_limit(std::size_t bytes) noexcept {
        ::lypning_request_set_output_limit(q_.get(), bytes);
        return *this;
    }

    /// Run the program in THIS thread, capturing its output. Never spawns
    /// anything, and never throws for anything the program did.
    Result run() const {
        std::unique_ptr<::lypning_result, impl::result_deleter> h(::lypning_run(q_.get()));
        if (!h)
            throw error("lypning::Request::run: the library returned no result");
        return Result(h.get());
    }

private:
    /// The C setters answer -1 for arguments they cannot use: a NULL handle,
    /// which the constructor already excluded, or bytes that are not UTF-8.
    /// That leaves one cause, and it is the caller's.
    static void check(int rc, const char *what) {
        if (rc != 0)
            throw error(std::string("lypning::Request::") + what + ": not UTF-8");
    }

    std::unique_ptr<::lypning_request, impl::request_deleter> q_;
};

/* --- chaining other interpreters ----------------------------------------- */

/// The dispatcher's own predicate, for a harness that chains OTHER engines
/// too — "lypning-mp", or a sandboxed "cpython". True for exit 90, for a
/// MemoryError (a property of that engine's heap, never the program's answer),
/// and for a traceback reported with exit 0. Deliberately false for an ordinary
/// non-zero exit with a traceback: that is very often the program's own correct
/// answer, and re-running it would repeat its side effects.
///
/// Use it on the exit code and stderr of a process YOU spawned. For a `Result`
/// from `run()`, `should_fall_onward()` is the answer already.
inline bool fall_onward(int exit_code, std::string_view stderr_bytes) noexcept {
    return ::lypning_fall_onward(static_cast<std::int32_t>(exit_code), impl::bytes_of(stderr_bytes),
                                 stderr_bytes.size()) != 0;
}

} // namespace lypning

#endif /* LYPNING_HPP */
