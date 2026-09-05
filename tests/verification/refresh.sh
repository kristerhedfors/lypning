#!/bin/sh
# Regenerate every tests/verification/expected/<contract>-<tool>.txt from ONE run.
#   sh tests/verification/refresh.sh /tmp/lypning-verify-run
# Run it from a worktree with its own LYPNING_HOME (docs/VERIFICATION.md §0, §C8),
# then diff the scratch dir against tests/verification/expected/ and commit the
# files together with §0's header. No expected file is ever edited by hand.
set -u
RR=${1:?usage: refresh.sh <scratch-dir>}
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT" || exit 1
mkdir -p "$RR/bin"
printf '#!/bin/sh\nexec python3 -m lypning "$@"\n' > "$RR/bin/lypning"
chmod +x "$RR/bin/lypning"
export PATH="$RR/bin:$PATH" PYTHONPATH="$ROOT/src"
today=$(date +%Y-%m-%d)
sha=$(git rev-parse --short HEAD)
loaded=$(lypning status | awk '/^corpus/ {print $2}')
host=$(uname -sm)
for script in tests/verification/checks/*.sh; do
    name=$(basename "$script" .sh)
    tool=$(sed -n 's/^# tool: //p' "$script")
    {
        echo "run of record · $tool · $today · $sha · $loaded loaded · $host"
        sh "$script" 2>&1
    } | sed -E 's/ in [0-9]+\.[0-9]+s / in <s>s /; s/^(lypning[-a-z]* +(lib +)?host +[0-9]+ +[0-9]+ +)[0-9]+\.[0-9]+/\1<s>  /' \
      > "$RR/$name.txt"
    echo "$name: $(wc -l < "$RR/$name.txt" | tr -d ' ') lines"
done
echo "diff -r tests/verification/expected \"$RR\" — then commit the files and §0's header as one change"
