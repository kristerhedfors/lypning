// run_cpp.cpp — the C++ host, over the header-only RAII wrapper.
//
// Same walk as run_c.c and deliberately so: the point of five hosts over one
// ABI is that they agree, and two hosts that disagreed about which programs
// the subset takes would be reporting a binding bug rather than a coverage
// number. The only thing that differs here is who frees the handles.
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>
#include <dirent.h>
#include <unistd.h>

#include "lypning.hpp"
extern "C" {
#include "capture.h"
}

static std::string slurp(const std::string &path)
{
    std::ifstream in(path, std::ios::binary);
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

int main(int argc, char **argv)
{
    if (argc < 2) { std::fputs("usage: run_cpp <hostset-dir>\n", stderr); return 2; }
    std::vector<std::string> names;
    if (DIR *d = opendir(argv[1])) {
        while (struct dirent *e = readdir(d))
            if (e->d_name[0] != '.') names.push_back(e->d_name);
        closedir(d);
    } else {
        std::perror("opendir");
        return 1;
    }
    std::sort(names.begin(), names.end());

    long ran = 0, refused = 0, other = 0;
    for (const auto &name : names) {
        const std::string base = std::string(argv[1]) + "/" + name;
        const std::string prog = slurp(base + "/program.py");
        if (prog.empty()) continue;
        const std::string sin = slurp(base + "/stdin");
        std::vector<std::string> args;
        {
            std::istringstream as(slurp(base + "/args"));
            std::string line;
            while (std::getline(as, line)) if (!line.empty()) args.push_back(line);
        }

        lypning::Request q(prog);
        for (const auto &a : args) q.arg(a);
        q.stdin_bytes(sin);
        q.step_limit(200000000ULL);
        q.output_limit(1u << 20);

        // The program runs in THIS process; give it the entry directory, where
        // prepare.py put the fixtures it was written against.
        char home[4096];
        const bool have_home = getcwd(home, sizeof home) != nullptr;
        const bool moved = chdir(base.c_str()) == 0;

        const auto t0 = std::chrono::steady_clock::now();
        lypning::Result r = q.run();
        const auto t1 = std::chrono::steady_clock::now();
        if (moved && have_home && chdir(home) != 0) { /* best effort */ }
        const long ms = (long)std::chrono::duration_cast<std::chrono::milliseconds>(t1 - t0).count();

        if (r.status() == lypning::Status::Ok) ran++;
        else if (r.status() == lypning::Status::Unsupported) refused++;
        else other++;

        std::vector<char *> cargs;
        for (auto &a : args) cargs.push_back(&a[0]);
        lys_capture("cpp-embed", prog.data(), prog.size(),
                    cargs.empty() ? nullptr : cargs.data(), (int)cargs.size(),
                    r.exit_code(), ms);
    }
    std::printf("cpp-embed    %zu programs: %ld ran, %ld refused, %ld other\n",
                names.size(), ran, refused, other);
    return 0;
}
