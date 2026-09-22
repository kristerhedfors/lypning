"""Keep `fla` out of this process, and say which gated-delta rule actually bound.

Stdlib only and import-free on purpose: this module has to run BEFORE anything
that might import `fla` -- torch, transformers, `lypning_lora`, and the
`kernel_state` observation that used to import it itself.

WHY IT IS ITS OWN MODULE. The blocker used to be installed by `lypning_lora` at
import time, and `train_verified.run` called `kernel_state()` first -- which ran
`importlib.import_module("fla")`. A meta-path finder is never consulted for a
module already in `sys.modules`, so on an image where `fla` imports the blocker
was inert: transformers would bind fla's `chunk_gated_delta_rule` two lines after
the log said "will use the torch reference", and the manifest would read
`NTX_USE_FLA=0, fla: usable` -- which looks like "deliberately unused" and meant
the opposite. The kernel is part of an arm's identity (`STATUS.md` §2), so the
torch-reference arm has to be ENFORCED, not requested; `install` refuses to
pretend when it is too late, and `bound_kernels` reads what transformers
actually resolved rather than what was asked for.
"""
from __future__ import annotations

import importlib.abc
import sys

#: The two top-level packages that serve the gated-delta-net kernels.
BLOCKED = ("fla", "fla_core")


class Blocked(importlib.abc.MetaPathFinder):
    """Make `import fla` fail, so transformers resolves its torch reference."""

    def __init__(self, names):
        self.names = set(names)

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in self.names:
            raise ImportError("blocked: " + fullname)
        return None


def install(names=BLOCKED):
    """Put the blocker first on `sys.meta_path`; return what it is too late for.

    Idempotent: a second call finds its own finder and adds nothing. The return
    value is the list of blocked names ALREADY imported -- a finder cannot
    unload them, so the caller must treat a non-empty answer as "the reference
    path is not enforced" and refuse, not log and carry on.
    """
    names = tuple(names)
    if not any(isinstance(f, Blocked) and f.names >= set(names) for f in sys.meta_path):
        sys.meta_path.insert(0, Blocked(names))
    return sorted(m for m in sys.modules if m.split(".")[0] in names)


def _implementation(fn):
    """The function a transformers kernel wrapper will really call.

    `use_kernel_func_from_hub_with_fallback` (transformers 5.17.0,
    `integrations/hub_kernels.py`) resolves the package once, at decoration,
    and closes over the result as `implementation`; the module attribute is the
    `functools.wraps` wrapper, which carries the torch function's NAME whatever
    it calls. So the name proves nothing and the closure is the answer. Walks
    `__wrapped__` too, in case a hub-kernel decorator wraps it again.
    """
    seen = set()
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        for name, cell in zip(getattr(getattr(fn, "__code__", None), "co_freevars", ()),
                              getattr(fn, "__closure__", None) or ()):
            if name == "implementation":
                try:
                    return cell.cell_contents
                except ValueError:                      # empty cell
                    return None
        fn = getattr(fn, "__wrapped__", None)
    return None


def _describe(fn):
    if fn is None:
        return None
    return "%s.%s" % (getattr(fn, "__module__", "?"),
                      getattr(fn, "__qualname__", getattr(fn, "__name__", "?")))


#: The two gated-delta-rule wrappers transformers resolves at import.
FUNCTIONS = ("torch_chunk_gated_delta_rule", "torch_recurrent_gated_delta_rule")


def module_kernels(modeling, functions=FUNCTIONS):
    """What a modeling module's gated-delta-rule wrappers resolved to.

    The resolution happens when the module is IMPORTED (the decorator runs
    once), so this answer is available before any weight is loaded -- which
    is where `train_verified.run` asks it first, because a refusal after the
    55 GB pull is paid for and one before it is not. Never raises.
    """
    state = {"fla_imported": any(m.split(".")[0] in BLOCKED for m in sys.modules)}
    for name in functions:
        wrapper = getattr(modeling, name, None) if modeling is not None else None
        state[name] = _describe(_implementation(wrapper)) if wrapper is not None else None
    return state


def bound_kernels(model, functions=FUNCTIONS):
    """What the loaded model's gated-delta-net layers will call, read after load.

    Looks up the modeling module of the first module whose class name ends in
    `GatedDeltaNet` and reports, per wrapper, the resolved implementation's
    qualified name. Never raises: this is an observation for `experiment.json`,
    and a model with no such layer (a future architecture, a test double)
    answers `layer: None` rather than ending the run. The decision about what is
    admissible belongs to `reference_only`.
    """
    layer = None
    try:
        for _, module in model.named_modules():
            if type(module).__name__.endswith("GatedDeltaNet"):
                layer = module
                break
    except Exception:                                       # noqa: BLE001
        layer = None
    modeling = sys.modules.get(type(layer).__module__) if layer is not None else None
    state = {"layer": type(layer).__name__ if layer is not None else None}
    state.update(module_kernels(modeling, functions))
    return state


def reference_only(state):
    """True when every bound implementation is transformers' own torch code."""
    bound = [v for k, v in state.items() if k.startswith("torch_") and v is not None]
    return (not state.get("fla_imported") and bool(bound)
            and all(v.startswith("transformers.") for v in bound))


def refusal(modeling_module="transformers.models.qwen3_5.modeling_qwen3_5"):
    """The import-time kernel answer, as a refusal reason or None.

    For the job's deps stage: `train_verified.run` asks the same question, but
    its first call comes after review, preparation and the base-dev arm, so a
    wrong kernel on a new image would be discovered an hour into a billed job.
    The gated-delta rule is resolved when the modeling module is imported, so
    this answer needs no tokenizer, GPU or weight. `run` stays the in-process
    authority; this only moves the first refusal to minute one.
    """
    import importlib
    too_late = install()
    if too_late:
        return "fla was imported before its blocker: " + ", ".join(too_late)
    try:
        modeling = importlib.import_module(modeling_module)
    except ImportError as exc:
        return "cannot import %s: %s" % (modeling_module, exc)
    state = module_kernels(modeling)
    if not reference_only(state):
        return "gated-delta-net did not resolve to the torch reference: %r" % (state,)
    return None
