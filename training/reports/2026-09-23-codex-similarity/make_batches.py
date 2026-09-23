from __future__ import annotations
import io, json, random, re, tokenize, hashlib
d = json.load(open('/tmp/sim/pools.json'))
PAT = re.compile(r'claude|codex|gpt|openai|anthropic|opus|fable|sonnet|haiku|qwen|copilot|cursor|chatgpt|o[34]-mini|\bllm\b', re.I)
def strip(src):
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except Exception:
        return src, 0, True
    lines = src.splitlines(keepends=True)
    hits = [t for t in toks if t.type == tokenize.COMMENT and PAT.search(t.string)]
    # process bottom-up, right-to-left
    for t in sorted(hits, key=lambda t: t.start, reverse=True):
        r, c = t.start; r -= 1
        ln = lines[r]
        before = ln[:c]; after = ln[t.end[1]:]
        if before.strip() == '':
            lines[r] = ''  # whole-line comment: drop line
        else:
            lines[r] = before.rstrip(' \t') + after
    return ''.join(lines), len(hits), False
rng = random.Random(11)
pools = {'G': 'G', 'C1': 'C1', 'C2': 'C2' if len([r for r in d['C2'] if not r['contaminated']]) >= 20 else 'C3'}
items = []; stats = {}
for label, src in pools.items():
    cand = [r for r in d[src] if r['verdict'] == '' and not r['contaminated']]
    excluded = len(d[src]) - len(cand)
    cand.sort(key=lambda r: r['sha'])
    samp = rng.sample(cand, min(40, len(cand)))
    nstrip = 0; residual = 0; tokfail = 0
    for r in samp:
        prog, n, fail = strip(r['program'])
        nstrip += (n > 0); tokfail += fail
        residual += bool(PAT.search(prog))
        items.append({'pool': label, 'model': r['model'], 'sha': r['sha'], 'program': prog})
    stats[label] = {'source': src, 'available_tierA_clean': len(cand), 'excluded_contaminated': excluded,
                    'sampled': len(samp), 'programs_with_comments_stripped': nstrip,
                    'residual_name_mentions_outside_comments': residual, 'tokenize_fail': tokfail}
ids = {}
for it in items:
    oid = 'p' + hashlib.sha256(('sim11:' + it['sha'] + it['pool']).encode()).hexdigest()[:8]
    ids[oid] = it
key = {oid: {'pool': it['pool'], 'model': it['model'], 'sha': it['sha']} for oid, it in ids.items()}
json.dump(key, open('/tmp/sim/key.json', 'w'), indent=1)
order = sorted(ids)
for b in (1, 2, 3):
    o = order[:]; random.Random(11 * 100 + b).shuffle(o)
    json.dump([{'id': i, 'program': ids[i]['program']} for i in o], open(f'/tmp/sim/batch-{b}.json', 'w'), ensure_ascii=False, indent=1)
print(json.dumps(stats, indent=1)); print('total', len(ids))
