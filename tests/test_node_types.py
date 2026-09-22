"""Keep Node's route declaration aligned without a TypeScript dependency."""

from __future__ import annotations

import re
from pathlib import Path

from lypning import engines


def test_node_route_engine_type_matches_the_routing_spectrum():
    """The addon forwards the C ABI's route engine; it does not rename it.

    Even an addon embedding the core can route to the larger Rust variant.
    """
    declaration = (Path(__file__).resolve().parents[1] / "src" / "lypning"
                   / "assets" / "node" / "index.d.ts").read_text(encoding="utf-8")
    match = re.search(r"export type Engine\s*=\s*([^;]+);", declaration)
    assert match is not None, "Node must declare its route Engine union"
    literals = tuple(re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)))
    assert literals == engines.ENGINE_ORDER
