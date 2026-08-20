"""``python -m lypning`` — the same front door as the console script.

Two lines of substance on purpose: everything, interpreter mode included, is
:func:`lypning.cli.main`'s, so the two entry points cannot drift apart.
"""

from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
