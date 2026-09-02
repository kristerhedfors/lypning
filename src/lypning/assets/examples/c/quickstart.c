/*
 * quickstart.c - the smallest complete lypning host, and the file to copy:
 * run the program in this process, or fall onward to CPython. Build and run:
 *   make -C src/lypning/assets/examples/c quickstart && src/lypning/assets/examples/c/quickstart "print(sum(range(10)))"
 * Usage: quickstart "<python source>" [args...]   (args become sys.argv[1:])
 * SPDX-License-Identifier: MIT
 */
#define _POSIX_C_SOURCE 200809L
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include "lypning.h"

int main(int argc, char **argv)
{
    if (argc < 2) {
        fputs("usage: quickstart \"<python source>\" [args...]\n", stderr);
        return 2;
    }
    lypning_request *q = lypning_request_new(argv[1], strlen(argv[1]));
    int ours = q != NULL; /* NULL, or -1 below: bytes that are not UTF-8 are CPython's to answer, not ours to drop */
    for (int i = 2; ours && i < argc; i++)
        ours = lypning_request_add_arg(q, argv[i], strlen(argv[i])) == 0;
    lypning_result *r = NULL;
    if (ours) {
        lypning_request_set_step_limit(q, 10000000); /* a call in our own thread has no process to kill */
        r = lypning_run(q);
    }
    if (r == NULL || lypning_result_should_fall_onward(r)) {
        /* A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once. */
        char **py = malloc(((size_t)argc + 2) * sizeof *py);
        if (py == NULL)
            return 1;
        py[0] = "python3";
        py[1] = "-c";
        memcpy(py + 2, argv + 1, (size_t)argc * sizeof *py); /* argv[1..] and its terminating NULL */
        fflush(stdout);
        dup2(open("/dev/null", O_RDONLY), STDIN_FILENO);
        execvp("python3", py);
        perror("quickstart: python3");
        return 127;
    }
    size_t n;
    const uint8_t *b = lypning_result_stdout(r, &n);
    fwrite(b, 1, n, stdout);
    b = lypning_result_stderr(r, &n);
    fwrite(b, 1, n, stderr);
    int code = lypning_result_exit_code(r);
    lypning_result_free(r);
    lypning_request_free(q);
    return code;
}
