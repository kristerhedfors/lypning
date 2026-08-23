/*
 * run_c.c — the C host. Runs every program in a hostset directory through
 * liblypning and logs each one to the capture log.
 *
 * usage: run_c <hostset-dir>
 *
 * A refusal is a route, not an error (docs/EMBEDDING.md §1): the program is
 * counted as refused, logged with the exit code the refusal carries, and the
 * run carries on. What this driver does NOT do is fall onward to CPython —
 * measuring what the subset takes is the point, and a host that quietly
 * answered from python3 would report a coverage it did not have.
 */
#define _POSIX_C_SOURCE 200809L
#include <dirent.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>

#include "lypning.h"
#include "capture.h"

static char *slurp(const char *path, size_t *len)
{
    FILE *fh = fopen(path, "rb");
    if (!fh) { if (len) *len = 0; return NULL; }
    fseek(fh, 0, SEEK_END);
    long n = ftell(fh);
    fseek(fh, 0, SEEK_SET);
    if (n < 0) { fclose(fh); if (len) *len = 0; return NULL; }
    char *b = malloc((size_t)n + 1);
    if (!b) { fclose(fh); exit(1); }
    size_t got = fread(b, 1, (size_t)n, fh);
    b[got] = '\0';
    fclose(fh);
    if (len) *len = got;
    return b;
}

static int cmp(const void *a, const void *b)
{
    return strcmp(*(const char *const *)a, *(const char *const *)b);
}

int main(int argc, char **argv)
{
    if (argc < 2) { fputs("usage: run_c <hostset-dir>\n", stderr); return 2; }
    DIR *d = opendir(argv[1]);
    if (!d) { perror("opendir"); return 1; }

    char **names = NULL; size_t n = 0, cap = 0;
    struct dirent *e;
    while ((e = readdir(d))) {
        if (e->d_name[0] == '.') continue;
        if (n == cap) { cap = cap ? cap * 2 : 64; names = realloc(names, cap * sizeof *names); }
        names[n++] = strdup(e->d_name);
    }
    closedir(d);
    qsort(names, n, sizeof *names, cmp);

    long ran = 0, refused = 0, other = 0;
    for (size_t i = 0; i < n; i++) {
        char p[4096];
        size_t plen = 0, slen = 0, alen = 0;
        snprintf(p, sizeof p, "%s/%s/program.py", argv[1], names[i]);
        char *prog = slurp(p, &plen);
        if (!prog) continue;
        snprintf(p, sizeof p, "%s/%s/stdin", argv[1], names[i]);
        char *sin = slurp(p, &slen);
        snprintf(p, sizeof p, "%s/%s/args", argv[1], names[i]);
        char *args = slurp(p, &alen);

        lypning_request *q = lypning_request_new(prog, plen);
        if (!q) { free(prog); free(sin); free(args); other++; continue; }
        char *argvec[16]; int nargs = 0;
        if (args) {
            char *save = NULL;
            for (char *tok = strtok_r(args, "\n", &save); tok && nargs < 16;
                 tok = strtok_r(NULL, "\n", &save)) {
                argvec[nargs++] = tok;
                lypning_request_add_arg(q, tok, strlen(tok));
            }
        }
        lypning_request_set_stdin(q, sin ? sin : "", slen);
        lypning_request_set_step_limit(q, 200000000ULL);
        lypning_request_set_output_limit(q, 1u << 20);

        /* The program runs in THIS process, so its working directory is ours.
         * Give it the entry directory, where prepare.py put the fixtures it was
         * written against, and put ours back afterwards. */
        char home[4096];
        if (!getcwd(home, sizeof home)) home[0] = '\0';
        snprintf(p, sizeof p, "%s/%s", argv[1], names[i]);
        int moved = (chdir(p) == 0);

        struct timespec t0, t1;
        clock_gettime(CLOCK_MONOTONIC, &t0);
        lypning_result *r = lypning_run(q);
        clock_gettime(CLOCK_MONOTONIC, &t1);
        if (moved && home[0]) { if (chdir(home) != 0) { /* best effort */ } }
        long ms = (long)((t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000);

        int status = r ? lypning_result_status(r) : -1;
        int code = r ? lypning_result_exit_code(r) : -1;
        if (status == LYPNING_OK) ran++;
        else if (status == LYPNING_UNSUPPORTED) refused++;
        else other++;

        lys_capture("c-embed", prog, plen, argvec, nargs, code, ms);

        if (r) lypning_result_free(r);
        lypning_request_free(q);
        free(prog); free(sin); free(args);
    }
    printf("c-embed      %zu programs: %ld ran, %ld refused, %ld other\n", n, ran, refused, other);
    return 0;
}
