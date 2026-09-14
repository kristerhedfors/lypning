"""The new L slices must compose, not merely pass separate module grids."""

from __future__ import annotations

import sys

import pytest

from lypning import engines


PROGRAMS = [
    "import re, csv\n"
    "text = 'id,count\\na-1,2\\nb-3,4\\n'\n"
    "lines = [m['line'] for m in re.finditer(r'(?P<line>[^\\n]+)', text)]\n"
    "for row in csv.DictReader(lines):\n"
    "    m = re.fullmatch(r'(?P<item>[a-z]+)-(?P<index>[0-9]+)', row['id'])\n"
    "    print(m.groupdict(), int(row['count']))\n",
    "import re, csv\n"
    "names = list(re.fullmatch('(?P<a>[a-z]+),(?P<b>[a-z]+)', 'key,value').groups())\n"
    "reader = csv.DictReader(['1,2', '3,4'], fieldnames=names)\n"
    "print(next(reader))\n"
    "names[0] = 'renamed'\n"
    "print(next(reader))\n",
    "import re, csv\n"
    "rows = csv.reader(re.split(r'\\r?\\n', 'a,1\\r\\nb,2\\n'))\n"
    "for row in rows:\n"
    "    if row:\n"
    "        print(re.sub('(?P<letter>[a-z])', lambda m: m['letter'].upper(), row[0]), row[1])\n",
]


@pytest.mark.parametrize("program", PROGRAMS, ids=range(len(PROGRAMS)))
def test_csv_and_named_regex_compose_natively(program, monkeypatch):
    monkeypatch.setenv("LYPNING_CPYTHON", sys.executable)
    binary = engines.find(engines.LYPNING_L)
    if binary is None:
        pytest.skip("lypning-l is not built")
    reference = engines.run(engines.CPYTHON, program)
    result = engines.run(engines.LYPNING_L, program, binary=binary)
    assert reference.returncode == 0, reference.stderr
    assert (result.returncode, result.stdout, result.stderr) == (
        0, reference.stdout, reference.stderr
    )
