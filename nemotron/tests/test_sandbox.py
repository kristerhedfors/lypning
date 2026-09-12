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


# --------------------------------------------- the child's PYTHON* settings
#
# `-E` used to void every one of these. The first three pin that they now land;
# the rest pin that landing them moved nothing else about the oracle.

_SET_ORDER = "print(list({'alpha','beta','gamma','delta','epsilon','zeta','eta'}))"


def test_set_iteration_order_is_the_same_on_every_run():
    # PYTHONHASHSEED=0 only bites if the child is not told to ignore its
    # environment. A run that is a coin flip re-grades a stored completion.
    assert len({run_python(_SET_ORDER).stdout for _ in range(12)}) == 1


def test_hash_randomization_is_off_and_bytecode_is_not_written():
    r = run_python("import sys;print(sys.flags.hash_randomization,"
                   "sys.dont_write_bytecode)")
    assert r.stdout.strip() == "0 True"


def test_importing_a_setup_module_leaves_no_pycache_in_the_program_cwd():
    r = run_python("import helper, os;print(helper.V, sorted(os.listdir('.')))",
                   files={"helper.py": "V = 7\n"})
    assert r.stdout.strip() == "7 ['helper.py', 'solution.py']"


def test_a_lone_surrogate_on_stdout_is_still_written_not_raised():
    # Pinning the encoding must not also pin the *error handler*: plain
    # PYTHONIOENCODING=utf-8 makes sys.stdout.errors strict, so this program
    # stops exiting 0 and the interpreter we grade against is no longer the one
    # a plain `python3 solution.py` gives.
    r = run_python("import sys;sys.stdout.write('a\\udce9b\\n')")
    assert r.ok, r.brief()
    # The surrogate went out as the single byte 0xe9 and comes back through
    # _read_capped's errors='replace' as one U+FFFD.
    assert r.stdout == "a�b\n"


def test_stdout_error_handler_and_buffering_are_the_ambient_defaults():
    r = run_python("import sys;print(sys.stdout.errors, sys.stdout.write_through)")
    assert r.stdout.strip() == "surrogateescape False"


def test_text_encoding_does_not_follow_the_operator_locale(monkeypatch):
    monkeypatch.setenv("LANG", "en_US.iso88591")
    monkeypatch.setenv("LC_ALL", "en_US.iso88591")
    r = run_python("import codecs,sys,locale;print(' '.join("
                   "codecs.lookup(e).name for e in (sys.getfilesystemencoding(),"
                   "locale.getpreferredencoding(False), sys.stdout.encoding)))")
    assert r.stdout.strip() == "utf-8 utf-8 utf-8"


def test_no_parent_python_variable_reaches_the_child(monkeypatch):
    # This is the guarantee -E was providing as a belt over env=. The belt is
    # gone; the braces are _ENV_KEEP naming no PYTHON* variable, and until now
    # nothing pinned that.
    monkeypatch.setenv("PYTHONPATH", "/evil")
    monkeypatch.setenv("PYTHONWARNINGS", "error")
    monkeypatch.setenv("PYTHONSTARTUP", "/evil/startup.py")
    monkeypatch.setenv("PYTHONHASHSEED", "12345")
    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    r = run_python("import os,sys;print('/evil' in sys.path,"
                   "os.environ.get('PYTHONWARNINGS'),"
                   "os.environ.get('PYTHONSTARTUP'),"
                   "sys.stdout.write_through)")
    assert r.stdout.strip() == "False None None False"
    assert run_python(_SET_ORDER).stdout == run_python(_SET_ORDER).stdout


# ------------------------------------------ what is and is not in the workdir


def test_the_program_sees_its_own_files_and_its_own_script_and_nothing_else():
    r = run_python("import os;print(sorted(os.listdir('.')))",
                   files={"data.txt": "x"})
    assert r.stdout.strip() == "['data.txt', 'solution.py']"


def test_the_script_stays_next_to_the_files_the_case_set_up():
    # `python3 solution.py` puts the script in the directory it runs from, and
    # the model-facing contract says so. __file__-relative access must work.
    r = run_python(
        "import os;d=os.path.dirname(os.path.abspath(__file__));"
        "print(d==os.getcwd(), open(os.path.join(d,'data.txt')).read())",
        files={"data.txt": "payload"})
    assert r.stdout.strip() == "True payload"


def test_a_program_that_wipes_its_cwd_cannot_forge_its_own_stdout():
    src = ("import os\n"
           "for n in os.listdir('.'):\n"
           "    try: os.remove(n)\n"
           "    except OSError: pass\n"
           "open('.ntx-stdout','w').write('forged')\n"
           "print('spoken')\n")
    r = run_python(src)
    assert r.ok and r.stdout.strip() == "spoken"


def test_a_file_the_program_writes_is_listed_even_under_a_harness_name():
    r = run_python("import os;os.mkdir('sub');"
                   "open('.ntx-stdout','w').write('yy');"
                   "open(os.path.join('sub','solution.py'),'w').write('z')")
    assert r.ok and r.workdir_files == {".ntx-stdout": 2, "sub/solution.py": 1}


def test_no_capture_directory_is_left_behind():
    import glob
    import tempfile

    pattern = os.path.join(tempfile.gettempdir(), "ntx-cap-*")
    before = set(glob.glob(pattern))
    run_python("print('x')")
    assert set(glob.glob(pattern)) == before
