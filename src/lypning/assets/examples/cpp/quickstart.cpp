/*
 * quickstart.cpp — the smallest correct host, and the file to copy: run one
 * program in this process, and hand a refusal to python3 unchanged.
 *   usage:      quickstart "<python source>" [args...]   (args become sys.argv[1:])
 *   build+run:  make -C src/lypning/assets/examples/cpp quickstart && src/lypning/assets/examples/cpp/quickstart "print(sum(range(10)))"
 * SPDX-License-Identifier: MIT
 */

#include "lypning.hpp"

#include <cstdio>
#include <fcntl.h>
#include <unistd.h>
#include <vector>

int main(int argc, char **argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: %s \"<python source>\" [args...]\n", argv[0]);
        return 2;
    }
    const lypning::Result r = lypning::Request(argv[1])
                                  .args(argv + 2, argv + argc)
                                  .step_limit(10000000) // a call in our own thread has no process to kill
                                  .run();
    if (r.should_fall_onward()) {
        // A refusal is not an error: lypning ran none of it and wrote nothing,
        // so CPython runs it once, on the same empty stdin lypning was given.
        std::fflush(stdout);
        char python3[] = "python3", dash_c[] = "-c";
        std::vector<char *> cargv{python3, dash_c};
        cargv.insert(cargv.end(), argv + 1, argv + argc);
        cargv.push_back(nullptr);
        dup2(open("/dev/null", O_RDONLY), STDIN_FILENO);
        execvp(python3, cargv.data());
        std::perror("quickstart: python3");
        return 127;
    }
    std::fwrite(r.stdout_bytes().data(), 1, r.stdout_bytes().size(), stdout);
    std::fwrite(r.stderr_bytes().data(), 1, r.stderr_bytes().size(), stderr);
    return r.exit_code();
}
