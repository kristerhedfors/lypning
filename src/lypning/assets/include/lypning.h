/*
 * lypning.h — embed the lypning Python-subset runtime in a C or C++ program.
 *
 * lypning runs the bottom slice of the Python a coding agent actually types —
 * the one-liners — on a from-scratch interpreter written in Rust, and REFUSES
 * everything else rather than guessing at it. Linked as a library it runs those
 * programs in your own process: no fork, no exec, no pipe, no serialisation.
 *
 * ---------------------------------------------------------------------------
 * The one thing you must get right
 * ---------------------------------------------------------------------------
 *
 * LYPNING_UNSUPPORTED IS NOT AN ERROR. It means the program is outside the
 * subset, that lypning ran none of it, and that you should now run it on
 * CPython. A harness that reports a refusal to its user as a failure has turned
 * a speedup into a bug. The whole design rests on this: lypning is allowed to
 * be small precisely because refusing is free and always safe.
 *
 * The complete minimal host. Nothing in it is optional, and there is nothing
 * else to it (assets/examples/c/quickstart.c is this, buildable):
 *
 *     lypning_request *q = lypning_request_new(src, len);
 *     if (q == NULL) {                        // not UTF-8: that is CPython's
 *         return run_it_on_python3(src);      // to reject, not ours to report
 *     }
 *     lypning_request_set_step_limit(q, 10000000);   // no process to kill
 *     lypning_result *r = lypning_run(q);
 *     if (lypning_result_should_fall_onward(r)) {
 *         run_it_on_python3(src);              // your existing path, unchanged
 *     } else {
 *         use(lypning_result_stdout(r, &n),    // bytes, with a length
 *             lypning_result_stderr(r, &m),
 *             lypning_result_exit_code(r));    // a traceback IS the answer: 1
 *     }
 *     lypning_result_free(r);
 *     lypning_request_free(q);
 *
 * What makes that safe is the commit barrier: a refused run has written no
 * output, touched no file, and consumed no input, so running the program again
 * elsewhere cannot repeat a side effect. lypning_result_committed() says whether
 * a run passed the point where that stops being true — a directory it created,
 * say, which cannot be staged the way a file write can — and
 * should_fall_onward() already folds that in, which is why it is the call to
 * branch on rather than the status.
 *
 * ---------------------------------------------------------------------------
 * Rules of the surface
 * ---------------------------------------------------------------------------
 *
 *   * Every handle is opaque and is freed with its own _free. Freeing NULL is
 *     fine, and every accessor tolerates a NULL handle: integers answer 0 (or
 *     -1 where the comment says so), every `const char *` answers "" and
 *     NEVER NULL, and the two byte buffers answer NULL with a length of 0.
 *     So a host that forgot a check reads an empty string, not a fault.
 *   * Returned pointers belong to the handle and die with it. Copy anything you
 *     need to outlive it.
 *   * Returned strings are NUL-terminated. Program OUTPUT is bytes with a
 *     length, because a program's stdout is whatever it printed.
 *   * Nothing here unwinds, throws, longjmps, or calls exit(). A bug inside the
 *     interpreter comes back as LYPNING_PANIC, not as your process dying.
 *   * A run is confined to one thread. Two threads may run two programs at
 *     once; one thread may not run two at once and gets LYPNING_BUSY.
 *   * The library never reads your stdin, and PROGRAM output never reaches your
 *     stdout or stderr — it is captured, always. One exception, and it is not
 *     the program's: an interpreter bug prints Rust's own panic message to fd 2
 *     before the unwind is caught. Silencing it means a process-global panic
 *     hook, which is not a library's to install over yours; set your own if the
 *     noise matters.
 *   * An allocation this runtime cannot satisfy aborts the process, because
 *     Rust's allocator failure handler does. Program-driven sizes are ceilinged
 *     and refused long before that (`x = "a" * 10**14` is a refusal, not an
 *     abort), so what remains is a bug — but it is not something a guard at
 *     this boundary can catch, and saying otherwise would be a promise.
 *
 * Link with -llypning (see `lypning lib --cflags --libs`).
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef LYPNING_H
#define LYPNING_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* The ABI this header describes. Compare against lypning_abi_version() at
 * startup if you dlopen() the library rather than linking it. */
#define LYPNING_ABI_VERSION 1u

/* Exit code of a refusal, everywhere in this project: the `lypning` binary
 * returns it, and lypning_result_exit_code() reports it. 90 is clear of 0/1/2
 * (python's own), 126/127 (shell) and 128+n (signals), so it can be branched on
 * without ambiguity. */
#define LYPNING_UNSUPPORTED_EXIT 90

/* The Rust spectrum: one crate, N sized variants; this library is the largest.
 * All strings static, never freed. Added in ABI 1 additively (no existing
 * symbol changed shape). */
const char *lypning_engine_self(void);       /* e.g. "lypning-l" */
int lypning_engine_count(void);
const char *lypning_engine_name(int i);      /* cheapest first; "" past the end */
int lypning_engine_is_rust(const char *name); /* 1 for a spectrum member */

/* lypning_result_status(). */
enum {
    /* The program ran. lypning_result_exit_code() is its own: 0, or whatever it
     * passed to sys.exit(). */
    LYPNING_OK = 0,
    /* The program raised. stderr holds the traceback; exit code 1. */
    LYPNING_ERROR = 1,
    /* lypning refused. NOT a failure — run it on CPython. Exit code 90, stdout
     * empty, stderr exactly one `lypning: unsupported: <kind>: <detail>` line. */
    LYPNING_UNSUPPORTED = 2,
    /* This thread is already running a program. Nothing was executed. */
    LYPNING_BUSY = 3,
    /* The interpreter itself failed. Please report it — and then run the
     * program on CPython, which lypning_result_should_fall_onward() will
     * already be telling you to do. */
    LYPNING_PANIC = 4
};

/* --- version ------------------------------------------------------------- */

uint32_t lypning_abi_version(void);
/* The runtime version, e.g. "0.1.0". Static storage, never NULL; do not free. */
const char *lypning_version(void);

/* --- routing: which interpreter should run this? ------------------------- */
/*
 * Costs one parse and no execution. This is lypning's own front end answering
 * the question, not a heuristic over the program text, so "lypning" here means
 * lypning can genuinely run it — the refusal is what catches the cases where
 * only running it can tell.
 *
 * Useful on its own: a harness can route, log why, or inspect the imports
 * before deciding whether the program is worth a CPython spawn at all.
 */

typedef struct lypning_route lypning_route;

/* NULL if `src` is not UTF-8 or is NULL itself. */
lypning_route *lypning_route_new(const char *src, size_t len);
/* A member of the Rust spectrum (see lypning_engine_name), "lypning-mp" or
 * "cpython". "" for a NULL handle. Do not compare against "lypning" by hand:
 * ask lypning_engine_is_rust(), or lypning_engine_self() for this library. */
const char *lypning_route_engine(const lypning_route *r);
/* The construct that pushed it past lypning ("module", "async", …), or "".
 * Also "" for a NULL handle. */
const char *lypning_route_kind(const lypning_route *r);
/* Its detail ("import re"), or "". Also "" for a NULL handle. */
const char *lypning_route_detail(const lypning_route *r);
/* 0 for a NULL handle. */
size_t lypning_route_import_count(const lypning_route *r);
/* The i'th import, or NULL when i is out of range. SORTED AND DEDUPLICATED, not
 * in source order: the question is which modules a program needs, which has no
 * order. For the one import that decided the tier, read lypning_route_detail.
 * NULL is the loop terminator and only that: a NULL handle answers "", like
 * every other string accessor, and has an import count of 0. */
const char *lypning_route_import(const lypning_route *r, size_t i);
void lypning_route_free(lypning_route *r);

/* --- a program, and what you decide about it ----------------------------- */

typedef struct lypning_request lypning_request;

/* `src` is UTF-8 Python source, `len` bytes. NULL if it is not UTF-8.
 *
 * A NULL here MUST BE ROUTED ONWARD, exactly like a refusal: lypning has run
 * none of it and has nothing to say about it, and whether the bytes are a
 * program at all is CPython's to decide with its own message. It is not an
 * error to report, and not a reason to stop. The snippet at the top of this
 * file shows the branch. */
lypning_request *lypning_request_new(const char *src, size_t len);
/* sys.argv[0]. Unset gives CPython's `-c` shape, which is what a one-liner is.
 * Returns 0 on success, -1 on bad arguments. */
int lypning_request_set_filename(lypning_request *q, const char *name, size_t len);
/* Append one entry to sys.argv[1:]. 0 on success, -1 on bad arguments: `q`
 * NULL, or `arg` not UTF-8 — which is routed onward like a NULL request, not
 * dropped, because CPython would have seen that argument and lypning did not. */
int lypning_request_add_arg(lypning_request *q, const char *arg, size_t len);
/* The program's stdin, as bytes. Unset is an empty stream — the library never
 * reads your fd 0. 0 on success, -1 on bad arguments. */
int lypning_request_set_stdin(lypning_request *q, const void *data, size_t len);
/* May the program touch the filesystem? Non-zero (the default) yes.
 *
 * Zero makes every file operation a REFUSAL rather than a lie: the program is
 * never told a file is missing, you are told lypning would not run this. Since
 * a refusal is routable, you decide whether it goes to CPython — which is where
 * the policy belongs, not here. */
void lypning_request_set_filesystem(lypning_request *q, int allow);
/* Refuse once the program has taken `steps` statements or loop iterations.
 * 0 (the default) is no limit.
 *
 * SET THIS if you run programs a language model wrote. A process that will not
 * stop can be killed; a function call in your own thread cannot, so
 * `while True: pass` with no limit is a hang with no way back. Passing the
 * limit is a refusal like any other — routable — so the program still gets its
 * answer from CPython, under whatever timeout you already apply to spawning it.
 *
 * The counter ticks on every statement AND on every iterator advance, so a
 * single builtin over a huge range is bounded too. It is not a wall-clock
 * timeout and does not pretend to be one: it bounds work, not time. */
void lypning_request_set_step_limit(lypning_request *q, uint64_t steps);

/* Refuse once captured output passes `bytes`. 0 (the default) is no limit. */
void lypning_request_set_output_limit(lypning_request *q, size_t bytes);
void lypning_request_free(lypning_request *q);

/* --- running ------------------------------------------------------------- */

typedef struct lypning_result lypning_result;

/* Run the program in THIS thread, capturing its output. Never spawns anything.
 * NULL only if `q` is NULL. */
lypning_result *lypning_run(const lypning_request *q);

/* One of the LYPNING_* status values above; -1 for a NULL result. */
int32_t lypning_result_status(const lypning_result *r);
/* What the `lypning` binary would have exited with: the program's own code, 1
 * for an uncaught exception, 90 for a refusal, -1 for a NULL result. */
int32_t lypning_result_exit_code(const lypning_result *r);
/* The program's stdout. Empty after a refusal, by the commit barrier. The
 * pointer is non-NULL even when the length is 0; NULL, with `*len` set to 0,
 * only for a NULL result. `len` may be NULL. */
const uint8_t *lypning_result_stdout(const lypning_result *r, size_t *len);
/* The program's stderr: its traceback, or after a refusal exactly the one
 * `lypning: unsupported: <kind>: <detail>` line the binary would have printed. */
const uint8_t *lypning_result_stderr(const lypning_result *r, size_t *len);
/* The refusal's two halves, so you can branch on the kind without parsing the
 * line apart: "module" / "import re". "" when the run was not a refusal, and
 * "" for a NULL result. */
const char *lypning_result_kind(const lypning_result *r);
const char *lypning_result_detail(const lypning_result *r);
/* Did the run pass the commit point — where staged output and staged file
 * writes are flushed and the run stops being reversible? True for any run that
 * finished, whether or not it touched a file; false for a refusal, which is
 * what makes the program safe to hand to CPython. Branch on
 * lypning_result_should_fall_onward(), which already folds this in. */
int lypning_result_committed(const lypning_result *r);
/* Should you run this program on CPython now?
 *
 * True for every outcome that is not the program's own answer and left nothing
 * behind: a refusal, a LYPNING_BUSY that executed nothing, and a LYPNING_PANIC
 * that reached no commit. All three mean the same thing to you — lypning did
 * not answer, and the program still needs one.
 *
 * Never true for LYPNING_OK or LYPNING_ERROR: those ARE the answer, and an
 * uncaught exception is as much of an answer as a printed line. This is the
 * call to branch on. */
int lypning_result_should_fall_onward(const lypning_result *r);
void lypning_result_free(lypning_result *r);

/* The dispatcher's own predicate, for a harness that chains OTHER interpreters
 * too (lypning-mp, or a sandboxed python3). True for exit 90, for a MemoryError
 * — a property of that engine's heap, never the program's answer — and for a
 * traceback reported with exit 0, which would hand you empty stdout and a
 * success status. Deliberately false for an ordinary non-zero exit with a
 * traceback: that is very often the program's own correct answer, and re-running
 * it would repeat its side effects. */
int lypning_fall_onward(int32_t exit_code, const void *stderr_bytes, size_t len);

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* LYPNING_H */
