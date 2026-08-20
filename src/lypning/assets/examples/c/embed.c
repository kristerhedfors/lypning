/*
 * embed.c — the C ABI, executable.
 *
 * The one invariant this file holds: **a refusal is a route, not an error**,
 * and it is asserted against the library that was just built rather than
 * described in a comment. The refusal path is the only part of lypning that
 * has ever broken silently — a parser change that turns `unsupported` into a
 * traceback still compiles, still links, still answers `--version` — so the
 * four properties a host branches on (status, exit code, empty stdout,
 * should_fall_onward) are assert()ed here, and this program exits non-zero the
 * moment one of them stops being true.
 *
 * It is also the file a harness author copies. So it does not stop at the
 * refusal: it implements the other half of the mixture, the fork/execvp onto
 * python3, because a demo that prints "would fall onward" and stops has
 * demonstrated the easy half. Six programs go in. Three are run here; one is
 * refused and answered by CPython; two are refused and this host declines to
 * route them onward, for two different reasons it prints. Declining is a
 * decision the ABI leaves to the host, and both of those are cases where
 * routing onward would be the mistake.
 *
 * If the fallback tier cannot be started at all, that is reported and this
 * program exits non-zero: a run in which a program went unanswered has not
 * demonstrated a mixture, whatever the assertions say.
 *
 * Build: make. Read the Makefile for which library it picked.
 *
 * SPDX-License-Identifier: MIT
 */

#define _POSIX_C_SOURCE 200809L

#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "lypning.h"

/* This example is a test before it is a demo, and NDEBUG deletes a test
 * without deleting a line of output — it would still print its answers and
 * still exit 0 with the contract broken. Refuse to be built that way. */
#ifdef NDEBUG
#error "embed.c asserts the refusal contract; building it with NDEBUG removes the assertions and leaves a demo that cannot fail"
#endif

/* ------------------------------------------------------------------------ */
/* A growable byte buffer, because a program's output is bytes with a length  */
/* and not a string — the one thing about it we know is that we did not write */
/* it.                                                                       */
/* ------------------------------------------------------------------------ */

typedef struct {
    unsigned char *b;
    size_t len;
    size_t cap;
} blob;

static void blob_push(blob *v, const unsigned char *p, size_t n)
{
    if (v->len + n + 1 > v->cap) {
        size_t cap = v->cap ? v->cap : 256;
        while (cap < v->len + n + 1) {
            cap *= 2;
        }
        unsigned char *q = realloc(v->b, cap);
        if (q == NULL) {
            fputs("embed: out of memory\n", stderr);
            exit(1);
        }
        v->b = q;
        v->cap = cap;
    }
    memcpy(v->b + v->len, p, n);
    v->len += n;
    /* NUL past the end so the buffer can also be printed, without ever letting
     * the NUL be mistaken for the length. */
    v->b[v->len] = '\0';
}

static void blob_free(blob *v)
{
    free(v->b);
    v->b = NULL;
    v->len = v->cap = 0;
}

/* ------------------------------------------------------------------------ */
/* Policy this host applies to every program                                 */
/* ------------------------------------------------------------------------ */

/*
 * A step limit on every request, not on the one program below that needs it.
 *
 * The header says to set this and means it: a process that will not stop can
 * be killed, a function call on your own thread cannot. These programs were
 * typed by a language model, and "this one looks like it terminates" is not a
 * property you can read off a one-liner — which is the whole reason the limit
 * is a harness-wide policy here rather than a per-program annotation. Passing
 * it is a refusal like any other, so nothing is lost by setting it: the
 * program still gets its answer from whatever engine you route it to.
 *
 * The number bounds work, not time, and is deliberately far above anything the
 * five real programs below need — the sixth is the one that finds it.
 */
#define STEP_LIMIT 200000u

/* ------------------------------------------------------------------------ */
/* The programs                                                              */
/* ------------------------------------------------------------------------ */

/* Shared by the program lypning runs and the program it refuses, so the
 * fallback below has to forward stdin faithfully or the two answers disagree
 * in a way you can see. */
#define SPEECH "the quick brown fox jumps over the lazy dog the fox\n"

/* One program, two policies. `filesystem` is the only difference between the
 * fourth entry and the fifth. */
#define OUT_FILE "lypning-embed-example.txt"
#define WRITER                                                                \
    "open(\"" OUT_FILE "\", \"w\").write(\"staged, committed at exit\\n\")\n"  \
    "print(\"wrote " OUT_FILE "\")\n"

enum { EXPECT_RAN, EXPECT_REFUSED };

typedef struct {
    const char *label;
    const char *src;
    /* sys.argv[1:], NULL-terminated. */
    const char *const *args;
    /* Bytes, with a length: a program's stdin may contain a NUL and often is
     * not text at all. NULL is an empty stream, never this process's fd 0. */
    const char *stdin_bytes;
    size_t stdin_len;
    /* Passed straight to lypning_request_set_filesystem. */
    int filesystem;
    /* NULL routes a refusal onward to CPython. Otherwise: why this host does
     * not, printed as written. Not a property of the refusal — the refusal is
     * always routable, and whether routing it is a good idea is the host's
     * call and nobody else's. */
    const char *decline;
    /* Asserted, so that a library change which starts accepting — or starts
     * refusing — one of these fails here instead of surprising a harness. */
    int expect;
    /* Asserted when set: refusing for a different reason than the one this
     * entry is here to show is the same kind of surprise as not refusing. */
    const char *expect_kind;
} program;

static const char *const SUM_ARGS[] = { "3", "4", "5", NULL };

static const program PROGRAMS[] = {
    {
        .label = "stdin -> transform -> stdout",
        .src = "import sys\n"
               "counts = {}\n"
               "for w in sys.stdin.read().split():\n"
               "    counts[w] = counts.get(w, 0) + 1\n"
               "for w in sorted(counts):\n"
               "    print(w, counts[w])\n",
        .stdin_bytes = SPEECH,
        .stdin_len = sizeof SPEECH - 1,
        .filesystem = 1,
        .expect = EXPECT_RAN,
    },
    {
        .label = "sys.argv[1:]",
        .src = "import sys\n"
               "print(sum(int(a) for a in sys.argv[1:]))\n",
        .args = SUM_ARGS,
        .filesystem = 1,
        .expect = EXPECT_RAN,
    },
    {
        .label = "outside the subset — the same stdin, answered by CPython",
        .src = "import re, sys\n"
               "print(len(re.findall(r\"[aeiou]\", sys.stdin.read())))\n",
        .stdin_bytes = SPEECH,
        .stdin_len = sizeof SPEECH - 1,
        .filesystem = 1,
        .expect = EXPECT_REFUSED,
        .expect_kind = "module",
    },
    {
        .label = "a program that writes a file",
        .src = WRITER,
        .filesystem = 1,
        .expect = EXPECT_RAN,
    },
    {
        .label = "the same program, filesystem denied",
        .src = WRITER,
        .filesystem = 0,
        /* Declined on purpose. Handing this to CPython would run on CPython
         * exactly the write this host just refused to allow — the sandbox would
         * be a speed bump. The refusal is routable; routing it here would be a
         * policy mistake, and the ABI leaves that mistake to us to not make. */
        .decline = "declined — this host denied the filesystem, and CPython "
                   "would not have been allowed either",
        .expect = EXPECT_REFUSED,
        .expect_kind = "sandbox",
    },
    {
        .label = "a program that does not stop",
        .src = "while True:\n"
               "    pass\n",
        .filesystem = 1,
        /* Also declined, and for a reason worth reading twice. The header is
         * right that a step refusal is routable: CPython under a spawn timeout
         * is exactly where an endless program belongs. This host has no such
         * timeout — cpython_run() below polls with none — so routing it would
         * move the hang from a call it could bound into a child it could not.
         * Routable is not the same as route it; a host that cannot survive the
         * next engine must say so rather than find out. */
        .decline = "declined — the fallback below polls with no timeout, so "
                   "handing an endless program to CPython would hang this "
                   "process instead of bounding it",
        .expect = EXPECT_REFUSED,
        .expect_kind = "steps",
    },
};

/* ------------------------------------------------------------------------ */
/* Step 3b: the fallback. This is the half of the mixture that is not lypning */
/* ------------------------------------------------------------------------ */

static size_t arg_count(const char *const *args)
{
    size_t n = 0;
    while (args != NULL && args[n] != NULL) {
        n++;
    }
    return n;
}

/* 1 to keep polling this fd, 0 when it is finished. */
static int drain(int fd, blob *v)
{
    unsigned char buf[4096];
    ssize_t k = read(fd, buf, sizeof buf);
    if (k < 0) {
        /* EAGAIN cannot happen on a blocking fd that just polled readable, but
         * treating it as end-of-file would silently truncate the answer, and a
         * truncated answer is the failure this whole project exists to avoid.
         * Cheaper to keep going. */
        return errno == EINTR || errno == EAGAIN;
    }
    if (k == 0) {
        return 0;
    }
    blob_push(v, buf, (size_t)k);
    return 1;
}

/*
 * Run the refused program on CPython, forwarding exactly what lypning was
 * given: the same source, the same sys.argv, the same stdin bytes. `python3
 * -c SRC a b` gives the program sys.argv == ['-c', 'a', 'b'], which is the
 * shape lypning_request_new produces when no filename is set — the two engines
 * see the same argv, which is what makes the second answer the same answer.
 *
 * Returns the child's exit code, or 128+n if it died of a signal.
 *
 * Returns -1, with *start_errno set, if the child could not be started —
 * INCLUDING the case where execvp itself failed, which the exit status alone
 * cannot tell you. A failed exec has to leave through some exit code, and 127
 * is the conventional one; but 127 is also a perfectly ordinary thing for a
 * program to exit with, so a harness that reads it off waitpid() reports "no
 * python3 on this machine" and "your program exited 127" as the same event.
 * The fix is the standard one: a close-on-exec pipe that carries errno back
 * only when exec did not happen. Silence on it means the exec took.
 */
static int cpython_run(const program *p, blob *out, blob *err, int *start_errno)
{
    *start_errno = 0;
    size_t nargs = arg_count(p->args);

    /* Built before the fork: between fork() and exec() only async-signal-safe
     * calls are legal and malloc is not one of them. It happens to be harmless
     * in a single-threaded program, which is exactly why it survives review and
     * then deadlocks in the harness that adds a thread later. */
    char **argv = calloc(nargs + 4, sizeof *argv);
    if (argv == NULL) {
        *start_errno = ENOMEM;
        return -1;
    }
    argv[0] = (char *)"python3";
    argv[1] = (char *)"-c";
    argv[2] = (char *)p->src;
    for (size_t i = 0; i < nargs; i++) {
        argv[3 + i] = (char *)p->args[i];
    }

    /* in, out, err, and the exec-status pipe. Opened together and closed
     * together so no error path has to remember a subset of them. */
    enum { P_IN, P_OUT, P_ERR, P_EXEC, P_N };
    int fds[P_N][2];
    size_t opened = 0;
    for (; opened < P_N; opened++) {
        if (pipe(fds[opened]) != 0) {
            break;
        }
    }
    if (opened < P_N) {
        int e = errno;
        for (size_t i = 0; i < opened; i++) {
            close(fds[i][0]);
            close(fds[i][1]);
        }
        free(argv);
        *start_errno = e;
        return -1;
    }

    /* The one fd whose closing is the message. */
    fcntl(fds[P_EXEC][1], F_SETFD, FD_CLOEXEC);

    pid_t pid = fork();
    if (pid < 0) {
        int e = errno;
        for (size_t i = 0; i < P_N; i++) {
            close(fds[i][0]);
            close(fds[i][1]);
        }
        free(argv);
        *start_errno = e;
        return -1;
    }
    if (pid == 0) {
        dup2(fds[P_IN][0], STDIN_FILENO);
        dup2(fds[P_OUT][1], STDOUT_FILENO);
        dup2(fds[P_ERR][1], STDERR_FILENO);
        for (size_t i = 0; i < P_N; i++) {
            if (fds[i][0] != STDIN_FILENO) {
                close(fds[i][0]);
            }
            if ((int)i != P_EXEC && fds[i][1] != STDOUT_FILENO
                && fds[i][1] != STDERR_FILENO) {
                close(fds[i][1]);
            }
        }
        /* An ignored SIGPIPE survives exec. main() ignores it for its own
         * sake; leaving it ignored here would hand the child a disposition it
         * did not ask for and quietly change how it behaves in a pipeline.
         * signal() is async-signal-safe; nothing else between here and exec is
         * allowed to be anything else. */
        signal(SIGPIPE, SIG_DFL);
        execvp("python3", argv);
        int e = errno;
        ssize_t ignored = write(fds[P_EXEC][1], &e, sizeof e);
        (void)ignored;
        /* 127 is the shell's "not found", and the header's reason for putting
         * the refusal at 90 rather than anywhere near it. The pipe above is
         * what makes this distinguishable from a program that exits 127. */
        _exit(127);
    }

    close(fds[P_IN][0]);
    close(fds[P_OUT][1]);
    close(fds[P_ERR][1]);
    close(fds[P_EXEC][1]);

    /* Resolved before anything else: the write end is close-on-exec, so this
     * read returns 0 the instant the exec succeeds and four bytes if it did
     * not. Either way it does not wait on the program. */
    int exec_errno = 0;
    ssize_t got = read(fds[P_EXEC][0], &exec_errno, sizeof exec_errno);
    close(fds[P_EXEC][0]);
    if (got == (ssize_t)sizeof exec_errno) {
        close(fds[P_IN][1]);
        close(fds[P_OUT][0]);
        close(fds[P_ERR][0]);
        int st = 0;
        while (waitpid(pid, &st, 0) < 0 && errno == EINTR) {
            /* reap the shell of a child that never became python3 */
        }
        free(argv);
        *start_errno = exec_errno;
        return -1;
    }

    int w = fds[P_IN][1], ro = fds[P_OUT][0], re = fds[P_ERR][0];
    size_t sent = 0;
    if (p->stdin_len == 0) {
        /* An empty stream is a closed one. A program that blocks on read()
         * forever because nobody closed the write end is the classic way to
         * hang a harness on its own success. */
        close(w);
        w = -1;
    }

    while (w >= 0 || ro >= 0 || re >= 0) {
        struct pollfd pf[3];
        int n = 0, iw = -1, io = -1, ie = -1;
        if (w >= 0) {
            pf[n].fd = w;
            pf[n].events = POLLOUT;
            iw = n++;
        }
        if (ro >= 0) {
            pf[n].fd = ro;
            pf[n].events = POLLIN;
            io = n++;
        }
        if (re >= 0) {
            pf[n].fd = re;
            pf[n].events = POLLIN;
            ie = n++;
        }
        /* All three at once, and not one after the other: a program that fills
         * the stdout pipe before it has finished reading stdin deadlocks
         * against a harness that writes all of stdin first. */
        if (poll(pf, (nfds_t)n, -1) < 0) {
            if (errno == EINTR) {
                continue;
            }
            break;
        }
        if (iw >= 0 && pf[iw].revents != 0) {
            ssize_t k = write(w, p->stdin_bytes + sent, p->stdin_len - sent);
            if (k > 0) {
                sent += (size_t)k;
            }
            /* A program is entitled to ignore its stdin, and the one above that
             * sums sys.argv does. That closes the pipe under us: with SIGPIPE
             * ignored in main() the write returns EPIPE and we simply stop
             * writing, rather than dying of the program's success. */
            if (k <= 0 || sent == p->stdin_len) {
                close(w);
                w = -1;
            }
        }
        if (io >= 0 && pf[io].revents != 0 && !drain(ro, out)) {
            close(ro);
            ro = -1;
        }
        if (ie >= 0 && pf[ie].revents != 0 && !drain(re, err)) {
            close(re);
            re = -1;
        }
    }

    /* Only the `break` above reaches here with anything still open, and it is
     * not a leak that matters — it is a hang. Leaving the read ends open means
     * a child blocked on a full stdout pipe stays blocked, and the waitpid()
     * below never returns. Closing them turns that into an EPIPE the child can
     * die of. */
    if (w >= 0) {
        close(w);
    }
    if (ro >= 0) {
        close(ro);
    }
    if (re >= 0) {
        close(re);
    }

    int st = 0;
    while (waitpid(pid, &st, 0) < 0 && errno == EINTR) {
        /* retry */
    }
    free(argv);
    return WIFEXITED(st) ? WEXITSTATUS(st) : 128 + WTERMSIG(st);
}

/* ------------------------------------------------------------------------ */
/* The contract                                                              */
/* ------------------------------------------------------------------------ */

/*
 * The refusal contract, in library terms. The CLI states it as a process
 * contract — exit 90, one line on stderr, nothing on stdout — and these are
 * the same three facts carried by values, plus the fourth that a host actually
 * branches on.
 *
 * A harness may rely on all four. That is only true if something checks, and
 * this is the something.
 */
static void assert_refusal_contract(const lypning_result *r)
{
    size_t out_len = (size_t)-1;
    const uint8_t *out = lypning_result_stdout(r, &out_len);
    size_t err_len = (size_t)-1;
    const uint8_t *err = lypning_result_stderr(r, &err_len);

    /* 1. It is a refusal, and refusal has its own status — not ERROR, which a
     *    host would report to its user as a failure. */
    assert(lypning_result_status(r) == LYPNING_UNSUPPORTED);

    /* 2. Exit 90, the same number the binary would have returned, so a host
     *    that shells out and a host that links get the same answer. */
    assert(lypning_result_exit_code(r) == LYPNING_UNSUPPORTED_EXIT);

    /* 3. Nothing on stdout. Non-NULL even so: a host that checks the pointer
     *    before the length must not read "no result" out of "printed nothing".
     *    This is the commit barrier, and it is why step 4 is safe. */
    assert(out != NULL);
    assert(out_len == 0);

    /* 4. The call to branch on says yes. */
    assert(lypning_result_should_fall_onward(r));

    /* Supporting facts, each of which one of the four rests on. */
    assert(!lypning_result_committed(r));      /* re-running repeats nothing */
    assert(lypning_result_kind(r)[0] != '\0'); /* branch on the kind, not the text */
    assert(err != NULL && err_len > 0);
    assert(err[err_len - 1] == '\n');
    size_t lines = 0;
    for (size_t i = 0; i < err_len; i++) {
        lines += err[i] == '\n';
    }
    assert(lines == 1); /* exactly one line, as on the binary's stderr */
}

/* ------------------------------------------------------------------------ */
/* Reporting                                                                 */
/* ------------------------------------------------------------------------ */

/* Bytes, printed a line at a time under a tag. A real harness hands these on
 * unchanged instead. */
static void show(const char *tag, const unsigned char *b, size_t n)
{
    if (n == 0) {
        printf("  %-9s (empty)\n", tag);
        return;
    }
    size_t i = 0;
    while (i < n) {
        size_t j = i;
        while (j < n && b[j] != '\n') {
            j++;
        }
        /* fwrite and not printf("%.*s"): a program's stdout is allowed to
         * contain a NUL, and every %s conversion stops at one. Printing bytes
         * with a length is the same rule the ABI itself follows, and a demo
         * that broke it here would be teaching the bug. */
        printf("  %-9s ", tag);
        fwrite(b + i, 1, j - i, stdout);
        putchar('\n');
        tag = "";
        i = (j < n) ? j + 1 : j;
    }
}

/* ------------------------------------------------------------------------ */
/* The three steps                                                           */
/* ------------------------------------------------------------------------ */

/* 0 when the program got an answer, 1 when it went unanswered because the
 * fallback tier could not be started. */
static int answer(const program *p)
{
    int unanswered = 0;

    printf("== %s\n", p->label);

    /* Step 1 — decide, without running anything. One parse. This is lypning's
     * own front end answering, not a guess over the program text, so a harness
     * can use it to skip a CPython spawn it was going to lose on anyway. */
    lypning_route *route = lypning_route_new(p->src, strlen(p->src));
    assert(route != NULL);
    /* Copied, not kept: every string the route hands out dies with the handle,
     * and the note printed after the run outlives it. */
    char routed[32];
    snprintf(routed, sizeof routed, "%s", lypning_route_engine(route));
    printf("  route     %s", routed);
    if (lypning_route_kind(route)[0] != '\0') {
        printf("  (%s: %s)", lypning_route_kind(route), lypning_route_detail(route));
    }
    printf("\n");
    for (size_t i = 0; i < lypning_route_import_count(route); i++) {
        printf("  import    %s\n", lypning_route_import(route, i));
    }
    lypning_route_free(route);

    /* Step 2 — execute, in this thread. No fork, no exec, no pipe: on a
     * program lypning accepts, this call is the entire cost of the run. */
    lypning_request *q = lypning_request_new(p->src, strlen(p->src));
    assert(q != NULL);
    size_t nargs = arg_count(p->args);
    for (size_t i = 0; i < nargs; i++) {
        assert(lypning_request_add_arg(q, p->args[i], strlen(p->args[i])) == 0);
    }
    if (p->stdin_bytes != NULL) {
        assert(lypning_request_set_stdin(q, p->stdin_bytes, p->stdin_len) == 0);
    }
    lypning_request_set_filesystem(q, p->filesystem);
    /* Every request, not just the one that needs it. See STEP_LIMIT. */
    lypning_request_set_step_limit(q, STEP_LIMIT);

    lypning_result *r = lypning_run(q);
    assert(r != NULL);

    /* The table says what this program is; a library that changed its mind
     * about it must say so here rather than in someone else's harness. */
    assert((lypning_result_status(r) == LYPNING_UNSUPPORTED)
           == (p->expect == EXPECT_REFUSED));
    assert(p->expect_kind == NULL
           || strcmp(lypning_result_kind(r), p->expect_kind) == 0);

    /* Step 3 — decide again, on what actually happened. This one call, and not
     * the status, not the exit code, and never the text of the stderr line. */
    if (lypning_result_should_fall_onward(r)) {
        assert_refusal_contract(r);

        printf("  refused   %s: %s\n", lypning_result_kind(r), lypning_result_detail(r));
        printf("  onward    %s\n",
               p->decline ? p->decline : "python3 -c ... (same argv, same stdin)");

        /* Two things the route could not have told the host, both worth saying
         * out loud because a harness author will meet them on day one. */
        if (strcmp(routed, "lypning") == 0) {
            printf("  note      routing said lypning; only running it could tell. One parse\n"
                   "            cannot see a policy the host set — that is what the run is for\n");
        } else if (p->decline == NULL && strcmp(routed, "cpython") != 0) {
            printf("  note      routing named %s; a chain with that tier in it would try it\n"
                   "            before CPython. This example has two engines, so: CPython\n",
                   routed);
        }

        if (p->decline == NULL) {
            blob out = { 0 }, err = { 0 };
            int start_errno = 0;
            int code = cpython_run(p, &out, &err, &start_errno);
            if (code < 0) {
                /* Not an assertion: a machine without python3 is a legitimate
                 * machine. It is still a program this run did not answer, and
                 * main() will not claim otherwise. */
                printf("  UNANSWERED  the fallback tier could not be started: %s\n",
                       strerror(start_errno));
                unanswered = 1;
            } else {
                printf("  exit      %d (cpython)\n", code);
                show("stdout", out.b, out.len);
                if (err.len > 0) {
                    show("stderr", err.b, err.len);
                }
                /* The dispatcher's own predicate, asked of the next engine's
                 * result. A harness chaining lypning -> lypning-mp -> CPython
                 * asks this at every link; here there is no link after CPython,
                 * so a yes is worth saying out loud rather than swallowing. */
                if (lypning_fall_onward(code, err.b, err.len)) {
                    printf("  note      that result asks to fall onward too, and there is "
                           "no engine after CPython\n");
                }
            }
            blob_free(&out);
            blob_free(&err);
        }
    } else {
        size_t n = 0;
        const uint8_t *out = lypning_result_stdout(r, &n);
        printf("  exit      %d (lypning, in-process)\n", lypning_result_exit_code(r));
        show("stdout", out, n);
        size_t en = 0;
        const uint8_t *err = lypning_result_stderr(r, &en);
        if (en > 0) {
            show("stderr", err, en);
        }
    }

    lypning_result_free(r);
    lypning_request_free(q);
    printf("\n");
    return unanswered;
}

int main(void)
{
    /* The argv program never reads stdin, so the pipe closes under our write.
     * Default SIGPIPE would kill this process because the program it was
     * running succeeded. The child restores the default before exec. */
    signal(SIGPIPE, SIG_IGN);

    /* Before any other assertion is worth making: the header this was compiled
     * against and the library it linked to must be the same ABI. A host that
     * dlopen()s rather than links has no linker to catch the mismatch for it,
     * which is why this number exists at all. */
    assert(lypning_abi_version() == LYPNING_ABI_VERSION);
    printf("lypning %s, ABI %u\n\n", lypning_version(), lypning_abi_version());

    int unanswered = 0;
    for (size_t i = 0; i < sizeof PROGRAMS / sizeof PROGRAMS[0]; i++) {
        unanswered += answer(&PROGRAMS[i]);
    }

    /* One of the programs above really did write this, into whatever directory
     * make was run from. Leaving it behind in a checkout is precisely the thing
     * the filesystem switch exists to let a host prevent. */
    if (remove(OUT_FILE) == 0) {
        printf("removed %s\n", OUT_FILE);
    }

    if (unanswered > 0) {
        printf("all assertions held, but %d program(s) went unanswered:\n"
               "the refusal contract is intact and the mixture is not, because\n"
               "half of it is not installed on this machine.\n",
               unanswered);
        return 1;
    }

    printf("all assertions held\n");
    return 0;
}
