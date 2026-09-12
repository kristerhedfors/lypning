"""Flag SFT targets that recite the answer instead of computing it.

A target whose whole program is one print() of literals reproduces the output
without doing the work; training on it teaches the model to guess outputs, which
is the one thing this project must never buy.
"""
import ast, json, sys

def is_recitation(src):
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    body = [n for n in tree.body if not isinstance(n, ast.Expr) or not isinstance(n.value, ast.Constant)]
    if len(body) != 1:
        return False
    n = body[0]
    if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
            and isinstance(n.value.func, ast.Name) and n.value.func.id == "print"):
        return False
    return all(isinstance(a, ast.Constant) for a in n.value.args)

def program(text):
    if "```" in text:
        text = text.split("```", 2)[1]
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return text

bad, keep = [], []
rows = [json.loads(l) for l in open(sys.argv[1], encoding="utf-8") if l.strip()]
for r in rows:
    src = program(r["messages"][-1]["content"])
    if is_recitation(src):
        bad.append((r.get("case_id"), src.strip().splitlines()[0][:70]))
    else:
        keep.append(r)
print("%d of %d targets are pure recitations" % (len(bad), len(rows)))
for c, t in bad[:10]:
    print("  %s  %s" % (c, t))
out = sys.argv[2] if len(sys.argv) > 2 else ""
if out:
    with open(out, "w", encoding="utf-8") as fh:
        for r in keep:
            fh.write(json.dumps(r, sort_keys=True) + "\n")
    print("wrote %d clean targets to %s" % (len(keep), out))
    sys.exit(0)
sys.exit(1 if bad else 0)
