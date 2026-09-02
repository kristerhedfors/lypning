/*
 * embed.cpp — the contract test for the C++ header: route, run in-process, and
 * on a refusal fall onward to CPython, with every refusal checked against the
 * properties a host branches on.
 *
 * This is the file that fails when the header stops being right. The file to
 * COPY is quickstart.cpp beside it: the same branch in forty lines, with
 * nothing else on the page.
 *
 * The one thing this example exists to show: THE REFUSAL PATH IS ORDINARY
 * CODE. There is no try/catch anywhere below, nothing is logged as an error,
 * and the entire decision is
 *
 *     if (r.should_fall_onward()) ... else ...
 *
 * A harness that gets that one line right gets everything: the programs
 * lypning accepts cost no process at all, and the ones it refuses cost exactly
 * what they cost today — one python3 spawn — because a refused run wrote
 * nothing, read nothing and touched nothing.
 *
 * The fallback here is deliberately the crude, honest one: posix_spawnp of
 * python3 with three pipes. That is what a host already has, and lypning is
 * only worth linking if it can be dropped in front of it unchanged.
 *
 * Build and run:  make run        (see the Makefile beside this file)
 */

#include "lypning.hpp"

#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <poll.h>
#include <spawn.h>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>

extern char **environ;

/* --- the contract, checked rather than described -------------------------- */

/*
 * This example is a test before it is a demo. The refusal path is the one part
 * of lypning that has ever broken SILENTLY: a change that turns a refusal into
 * a traceback still compiles, still links, still prints a table exactly like
 * the one below — the row would just say `lypning` answered it, and the whole
 * point of the file would be gone with nothing on screen to say so. So every
 * refusal this program produces is checked against the properties a host
 * branches on, and main() exits non-zero the moment one of them stops holding.
 *
 * A plain function and not assert(): -DNDEBUG deletes an assert without
 * deleting a line of output, and a test a compiler flag can switch off is not
 * one this file may rest on.
 */

static int broken = 0;

static void must(bool cond, const char *what) {
    if (!cond) {
        std::fprintf(stderr, "CONTRACT BROKEN: %s\n", what);
        ++broken;
    }
}

/// Nothing ran, nothing was written, exit 90, and exactly the one stderr line
/// the `lypning` binary would have printed — assembled here from the two fields
/// a host is meant to branch on instead of parsing that line apart.
static void check_refusal(const lypning::Result &r) {
    must(r.status() == lypning::Status::Unsupported, "a refusal must be Status::Unsupported");
    must(r.exit_code() == LYPNING_UNSUPPORTED_EXIT, "a refusal must exit 90");
    must(r.stdout_bytes().empty(), "a refusal must have written nothing to stdout");
    must(!r.committed(), "a refusal must have committed nothing");
    must(r.stderr_bytes() == "lypning: unsupported: " + r.kind() + ": " + r.detail() + "\n",
         "a refusal must be one `lypning: unsupported: <kind>: <detail>` line");
}

/* --- the program a harness wants run, and everything it decides about it --- */

struct Program {
    const char *source;
    std::vector<std::string> args;
    std::string stdin_bytes;
    /// Mirrors lypning::Request::filesystem — and, for the fallback, the host's
    /// own policy about spawning something that has no such restriction.
    bool filesystem = true;
    const char *note = "";
};

/* --- the fallback: the host's own python3, unchanged ---------------------- */

struct Spawned {
    int exit_code = -1;
    std::string out;
    std::string err;
    bool timed_out = false;
};

static long now_ms() {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return ts.tv_sec * 1000L + ts.tv_nsec / 1000000L;
}

/// Run `p` on CPython, the way a host with no lypning in it already does.
///
/// `python3` comes from $PYTHON or PATH here because an example may not assume
/// a layout. A real harness should use the interpreter path it already knows:
/// PATH may hold lypning's own python3 shim, and a fallback that lands back on
/// lypning is a fallback that has not fallen anywhere.
///
/// The timeout is not decoration. lypning's step limit turns a runaway program
/// into a refusal, and a refusal is routable — which hands the runaway straight
/// to this function. Whatever timeout the host already applies to spawning
/// python3 is what stops it; there is no other stop.
static Spawned run_on_cpython(const Program &p, int timeout_ms) {
    Spawned s;

    // Opened one at a time so a failure halfway can close what it opened: the
    // short-circuiting `pipe(a) || pipe(b) || pipe(c)` leaks the earlier pairs,
    // and a harness copying this function runs it once per program.
    int in_pipe[2] = {-1, -1}, out_pipe[2] = {-1, -1}, err_pipe[2] = {-1, -1};
    int *pairs[3] = {in_pipe, out_pipe, err_pipe};
    for (int i = 0; i < 3; ++i) {
        if (pipe(pairs[i]) == 0)
            continue;
        s.err = "pipe: ";
        s.err += std::strerror(errno);
        for (int j = 0; j < i; ++j) {
            close(pairs[j][0]);
            close(pairs[j][1]);
        }
        return s;
    }

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_adddup2(&fa, in_pipe[0], STDIN_FILENO);
    posix_spawn_file_actions_adddup2(&fa, out_pipe[1], STDOUT_FILENO);
    posix_spawn_file_actions_adddup2(&fa, err_pipe[1], STDERR_FILENO);
    posix_spawn_file_actions_addclose(&fa, in_pipe[1]);
    posix_spawn_file_actions_addclose(&fa, out_pipe[0]);
    posix_spawn_file_actions_addclose(&fa, err_pipe[0]);

    const char *python = std::getenv("PYTHON");
    if (!python || !*python)
        python = "python3";

    // `-c SRC arg...` is the same shape lypning::Request gives a program with
    // no filename set, so sys.argv matches on both sides.
    std::vector<std::string> argv_own{python, "-c", p.source};
    argv_own.insert(argv_own.end(), p.args.begin(), p.args.end());
    std::vector<char *> argv;
    argv.reserve(argv_own.size() + 1);
    for (std::string &a : argv_own)
        argv.push_back(&a[0]);
    argv.push_back(nullptr);

    pid_t pid = 0;
    int rc = posix_spawnp(&pid, python, &fa, nullptr, argv.data(), environ);
    posix_spawn_file_actions_destroy(&fa);
    close(in_pipe[0]);
    close(out_pipe[1]);
    close(err_pipe[1]);
    if (rc != 0) {
        close(in_pipe[1]);
        close(out_pipe[0]);
        close(err_pipe[0]);
        s.err = std::string("posix_spawnp: ") + std::strerror(rc);
        return s;
    }

    // Written in one go before reading anything back. Safe for a one-liner's
    // stdin, which is all this example has; a host that feeds megabytes must
    // poll this fd for writability alongside the two below, or the child fills
    // its stdout pipe while we are still filling its stdin and both stop.
    if (!p.stdin_bytes.empty()) {
        const char *b = p.stdin_bytes.data();
        size_t left = p.stdin_bytes.size();
        while (left) {
            ssize_t n = write(in_pipe[1], b, left);
            if (n <= 0)
                break;
            b += n;
            left -= static_cast<size_t>(n);
        }
    }
    close(in_pipe[1]);

    const long deadline = now_ms() + timeout_ms;
    struct pollfd fds[2] = {{out_pipe[0], POLLIN, 0}, {err_pipe[0], POLLIN, 0}};
    std::string *into[2] = {&s.out, &s.err};
    int open_fds = 2;
    while (open_fds > 0) {
        const long left = deadline - now_ms();
        if (left <= 0) {
            s.timed_out = true;
            kill(pid, SIGKILL);
            break;
        }
        if (poll(fds, 2, static_cast<int>(left)) < 0)
            break;
        for (int i = 0; i < 2; ++i) {
            if (fds[i].fd < 0 || !fds[i].revents)
                continue;
            char buf[4096];
            ssize_t n = read(fds[i].fd, buf, sizeof buf);
            if (n > 0) {
                into[i]->append(buf, static_cast<size_t>(n));
            } else {
                close(fds[i].fd);
                fds[i].fd = -1;
                --open_fds;
            }
        }
    }
    for (int i = 0; i < 2; ++i)
        if (fds[i].fd >= 0)
            close(fds[i].fd);

    int status = 0;
    waitpid(pid, &status, 0);
    if (WIFEXITED(status))
        s.exit_code = WEXITSTATUS(status);
    else if (WIFSIGNALED(status))
        s.exit_code = 128 + WTERMSIG(status);
    return s;
}

/* --- reporting ------------------------------------------------------------ */

struct Row {
    std::string program;
    std::string route;
    std::string answered_by;
    std::string why;
    int exit_code;
    std::string output;
};

/// One printable line, for the table only — the bytes themselves are never
/// modified. ASCII in, ASCII out: a program's output is arbitrary bytes, and a
/// multi-byte ellipsis in a column padded by printf(3) would push every row
/// after it out of line.
static std::string one_line(const std::string &s, size_t width, bool last = false) {
    std::string picked, cur;
    for (char c : s) {
        if (c == '\n' || c == '\r') {
            if (!cur.empty()) {
                picked = cur;
                cur.clear();
                if (!last)
                    break;
            }
            continue;
        }
        cur += (c >= 32 && c < 127) ? c : '.';
    }
    if (!cur.empty() && (last || picked.empty()))
        picked = cur;
    if (picked.size() > width)
        picked = picked.substr(0, width - 3) + "...";
    return picked;
}

int main() {
    // A version mismatch is a deployment bug, and the one thing in this file
    // that may throw. Everything about the programs below is a value.
    lypning::require_abi();

    const Program programs[] = {
        {"print(sum(range(10)))", {}, "", true, "plain arithmetic"},
        {"import sys;print(len(sys.stdin.read().split()))", {}, "one two three\nfour\n", true,
         "stdin, handed over as bytes"},
        {"import sys;print(sys.argv[1:])", {"a", "b c"}, "", true, "argv, no shell in between"},
        {"print(1/0)", {}, "", true, "the program's own failure"},
        {"import re;print(re.findall(r'\\d+','a1b22'))", {}, "", true, "outside the subset"},
        {"print(f\"{3*7=}\")", {}, "", true, "outside the subset"},
        {"print(open('/etc/hosts').read().strip())", {}, "", false, "filesystem denied"},
        {"while True: pass", {}, "", true, "runaway, stopped by the step limit"},
    };

    std::vector<Row> rows;
    int in_process = 0, fell_onward = 0, refused_by_policy = 0;

    for (const Program &p : programs) {
        // Costs one parse, runs nothing. Useful for the log and for deciding
        // whether a program is worth a spawn at all — but advisory: the refusal
        // below is the authority, so we always try lypning first.
        const lypning::Route route = lypning::route(p.source);

        const lypning::Result r = lypning::Request(p.source)
                                      .args(p.args.begin(), p.args.end())
                                      .stdin_bytes(p.stdin_bytes)
                                      .filesystem(p.filesystem)
                                      // A program a model wrote may not stop.
                                      // In our own thread there is no kill(2).
                                      .step_limit(50000000)
                                      .run();

        Row row;
        row.program = one_line(p.source, 42);
        row.route = route.engine;

        if (!r.should_fall_onward()) {
            // Ran here. Note that a traceback lands in this branch too: exit 1
            // with a stack trace is the program's own answer, and re-running it
            // on CPython would only repeat it (and any side effect with it).
            ++in_process;
            row.answered_by = "lypning";
            row.why = (r.status() == lypning::Status::Ok) ? p.note : "traceback";
            row.exit_code = r.exit_code();
            // A traceback's last line is the answer; its first is boilerplate.
            const bool raised = r.status() != lypning::Status::Ok;
            row.output = one_line(raised ? r.stderr_bytes() : r.stdout_bytes(), 34, raised);
        } else if (r.kind() == "sandbox") {
            // The one refusal a host should think about before routing it on.
            // We denied the filesystem; python3 has no such restriction, so
            // spawning it would hand the program exactly the access we just
            // took away. should_fall_onward() says the retry is SAFE — that
            // nothing was written and nothing consumed — not that it is
            // WANTED. The policy stays here, where it was set.
            check_refusal(r);
            ++refused_by_policy;
            row.answered_by = "denied";
            row.why = r.kind() + ": " + r.detail();
            row.exit_code = r.exit_code();
            row.output = "";
        } else {
            // The whole point. Nothing ran, nothing was written, so the host's
            // existing path runs the program exactly once, on CPython.
            check_refusal(r);
            ++fell_onward;
            const Spawned s = run_on_cpython(p, 2000);
            row.answered_by = "cpython";
            row.why = r.kind() + ": " + r.detail();
            row.exit_code = s.exit_code;
            row.output = s.timed_out ? "(killed at the host's 2s timeout)"
                                     : one_line(s.out.empty() ? s.err : s.out, 34);
        }
        rows.push_back(row);
        // `r` dies here. Everything it produced is already in `row` — Result
        // owns its bytes precisely so a harness can report at the end.
    }

    std::printf("lypning %s, ABI %u — %d programs, one process\n\n", lypning::version().c_str(),
                lypning::abi_version(), static_cast<int>(sizeof programs / sizeof programs[0]));
    std::printf("%-44s %-11s %-9s %5s  %s\n", "program", "route", "answer", "exit", "output");
    std::printf("%-44s %-11s %-9s %5s  %s\n", "-------", "-----", "------", "----", "------");
    for (const Row &r : rows)
        std::printf("%-44s %-11s %-9s %5d  %s\n", r.program.c_str(), r.route.c_str(),
                    r.answered_by.c_str(), r.exit_code, r.output.c_str());

    std::printf("\nwhy the answer came from where it did\n");
    for (const Row &r : rows)
        if (r.answered_by != "lypning")
            std::printf("  %-44s %s\n", r.program.c_str(), r.why.c_str());

    std::printf("\n%d answered in-process, %d fell onward to CPython, %d refused by this host's "
                "own policy, 0 reported to a user as an error.\n",
                in_process, fell_onward, refused_by_policy);
    if (broken) {
        std::printf("\n%d property of the refusal contract no longer holds (see stderr). "
                    "The table above is not evidence of anything.\n",
                    broken);
        return 1;
    }
    return 0;
}
