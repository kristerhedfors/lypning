"""Build the verifier Space, and record the commit the bundle will pin.

The Space is the candidate execution boundary: a private Docker Space whose
build context is exactly the four reviewed files and a CPython base pinned by
the same digest the trainer Job runs from, which is what makes the `sys.version`
half of the identity handshake hold.

Two things here are deliberate and easy to get wrong.

The Dockerfile is NOT `training/worker/Dockerfile.verifier`. That one is the
Docker boundary's: it installs to `/runner/`, drops to uid 65534 and sets an
ENTRYPOINT. A pooled sandbox reads the standard system trees and nothing else at
the root, so the harness lives under `/usr/local/lib`; the Space needs a CMD
that keeps a health process alive, and no USER line. Both contracts are real and
they are not interchangeable.

And a Space name is not an immutable image: the Hub SDK offers no revision or
digest on `hf.co/spaces/<owner>/<name>`, so the string resolves to whatever the
Space last built. The 40-character commit printed here is the pin, and the
runner refuses to touch a host unless the Space's current head still equals it.
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path

from huggingface_hub import HfApi

#: Must equal `training/hf/launch.py`'s BASE_IMAGE. Read from there rather than
#: restated, so the two cannot drift into a handshake failure at job time.
LAUNCH = Path("training/hf/launch.py")
FOUR_FILES = ("sandbox.py", "child_exec.py", "container_worker.py")
BUILD_STATES_OK = ("RUNNING", "RUNNING_APP_STARTING", "RUNNING_BUILDING")
BUILD_STATES_BAD = ("BUILD_ERROR", "CONFIG_ERROR", "RUNTIME_ERROR", "DELETING")

#: A Space that is up but idle. Hugging Face sleeps one after inactivity, and a
#: re-run whose four files are unchanged uploads nothing, so nothing wakes it:
#: the build wait then sat at SLEEPING for its whole twenty minutes and failed
#: a FREE job, which skipped the billed one behind it (run 35440773281,
#: 2026-09-19). Sleeping is not a build failure and not a state to wait out —
#: it is a state to leave, once, and then wait normally.
BUILD_STATES_ASLEEP = ("SLEEPING", "PAUSED")


def base_image() -> str:
    text = LAUNCH.read_text(encoding="utf-8")
    m = re.search(r'^BASE_IMAGE = "([^"]+)"', text, re.M)
    if not m:
        raise SystemExit("could not read BASE_IMAGE from %s" % LAUNCH)
    return m.group(1)


def out(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    for key, value in kw.items():
        print("%-14s %s" % (key, value))
        if path:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write("%s=%s\n" % (key, value))


def main() -> int:
    token = os.environ["HF_TOKEN"]
    api = HfApi(token=token)
    owner = api.whoami()["name"]
    repo_id = "%s/%s" % (owner, os.environ.get("SPACE_REPO_NAME", "lypning-round02-verifier"))
    engine = Path(os.environ["LYPNING_HOME"]) / "bin" / "lypning-l"
    if not engine.is_file():
        raise SystemExit("not a file: %s — build the engine before the Space" % engine)

    staging = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "verifier-space"
    if staging.exists():
        for p in staging.iterdir():
            p.unlink()
    staging.mkdir(parents=True, exist_ok=True)

    for name in FOUR_FILES:
        src = Path("training/pipeline") / name
        (staging / name).write_bytes(src.read_bytes())
    (staging / "lypning-l").write_bytes(engine.read_bytes())

    (staging / "Dockerfile").write_text(
        "FROM %s\n"
        "COPY sandbox.py child_exec.py container_worker.py /usr/local/lib/lypning-verifier/\n"
        "COPY --chmod=755 lypning-l /usr/local/bin/lypning-l\n"
        'CMD ["python3", "-I", "/usr/local/lib/lypning-verifier/container_worker.py", '
        '"--health-server"]\n' % base_image(), encoding="utf-8")
    (staging / "README.md").write_text(
        "---\n"
        "title: lypning round-02 verifier\n"
        # No `emoji:` key: the Hub validates it against
        # /\p{Extended_Pictographic}/u, so a word like "fire" is a 400 on
        # upload_folder rather than a default. It is optional; omit it.
        "colorFrom: gray\n"
        "colorTo: gray\n"
        "sdk: docker\n"
        "app_port: 7860\n"
        "pinned: false\n"
        "---\n\n"
        "Candidate execution boundary for the lypning round-02 pilot. Built from\n"
        "exactly four reviewed files on a CPython base pinned by digest. Not a\n"
        "demo: it serves a health endpoint and the container_worker protocol.\n",
        encoding="utf-8")

    print("== staging context (%d files)" % len(list(staging.iterdir())))
    for p in sorted(staging.iterdir()):
        print("   %-22s %8d B" % (p.name, p.stat().st_size))

    print("== create or reuse %s (private)" % repo_id)
    api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="docker",
                    private=True, exist_ok=True)
    info = api.repo_info(repo_id, repo_type="space")
    if not info.private:
        raise SystemExit("refusing: %s exists and is not private" % repo_id)

    print("== upload the four files and the two Space files")
    commit = api.upload_folder(repo_id=repo_id, repo_type="space", folder_path=str(staging),
                               commit_message="round-02 verifier: four reviewed files on a pinned base")
    head = api.repo_info(repo_id, repo_type="space").sha
    print("   commit %s" % head)
    if not head or len(head) != 40:
        raise SystemExit("the Space head is not a 40-character commit: %r" % head)

    print("== wait for the build (a Space name is not an image; the commit is the pin)")
    deadline = time.time() + 20 * 60
    last = ""
    woken = False
    while time.time() < deadline:
        runtime = api.get_space_runtime(repo_id)
        stage = str(runtime.stage)
        if stage != last:
            print("   %s" % stage)
            last = stage
        if stage in BUILD_STATES_BAD:
            raise SystemExit("Space build failed in stage %s — see the Space's build log" % stage)
        if stage == "RUNNING":
            break
        if stage in BUILD_STATES_ASLEEP:
            if woken:
                raise SystemExit("Space returned to %s after a restart; it is not starting, "
                                 "and waiting longer will not change that" % stage)
            # Asked once, not every pass: a restart re-enters the build, and
            # asking again each loop would keep restarting the thing we are
            # waiting for.
            print("   asleep — requesting a restart, then waiting for the build")
            api.restart_space(repo_id)
            woken = True
        time.sleep(15)
    else:
        raise SystemExit("Space did not reach RUNNING within 20 minutes (last stage %s)%s"
                         % (last, "" if woken else "; it was never asleep, so this is a slow build"))

    # The head can move if the build triggered a follow-up commit; re-read it so
    # the pin is the commit that is actually serving.
    head = api.repo_info(repo_id, repo_type="space").sha
    out(space_repo=repo_id, space_rev=head, owner=owner)
    return 0


if __name__ == "__main__":
    sys.exit(main())
