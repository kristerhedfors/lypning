"""Sized stdin reads share a byte cursor but count text characters, as CPython does."""
from __future__ import annotations

import subprocess
import sys

import pytest

from lypning import engines


@pytest.fixture(params=[engines.LYPNING, engines.LYPNING_L])
def binary(request):
    found = engines.find(request.param)
    if found is None:
        pytest.skip("build the Rust spectrum first")
    return str(found)


def run(binary, program, data, cwd):
    return subprocess.run([binary, "-c", program], input=data, capture_output=True, cwd=cwd, timeout=5)


@pytest.mark.parametrize("data", [b"", b"xyz\nq", "å😀z\nq".encode(), b"a\r\nb\rc\n"])
@pytest.mark.parametrize("size", ["0", "1", "2", "10", "-1", "-7", "None", "True", "False", "1000000"])
def test_read_size_counts_characters_and_keeps_the_remaining_stream(binary, tmp_path, data, size):
    program = "import sys\nprint(repr(sys.stdin.read(%s)))\nprint(repr(sys.stdin.read()))\nprint(repr(sys.stdin.read(1)))" % size
    got, want = run(binary, program, data, tmp_path), run(sys.executable, program, data, tmp_path)
    assert got.returncode == want.returncode == 0
    assert got.stdout == want.stdout
    assert got.stderr == b""


@pytest.mark.parametrize("body", [
    "print(repr(sys.stdin.read(1))); print(repr(sys.stdin.readline())); print(repr(sys.stdin.read()))",
    "print(repr(sys.stdin.read(1))); print(input()); print(repr(sys.stdin.read(1))); print(repr(sys.stdin.read()))",
    "print(repr(sys.stdin.read(1))); print(list(sys.stdin)); print(repr(sys.stdin.read(1)))",
    "print(repr(sys.stdin.readline())); print(repr(sys.stdin.read(2))); print(sys.stdin.readlines())",
])
def test_all_stream_views_advance_one_cursor(binary, tmp_path, body):
    program = "import sys\n" + body
    data = "å😀x\nyz\nlast".encode()
    got, want = run(binary, program, data, tmp_path), run(sys.executable, program, data, tmp_path)
    assert got.returncode == want.returncode == 0
    assert got.stdout == want.stdout


@pytest.mark.parametrize("argument", ["1.5", "'2'", "[]", "{}", "1, 2", "size=1", "bad=1"])
def test_bad_read_arguments_raise_before_consuming_input(binary, tmp_path, argument):
    program = ("import sys\ntry:\n sys.stdin.read(%s)\nexcept TypeError as e:\n print(str(e))\n"
               "print(repr(sys.stdin.read()))") % argument
    got, want = run(binary, program, b"unchanged", tmp_path), run(sys.executable, program, b"unchanged", tmp_path)
    assert got.returncode == want.returncode == 0
    assert got.stdout == want.stdout


def test_zero_read_does_not_wait_for_eof(binary, tmp_path):
    # Keep the pipe open without writing: read(0) must finish anyway.
    proc = subprocess.Popen([binary, "-c", "import sys; print(repr(sys.stdin.read(0)))"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=tmp_path)
    try:
        assert proc.wait(timeout=3) == 0
        assert proc.stdout.read() == b"''\n"
    finally:
        proc.kill() if proc.poll() is None else None
        proc.communicate()


@pytest.mark.parametrize("data", [b"\xffx", b"\xc3", b"a\xff"])
def test_invalid_utf8_refuses_without_committed_output(binary, tmp_path, data):
    result = run(binary, "import sys; print('staged'); print(sys.stdin.read(2))", data, tmp_path)
    assert result.returncode == 90
    assert result.stdout == b""
    assert len(result.stderr.splitlines()) == 1
    assert b": unsupported: encoding:" in result.stderr


def test_dispatcher_replays_full_input_after_a_partial_read(binary, tmp_path):
    # eval is a runtime refusal; the dispatcher must replay the original input,
    # including the character already consumed before the refusal.
    program = "import sys\nprint(sys.stdin.read(1))\nprint(eval('2+3'))\nprint(repr(sys.stdin.read()))"
    got = subprocess.run([binary, "run", "-c", program], input=b"xyz\nq", capture_output=True, cwd=tmp_path, timeout=10)
    want = run(sys.executable, program, b"xyz\nq", tmp_path)
    assert got.returncode == want.returncode == 0
    assert got.stdout == want.stdout


def test_oversized_read_refuses_instead_of_masking_cpython_overflow(binary, tmp_path):
    result = run(binary, "import sys; print(sys.stdin.read(9223372036854775807))", b"abc", tmp_path)
    assert result.returncode == 90
    assert result.stdout == b""
    assert len(result.stderr.splitlines()) == 1
    assert b": unsupported: alloc:" in result.stderr
