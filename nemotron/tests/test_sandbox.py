"""The net has to hold, or every number above it is decoration."""
from __future__ import annotations

import os

import pytest

from pipeline.sandbox import netns_available, run_python


def test_basic_stdout_and_exit():
    r = run_python("print('hi')")
    assert r.ok and r.stdout == "hi\n" and r.exit_code == 0


def test_stdin_and_argv():
    r = run_python("import sys;print(sys.stdin.read().strip(), sys.argv[1])",
                   stdin="in", argv=["arg"])
    assert r.stdout.strip() == "in arg"


def test_timeout_kills_and_is_reported():
    r = run_python("while True: pass", timeout_s=2)
    assert r.timed_out and r.exit_code is None and r.duration_s < 20


def test_timeout_kills_the_whole_process_group():
    # A program that forks and then hangs must not leave the child behind.
    src = ("import os,time\n"
           "if os.fork()==0:\n"
           "    time.sleep(300)\n"
           "time.sleep(300)\n")
    r = run_python(src, timeout_s=2)
    assert r.timed_out


def test_credentials_are_not_visible_to_generated_code():
    os.environ["NTX_TEST_SECRET"] = "leaked"
    try:
        r = run_python("import os;print(os.environ.get('NTX_TEST_SECRET'), "
                       "os.environ.get('HF_TOKEN'))")
    finally:
        del os.environ["NTX_TEST_SECRET"]
    assert r.stdout.strip() == "None None"


def test_memory_cap_is_enforced():
    r = run_python("x = bytearray(4 * 1024**3)", mem_mb=256)
    assert r.exit_code != 0


@pytest.mark.skipif(not netns_available(), reason="kernel will not give us a netns")
def test_network_is_unreachable():
    r = run_python(
        "import socket;s=socket.socket();s.settimeout(3);s.connect(('1.1.1.1',80));print('OUT')"
    )
    assert "OUT" not in r.stdout and r.exit_code != 0


def test_workdir_is_private_between_runs():
    a = run_python("open('marker','w').write('x')")
    b = run_python("import os;print(os.path.exists('marker'))")
    assert a.ok and b.stdout.strip() == "False"


def test_setup_file_escaping_the_workdir_is_refused():
    r = run_python("pass", files={"../escape.txt": "no"})
    assert r.harness_error and "escapes" in r.harness_error


def test_binary_setup_files_are_written_as_bytes():
    r = run_python("print(open('b.bin','rb').read().hex())",
                   files={"b.bin": {"base64": "AAH+/w=="}})
    assert r.stdout.strip() == "0001feff"


def test_harness_error_is_never_set_by_a_misbehaving_program():
    for src in ("raise SystemExit(9)", "import sys;sys.stderr.write('x'*10000)",
                "raise ValueError('boom')"):
        assert run_python(src, timeout_s=5).harness_error is None
