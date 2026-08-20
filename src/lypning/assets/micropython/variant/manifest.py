# lypning-mp's frozen module manifest.
#
# The frozen stdlib is half the mechanism (docs/RESEARCH.md §1): a
# runtime whose library lives as .py files on disk re-inherits CPython's
# problem, because in the sandbox those files are fetched over a WebSocket one
# at a time. Everything lypning-mp ships in Python is compiled into the binary.
#
# This file deliberately names NO individual module. It freezes every .py under
# micropython/lib/ by glob, so the shim stdlib can grow — base64, os.path, glob,
# collections.Counter, the re.findall/finditer/split helpers
# (docs/RESEARCH.md §6 item 2) — without this file, the variant, or the
# build script ever being edited again.
#
# LYPNING_LIB_DIR is exported by mpconfigvariant.mk and points at the copy of
# micropython/lib that scripts/build-micropython.sh syncs into the pinned MicroPython
# checkout. Reading it from the environment rather than using a $(VARIANT_DIR)
# substitution is deliberate: the substitution happens inside freeze(), which
# is too late for the existence check below.

import os

_lib = os.environ.get("LYPNING_LIB_DIR", "")

# freeze() over a directory with no .py in it happens to be a no-op in current
# MicroPython, but that is not a documented contract — and micropython/lib is empty
# in a fresh checkout, holding only .gitkeep until the shim modules land. Check
# first, so an empty library can never fail a build.
if _lib and os.path.isdir(_lib) and any(n.endswith(".py") for n in os.listdir(_lib)):
    freeze(_lib)
