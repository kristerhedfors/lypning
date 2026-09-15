"""Small authored smoke curriculum, NOT an independent generalisation benchmark.

Inputs/answers are hand specified, never inferred from the reference's output.
Extend via the same JSONL schema with independently authored semantic families;
cloning these templates increases example count, not task diversity.
"""
from __future__ import annotations


def starter_cases():
    specs = [
        ("sum", "Read whitespace-separated integers from stdin; print their sum (0 if empty).",
         "import sys\nprint(sum(int(x) for x in sys.stdin.read().split()))", [("1 2 3", "6\n"), ("-7 2", "-5\n"), ("", "0\n")]),
        ("sort", "Read whitespace-separated integers from stdin; print them numerically sorted, separated by single spaces.",
         "import sys\nprint(' '.join(str(x) for x in sorted(int(x) for x in sys.stdin.read().split())))", [("9 -1 2", "-1 2 9\n"), ("3 3 1", "1 3 3\n"), ("", "\n")]),
        ("unique", "Read words from stdin. Print unique words in first-appearance order, separated by single spaces.",
         "import sys\na=[]\nfor x in sys.stdin.read().split():\n    if x not in a:\n        a.append(x)\nprint(' '.join(a))", [("b a b c", "b a c\n"), ("z z", "z\n"), ("", "\n")]),
        ("frequency", "Read words from stdin. For each distinct word in alphabetical order print word:count on its own line.",
         "import sys\nfrom collections import Counter\nc=Counter(sys.stdin.read().split())\nfor k in sorted(c):\n    print(k+':'+str(c[k]))", [("b a b", "a:1\nb:2\n"), ("z z z", "z:3\n"), ("", "")]),
        ("regex", "Read text from stdin. Print every contiguous sequence of ASCII digits in encounter order, separated by spaces.",
         "import sys,re\nprint(' '.join(re.findall('[0-9]+',sys.stdin.read())))", [("a12b003", "12 003\n"), ("no digits", "\n"), ("-5 8.2", "5 8 2\n")]),
        ("json", "Read a JSON object mapping names to integers from stdin. Print name=value lines in alphabetical name order.",
         "import sys,json\nd=json.loads(sys.stdin.read())\nfor k in sorted(d):\n    print(k+'='+str(d[k]))", [('{"b":2,"a":-1}', "a=-1\nb=2\n"), ('{"z":0}', "z=0\n"), ("{}", "")]),
        ("csv", "Read CSV with no header from stdin. Print the number of fields in each row, one count per line. Honor CSV quoting.",
         "import sys,csv\nfor row in csv.reader(sys.stdin):\n    print(len(row))", [('a,"b,c"\nx,y,z\n', "2\n3\n"), ("solo\n", "1\n"), ("", "")]),
        ("bigint", "Read a nonnegative integer n from stdin. Print the exact integer 2 raised to n, with no floating-point conversion.",
         "import sys\nprint(2**int(sys.stdin.read()))", [("0", "1\n"), ("10", "1024\n"), ("100", "1267650600228229401496703205376\n")]),
        ("gcd", "Read exactly two integers from stdin. Print their nonnegative greatest common divisor.",
         "import sys,math\na,b=map(int,sys.stdin.read().split())\nprint(math.gcd(a,b))", [("12 18", "6\n"), ("0 -7", "7\n"), ("13 17", "1\n")]),
        ("unicode", "Read text from stdin, strip leading/trailing whitespace and print its uppercase form, preserving Unicode casing rules.",
         "import sys\nprint(sys.stdin.read().strip().upper())", [("  hello\n", "HELLO\n"), ("straße", "STRASSE\n"), ("åäö", "ÅÄÖ\n")]),
        ("hex", "Read whitespace-separated byte values in hexadecimal from stdin. Print their decimal values separated by spaces.",
         "import sys\nprint(' '.join(str(int(x,16)) for x in sys.stdin.read().split()))", [("00 ff 10", "0 255 16\n"), ("aB", "171\n"), ("", "\n")]),
        ("brackets", "Read only parentheses from stdin, ignoring surrounding whitespace. Print yes if balanced, otherwise no.",
         "import sys\nn=0\nok=True\nfor c in sys.stdin.read().strip():\n    n += 1 if c=='(' else -1\n    if n<0:\n        ok=False\nprint('yes' if ok and n==0 else 'no')", [("(())", "yes\n"), (")(", "no\n"), ("(()", "no\n")]),
        ("hash", "Read text from stdin. Print the SHA-256 hexadecimal digest of its UTF-8 encoding, including any newlines in the input.",
         "import sys,hashlib\nprint(hashlib.sha256(sys.stdin.read().encode('utf-8')).hexdigest())", [("", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"), ("abc", "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad\n"), ("hello", "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824\n")]),
        ("decimal", "Read exactly two decimal numbers from stdin. Using decimal.Decimal, print their exact sum in Decimal's default string format.",
         "import sys\nfrom decimal import Decimal\na,b=sys.stdin.read().split()\nprint(Decimal(a)+Decimal(b))", [("0.1 0.2", "0.3\n"), ("-1.5 2.0", "0.5\n"), ("100 0.01", "100.01\n")]),
        ("argv", "Print command-line arguments after the script name, one per line, preserving their order and contents.",
         "import sys\nfor a in sys.argv[1:]:\n    print(a)", [("", "unused")]),
        ("files", "Read UTF-8 file input.txt from the current directory. Print its number of whitespace-separated words.",
         "from pathlib import Path\nprint(len(Path('input.txt').read_text(encoding='utf-8').split()))", [("", "unused")]),
    ]
    cases = []
    for family, task, reference, pairs in specs:
        tests = [{"stdin": s, "stdout": out} for s, out in pairs]
        if family == "argv":
            tests = [{"argv": a, "stdout": o} for a, o in
                     [([], ""), (["a", "two words"], "a\ntwo words\n"), (["å"], "å\n")]]
        if family == "files":
            tests = [{"files": {"input.txt": s}, "stdout": o} for s, o in
                     [("one two\nthree", "3\n"), ("", "0\n"), ("å", "1\n")]]
        cases.append({"case_id": "starter-" + family, "family": family, "task": task,
                      "source_group": "starter-" + family, "capabilities": [family],
                      "reference": reference, "tests": tests,
                      "population": "fallback-control" if family == "decimal" else "coverage",
                      "provenance": "hand-authored smoke fixture, 2026-09-14; not a benchmark"})
    return cases
