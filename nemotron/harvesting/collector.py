"""Trusted, offline collector for a read-only ephemeral volume.

Runs in a separate container while the producer is paused. Never follows links
or executes files. The controller treats even this tar stream as untrusted.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tarfile


def main():
    root = Path(sys.argv[1])
    if str(root) not in ("/capture/project", "/capture/output", "/capture"):
        raise SystemExit("Unexpected collection root")
    count = 0
    total = 0
    with tarfile.open(fileobj=sys.stdout.buffer, mode="w|") as archive:
        for directory, subdirs, files in os.walk(root, followlinks=False):
            for name in subdirs + files:
                path = Path(directory) / name
                info = archive.gettarinfo(str(path), arcname=str(Path(root.name) / path.relative_to(root)))
                count += 1
                if count > 4096:
                    raise SystemExit("Member cap exceeded")
                if info.isfile():
                    total += info.size
                    if total > 60 * 1024 * 1024:
                        raise SystemExit("Collection byte cap exceeded")
                    with path.open("rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)


if __name__ == "__main__":
    main()
