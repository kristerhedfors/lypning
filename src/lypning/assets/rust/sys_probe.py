"""Build-time bake of `sys.version` for lypning-l (`cap-random`).

A SEPARATE probe from reference_probe.py, whose five-line shape the core's
behaviour flags are parsed from: build.rs runs this one only for a build that
carries `cap-random`, on the interpreter that answered the first probe, and
only when that interpreter's minor is the named reference (REF_PY_EXACT).
Three lines: `sys.version` as UTF-8 hex (3.9's contains a newline), the
realpath of `sys.executable`, and the realpath of the shared libpython or
framework image the launcher links ("" for a static build, "-" when it is
shared and not found, which build.rs treats as unbakeable). stdlib only, and
3.9-safe.
"""
import os
import sys
import sysconfig


def libpython():
    v = sysconfig.get_config_var
    fw = v("PYTHONFRAMEWORK")
    if fw:
        p = os.path.join(v("PYTHONFRAMEWORKPREFIX") or "", v("PYTHONFRAMEWORKDIR") or "",
                         "Versions", v("VERSION") or "", fw)
    elif v("Py_ENABLE_SHARED"):
        p = os.path.join(v("LIBDIR") or "", v("INSTSONAME") or "")
    else:
        return ""
    return os.path.realpath(p) if os.path.isfile(p) else "-"


print(sys.version.encode("utf-8").hex())
print(os.path.realpath(sys.executable) if sys.executable else "")
print(libpython())
