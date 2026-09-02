#!/bin/sh
# Every host, over the same program set, into the same capture log.
#
# Every host is a binding over one C ABI (docs/EMBEDDING.md section 4 is the
# list), so they must agree on which programs the subset takes; running them one
# after the other over an identical directory is what makes a disagreement
# visible instead of theoretical. A host missing here is a host the study never
# checked, which is why this script runs all of them and skips none: build every
# driver first with `make -C study/hosts`.
#
# Each run happens in its own scratch cwd. These programs came from agents that
# were asked to write and list files, and an in-process run reaches the host's
# own working directory — CLAUDE.md invariant 4 applies to a library call
# exactly as it applies to a spawn, and with one fewer layer between the program
# and this repository.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
set=${1:-$root/study/data/hostset}
: "${LYPNING_LOG:?set LYPNING_LOG to the capture log this run should append to}"
export LYPNING_LOG
export LYPNING_STUDY_SESSION="${LYPNING_STUDY_SESSION:-lypning-prompting-study}"

copy_set() {
    d=$(mktemp -d)
    cp -r "$set" "$d/set"
    printf %s "$d"
}

# Each host gets its own COPY of the program set, and runs each program with
# that copy's entry directory as the working directory. A separate copy per
# host so the second cannot read back what the first wrote, and none of it
# inside the repository — CLAUDE.md invariant 4.
run_in_scratch() {
    d=$(copy_set)
    ( cd "$d" && "$@" "$d/set" )
    rm -rf "$d"
}

run_in_scratch "$here/run_c"
run_in_scratch "$here/run_cpp"
run_in_scratch "$here/run_rust/target/release/lypning-study-host"
run_in_scratch node "$here/run_node.js"
run_in_scratch "${STUDY_PYTHON:-python3}" "$here/run_py.py"
run_in_scratch "$here/run_go/run_go"
run_in_scratch "$here/run_swift/run_swift"
run_in_scratch luajit "$here/run_lua.lua"
