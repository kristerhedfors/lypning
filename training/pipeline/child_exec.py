"""Single-threaded resource setup before exec; never a GPU-parent preexec_fn.

The setup-error pipe closes on successful exec. Program exit 127 therefore
remains a program result, not a guessed harness error. No stdout/stderr protocol
is borrowed from the program being evaluated.
"""
from __future__ import annotations

import json
import os
import resource
import sys


def main():
    fd = int(sys.argv[1])
    os.set_inheritable(fd, False)
    try:
        limits = json.loads(sys.argv[2])
        cpu = int(limits["timeout_s"]) + 2
        resource.setrlimit(resource.RLIMIT_CPU, (cpu, cpu + 1))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        cap = limits["output_cap"]
        resource.setrlimit(resource.RLIMIT_FSIZE, (cap, cap))
        if limits["mem_mb"] > 0 and sys.platform != "darwin":
            cap = limits["mem_mb"] * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
        if limits["nproc"] > 0:
            resource.setrlimit(resource.RLIMIT_NPROC, (limits["nproc"], limits["nproc"]))
        os.execvpe(sys.argv[3], sys.argv[3:], os.environ)
    except (OSError, ValueError, KeyError) as exc:
        os.write(fd, ("child setup/exec: " + str(exc)).encode("utf-8")[:4000])
        return 127


if __name__ == "__main__":
    sys.exit(main())
