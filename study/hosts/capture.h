/*
 * capture.h — the one thing an embedding host has to do for itself.
 *
 * lypning's capture harness has two feeds and both watch for a PROCESS: the
 * shim on $PATH catches an exec of python3, and the PreToolUse hook catches a
 * Bash command that mentions one. A host that links liblypning and calls
 * lypning_run() spawns nothing, so it is invisible to both — five hosts can run
 * ten thousand programs and the corpus will not grow by one.
 *
 * So the host logs. The record below is the shim's own record shape, which is
 * what makes it merge: `lypning harvest` reads any `python_invocation` line
 * with a program in it, whatever wrote it, and ranks it as a `shim` sighting
 * because the program demonstrably RAN. The only field that differs is `shim`,
 * which names the host instead of an interpreter.
 *
 * Best-effort on every path, exactly like the shim: a log that cannot be opened
 * is a lost sighting and never a failed run.
 */
#ifndef LYPNING_STUDY_CAPTURE_H
#define LYPNING_STUDY_CAPTURE_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static void lys_json_str(FILE *fh, const char *s, size_t n)
{
    fputc('"', fh);
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)s[i];
        switch (c) {
        case '"':  fputs("\\\"", fh); break;
        case '\\': fputs("\\\\", fh); break;
        case '\n': fputs("\\n", fh);  break;
        case '\r': fputs("\\r", fh);  break;
        case '\t': fputs("\\t", fh);  break;
        default:
            if (c < 0x20 || c == 0x7f) fprintf(fh, "\\u%04x", c);
            else fputc(c, fh);
        }
    }
    fputc('"', fh);
}

static const char *lys_log_path(void)
{
    const char *p = getenv("LYPNING_LOG");
    if (p && *p) return p;
    return NULL;   /* the drivers always set it; no guessing here */
}

/* One `python_invocation` record. `argv` entries are the trailing arguments the
 * program was given, which is what harvest calls argv_tail. */
static void lys_capture(const char *host, const char *program, size_t plen,
                        char **args, int nargs, int exit_code, long wall_ms)
{
    const char *path = lys_log_path();
    if (!path) return;
    FILE *fh = fopen(path, "a");
    if (!fh) return;

    char ts[32];
    time_t now = time(NULL);
    struct tm g;
    if (gmtime_r(&now, &g)) strftime(ts, sizeof ts, "%Y-%m-%dT%H:%M:%SZ", &g);
    else strcpy(ts, "1970-01-01T00:00:00Z");

    const char *sess = getenv("LYPNING_STUDY_SESSION");

    fputs("{\"kind\":\"python_invocation\",\"ts\":", fh);
    lys_json_str(fh, ts, strlen(ts));
    fputs(",\"session\":", fh);
    if (sess && *sess) lys_json_str(fh, sess, strlen(sess));
    else fputs("null", fh);
    fputs(",\"shim\":", fh);
    lys_json_str(fh, host, strlen(host));
    fprintf(fh, ",\"pid\":%d,\"program\":", (int)getpid());
    lys_json_str(fh, program, plen);
    fputs(",\"module\":null,\"script\":null,\"argv_tail\":[", fh);
    for (int i = 0; i < nargs; i++) {
        if (i) fputc(',', fh);
        lys_json_str(fh, args[i], strlen(args[i]));
    }
    fprintf(fh, "],\"stdin_pipe\":true,\"stdin_kind\":\"bytes\","
                "\"exit_code\":%d,\"wall_ms\":%ld}\n", exit_code, wall_ms);
    fclose(fh);
}

#endif /* LYPNING_STUDY_CAPTURE_H */
