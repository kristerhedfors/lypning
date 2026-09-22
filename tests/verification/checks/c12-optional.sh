#!/bin/sh
# tool: python
python3 - <<'PY'
from unittest.mock import patch
from lypning import engines, bench, conformance, corpus
find = engines.find
with patch.object(engines, 'find', lambda name: None if name == engines.LYPNING_L else find(name)):
    print('lypning-l', 'not built' if engines.find(engines.LYPNING_L) is None else 'built')
    print('benchmark arms', len(bench.resolve_arms([engines.LYPNING_L])))
    report = conformance.run([corpus.Entry(id='absent-variant', program='print(1)')], engines=[engines.LYPNING_L])
    print('conformance unbuilt', ' '.join(report.unbuilt))
PY
