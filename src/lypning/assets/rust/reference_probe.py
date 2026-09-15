"""Build-time CPython behavior profile; no imports or probes in the runtime."""
from __future__ import annotations

import os.path
import sys


def profile():
    kind = type(os.path.normpath).__name__
    if kind not in ("function", "builtin_function_or_method"):
        raise RuntimeError("unknown normpath callable type: " + kind)
    try:
        reverse_truth = (sorted([2, 1], reverse=None) == [1, 2]
                         and sorted([2, 1], reverse=[0]) == [2, 1]
                         and sorted([2, 1], reverse=[]) == [1, 2])
    except TypeError:
        reverse_truth = False
    try:
        iter([], None)
    except TypeError as exc:
        message = str(exc)
    else:
        raise RuntimeError("iter(noncallable, sentinel) did not raise")
    if message not in ("iter(v, w): v must be callable",
                       "iter(object, sentinel): object must be callable"):
        raise RuntimeError("unknown iter error: " + message)
    try:
        empty = (-1).to_bytes(0, "big", signed=True)
    except OverflowError:
        zero_negative_overflow = True
    else:
        if empty != b"":
            raise RuntimeError("unknown zero-length signed integer conversion")
        zero_negative_overflow = False
    return ("%d.%d" % sys.version_info[:2], int(kind == "builtin_function_or_method"),
            int(reverse_truth), int(message == "iter(v, w): v must be callable"),
            int(zero_negative_overflow))


if __name__ == "__main__":
    print(*profile(), sep="\n")
