"""A pre-warmed CPython that forks per program — the chain's backstop tier.

The measurements that motivated this module are in ``docs/PAPER.md`` §5.4: over
the CPython-clean corpus subset, a cold ``python3`` spawn costs ~17 ms per
program of which ~16.8 ms is process spawn, interpreter startup and the
program's own imports, while the program itself computes for ~0.019 ms at the
median. A resident interpreter pays that once. Every arm of the chain that ends
at CPython was paying it per program.

**What this is.** A parent process that imports the interpreter once and then,
per request, forks a child that ``chdir``s into the caller's directory, execs
the program text, and exits. The parent never runs the program, so a program
that corrupts its interpreter, exhausts its recursion limit or calls
``os._exit`` costs one child, not the pool.

**What this is not.** It is not a sandbox and gives the caller nothing the
harness did not already have: the child inherits the parent's credentials and
namespace, and a program that deletes a file still deletes it. It is also not a
capability the refusal contract knows about — the pool answers programs that
have *already* been refused by every faster tier, so by the time it runs, the
question "may we run this" has been settled.

**Why fork and not a persistent namespace.** Reusing one interpreter across
programs would leak state — imported modules, mutated builtins, changed cwd,
installed signal handlers — from one agent one-liner into the next. That is the
one failure this project cannot accept, because it would produce a wrong answer
silently. Forking gives each program the parent's *warm* module cache and its
own copy of everything mutable.

**The environment is frozen at start, and one part of it cannot be thawed.**
A forked child adopts the caller's ``os.environ`` (the client sends it), so
anything read at run time — locale settings, ``PATH``, a program's own
configuration — matches a cold spawn. What a fork cannot undo is what CPython
consumed at *interpreter start*: ``PYTHONHASHSEED`` is already baked into this
process's hash randomization. Measured on the corpus (2026-08-31): a pool
started without the caller's seed diverged from a cold ``python3`` on 3 of 745
programs, all of them printing a set or a dict-view whose order is seed-
dependent; started with it, 0 of 745. **Start the pool with the hash seed its
callers expect.**

**The inherited-import subtlety, stated because it flatters us.** The child
inherits whatever the parent has already imported, so a program doing
``import json`` pays nothing where a cold spawn pays the import. That is a real
advantage of the design and also the reason the parent deliberately pre-imports
a small set: the alternative is a pool whose speed depends on which program
happened to run first.
"""

from __future__ import annotations

import io
import json
import os
import socket
import struct
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

__all__ = ["PoolError", "Server", "Client", "serve", "PRELOAD", "DEFAULT_SOCKET"]

#: Modules the parent imports before accepting work. Drawn from the corpus
#: profile (``docs/PAPER.md`` §3.1): these are the imports agent programs
#: actually reach for, and pre-importing them is what makes a forked child
#: cheaper than a cold interpreter rather than merely equal to one. Keep this
#: list short — every entry is resident memory in the parent, held for the
#: lifetime of the pool, whether or not any program uses it.
PRELOAD: tuple[str, ...] = (
    "json", "sys", "re", "os", "io", "collections", "pathlib",
    "itertools", "math", "textwrap", "csv", "hashlib", "datetime",
)

DEFAULT_SOCKET = "lypning-pool.sock"

_HDR = struct.Struct("!I")


class PoolError(RuntimeError):
    """The pool could not be reached, or answered something unintelligible."""


def _send(sock: socket.socket, obj: dict) -> None:
    payload = json.dumps(obj).encode("utf-8")
    sock.sendall(_HDR.pack(len(payload)) + payload)


def _recv(sock: socket.socket) -> Optional[dict]:
    head = _read_exactly(sock, _HDR.size)
    if head is None:
        return None
    (n,) = _HDR.unpack(head)
    body = _read_exactly(sock, n)
    if body is None:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as exc:
        raise PoolError("pool sent malformed JSON: %s" % exc) from exc


def _read_exactly(sock: socket.socket, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class Server:
    """The resident parent. Owns the listening socket and forks per request."""

    def __init__(self, path: Path | str, *, preload: Sequence[str] = PRELOAD,
                 backlog: int = 64) -> None:
        self.path = Path(path)
        self.preload = tuple(preload)
        self._backlog = backlog
        self._sock: Optional[socket.socket] = None
        self._stop = False
        self.served = 0

    # -- lifecycle ------------------------------------------------------------

    def warm(self) -> list[str]:
        """Import the preload set. Returns the names that failed, if any.

        A missing module is not an error: the pool's job is to be warm, not to
        guarantee an environment, and a child that needs it will import it
        itself at the cold price.
        """
        failed: list[str] = []
        for name in self.preload:
            try:
                __import__(name)
            except Exception:
                failed.append(name)
        return failed

    def open(self) -> None:
        if self.path.exists():
            self.path.unlink()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.path))
        # The socket carries program text and returns program output. Nothing
        # about it should be readable by another user on a shared host.
        os.chmod(str(self.path), 0o600)
        sock.listen(self._backlog)
        self._sock = sock

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        try:
            self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "Server":
        self.warm()
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- serving --------------------------------------------------------------

    def serve_forever(self, *, max_requests: int = 0) -> int:
        """Accept and answer until the socket closes, or ``max_requests`` is hit."""
        if self._sock is None:
            raise PoolError("serve_forever() before open()")
        while not self._stop:
            conn, _ = self._sock.accept()
            try:
                self.serve_one(conn)
            finally:
                conn.close()
            self.served += 1
            if self._stop or (max_requests and self.served >= max_requests):
                return self.served
        return self.served

    def serve_one(self, conn: socket.socket) -> None:
        req = _recv(conn)
        if req is None:
            return
        if req.get("op") == "ping":
            _send(conn, {"ok": True, "served": self.served, "pid": os.getpid()})
            return
        if req.get("op") == "shutdown":
            # A flag, not an exception: serve_forever() may be running in a
            # host's thread, where an unhandled SystemExit is that host's
            # problem rather than a clean stop.
            self._stop = True
            _send(conn, {"ok": True, "served": self.served})
            return
        _send(conn, self._run_forked(req))

    def _run_forked(self, req: dict) -> dict:
        """Fork, run the program in the child, return what the caller would see.

        stdout and stderr are captured through pipes rather than temp files so
        that a program which never flushes still has its output collected when
        the child's descriptors close at exit.
        """
        program = req.get("program", "")
        cwd = req.get("cwd") or os.getcwd()
        argv_tail = list(req.get("argv_tail") or ())
        stdin_text = req.get("stdin")
        caller_env = req.get("env")

        out_r, out_w = os.pipe()
        err_r, err_w = os.pipe()
        if stdin_text is None:
            in_r, in_w = os.open(os.devnull, os.O_RDONLY), None
        else:
            in_r, in_w = os.pipe()

        pid = os.fork()
        if pid == 0:                                   # ---- child ----
            code = 1
            try:
                os.close(out_r); os.close(err_r)
                if in_w is not None:
                    os.close(in_w)
                os.dup2(in_r, 0); os.dup2(out_w, 1); os.dup2(err_w, 2)
                os.close(out_w); os.close(err_w)
                if in_r > 2:
                    os.close(in_r)
                if caller_env is not None:
                    # A warm pool freezes its environment at start, and a
                    # program that reads os.environ would then see the pool's
                    # world rather than the caller's. Adopting the caller's
                    # environment fixes that for everything read at RUN time.
                    # It cannot fix what CPython consumes at INTERPRETER START:
                    # PYTHONHASHSEED is already baked into this process's hash
                    # randomization, and a fork cannot undo it. A pool must
                    # therefore be started with the hash seed its callers
                    # expect -- see the module docstring.
                    os.environ.clear()
                    os.environ.update(caller_env)
                os.chdir(cwd)
                # Rebind the Python-level streams onto the descriptors we just
                # dup'd. Inheriting the parent's sys.stdout is not enough: a
                # host that has replaced it (a test harness capturing output,
                # an embedding that redirects) leaves the child writing
                # somewhere the pipe cannot see, and the caller silently gets
                # empty output. The fds are right; the objects must be too.
                # errors= and write_through= are CPython's own settings for a
                # non-tty stdio; a program that round-trips undecodable bytes
                # relies on surrogateescape, and "replace" would corrupt them.
                sys.stdin = io.TextIOWrapper(io.FileIO(0, "r", closefd=False),
                                             encoding="utf-8", errors="surrogateescape")
                sys.stdout = io.TextIOWrapper(io.FileIO(1, "w", closefd=False),
                                              encoding="utf-8", errors="surrogateescape",
                                              write_through=True)
                sys.stderr = io.TextIOWrapper(io.FileIO(2, "w", closefd=False),
                                              encoding="utf-8", errors="backslashreplace",
                                              write_through=True)
                sys.argv = ["-c"] + argv_tail
                # A fresh module namespace, so the program cannot see the
                # pool's own globals -- but the parent's sys.modules stays,
                # which is the whole point of being warm.
                glb = {"__name__": "__main__", "__builtins__": __builtins__,
                       "__file__": None, "__doc__": None, "__package__": None}
                exec(compile(program, "<string>", "exec"), glb)
                code = 0
            except SystemExit as e:
                # CPython's -c prints a non-integer SystemExit argument to
                # stderr and exits 1; dropping the message would make the pool
                # distinguishable from the interpreter it stands in for.
                if e.code is None:
                    code = 0
                elif isinstance(e.code, int):
                    code = e.code
                else:
                    try:
                        sys.stderr.write("%s\n" % (e.code,))
                    except BaseException:
                        pass
                    code = 1
            except BaseException as exc:
                # Print the traceback the CALLER would have seen. The frame
                # that ran exec() is ours, and CPython's `-c` has no such
                # frame, so it is dropped: a traceback that names pool.py is a
                # visible difference from the interpreter being stood in for.
                import traceback
                try:
                    tb = exc.__traceback__
                    traceback.print_exception(type(exc), exc,
                                              tb.tb_next if tb is not None else tb)
                except BaseException:
                    pass
                code = 1
            finally:
                try:
                    sys.stdout.flush()
                except BaseException:
                    pass
                try:
                    sys.stderr.flush()
                except BaseException:
                    pass
                os._exit(code)

        # ---- parent ----
        os.close(out_w); os.close(err_w); os.close(in_r)
        if in_w is not None:
            try:
                os.write(in_w, (stdin_text or "").encode("utf-8"))
            except OSError:
                pass
            os.close(in_w)
        out = _drain(out_r)
        err = _drain(err_r)
        _, status = os.waitpid(pid, 0)
        if os.WIFSIGNALED(status):
            rc = -os.WTERMSIG(status)
        else:
            rc = os.WEXITSTATUS(status)
        return {"ok": True, "returncode": rc,
                "stdout": out.decode("utf-8", "replace"),
                "stderr": err.decode("utf-8", "replace")}


def _drain(fd: int) -> bytes:
    chunks = []
    try:
        while True:
            b = os.read(fd, 65536)
            if not b:
                break
            chunks.append(b)
    finally:
        os.close(fd)
    return b"".join(chunks)


class Client:
    """Talks to a :class:`Server` over its unix socket."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def _connect(self) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(self.path))
        except OSError as exc:
            sock.close()
            raise PoolError("no pool at %s: %s" % (self.path, exc)) from exc
        return sock

    def ping(self) -> dict:
        sock = self._connect()
        try:
            _send(sock, {"op": "ping"})
            reply = _recv(sock)
        finally:
            sock.close()
        if reply is None:
            raise PoolError("pool closed the connection during ping")
        return reply

    def run(self, program: str, *, cwd: Path | str | None = None,
            argv_tail: Sequence[str] = (), stdin: str | None = None,
            env: dict | None = None) -> dict:
        sock = self._connect()
        try:
            _send(sock, {"op": "run", "program": program,
                         "cwd": str(cwd) if cwd else None,
                         "argv_tail": list(argv_tail), "stdin": stdin,
                         "env": dict(env) if env is not None else None})
            reply = _recv(sock)
        finally:
            sock.close()
        if reply is None:
            raise PoolError("pool closed the connection without answering")
        return reply

    def shutdown(self) -> None:
        try:
            sock = self._connect()
        except PoolError:
            return
        try:
            _send(sock, {"op": "shutdown"})
            _recv(sock)
        except (OSError, PoolError):
            pass
        finally:
            sock.close()


def serve(path: Path | str | None = None, *, max_requests: int = 0) -> int:
    """Run a pool in this process until shut down. Returns programs served."""
    if path is None:
        path = Path(tempfile.gettempdir()) / DEFAULT_SOCKET
    with Server(path) as server:
        return server.serve_forever(max_requests=max_requests)
