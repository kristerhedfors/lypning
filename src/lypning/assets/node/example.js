'use strict';

// The whole binding, exercised — and in particular the one branch a harness
// must get right.
//
// Six sections. Five are ordinary: version, route, run, stdin and argv, the
// sandbox. The sixth is the only one that matters, because it is the one a
// harness author skips: a refusal arrives as a VALUE with fallOnward true, and
// the correct response is to run the same source on CPython and use that
// answer. There is not a single try/catch below, because nothing here throws.
//
//     node example.js

const { spawnSync } = require('child_process');
const lypning = require('./index.js');

const text = (buf) => buf.toString('utf8').replace(/\n$/, '');
const indent = (buf) => text(buf).split('\n').map((l) => '  | ' + l).join('\n');
const rule = (s) => console.log('\n-- ' + s + ' ' + '-'.repeat(Math.max(0, 58 - s.length)));

// --- 1. what did we load ----------------------------------------------------

rule('version');
console.log('runtime   ', lypning.version());
console.log('C ABI     ', lypning.abiVersion());
console.log('addon     ', lypning.addonPath);

// --- 2. routing: the answer without the run ---------------------------------
//
// One parse, no execution. A harness holding a queue of programs can sort them
// before deciding which are worth a CPython spawn at all.

rule('route');
for (const src of [
  'print(sum(int(x) for x in "1 2 3".split()))',
  'import re; print(re.findall(r"\\d+", "a1b22"))',
]) {
  const r = lypning.route(src);
  console.log(
    r.engine.padEnd(11),
    (src.length > 46 ? src.slice(0, 45) + '…' : src).padEnd(47),
    r.kind ? r.kind + ': ' + r.detail : '',
    r.imports.length ? '(imports ' + r.imports.join(', ') + ')' : ''
  );
}

// --- 3. running -------------------------------------------------------------

rule('run');
const ok = lypning.run('print("hello from lypning"); print(2 ** 10)');
console.log('statusName', ok.statusName, '| exitCode', ok.exitCode, '| committed', ok.committed);
console.log('stdout is a Buffer:', Buffer.isBuffer(ok.stdout), '| bytes', ok.stdout.length);
console.log(indent(ok.stdout));

// Bytes, never a re-encoded string. Here the gap is visible without leaving
// UTF-8 at all — more bytes than characters — but the reason for the rule is
// the case you do not see coming: a program whose output is not text. Decoding
// on the way out would hand JS a U+FFFD where the program wrote a byte, which
// is a wrong answer of exactly the kind this project exists to refuse.
const wide = lypning.run('print("dröm — åäö")');
console.log('bytes     ', wide.stdout.length, '| characters', text(wide.stdout).length);
console.log('hex       ', wide.stdout.toString('hex'));

// A program that raises is NOT a refusal. The traceback is the program's own
// answer, exit code 1, and re-running it elsewhere would only repeat it — so
// fallOnward is false and the harness must not retry.
rule('a traceback is an answer, not a refusal');
const boom = lypning.run('print(1 / 0)');
console.log('statusName', boom.statusName, '| exitCode', boom.exitCode, '| fallOnward', boom.fallOnward);
console.log('  | ' + text(boom.stderr).split('\n').pop());

// --- 4. stdin and argv ------------------------------------------------------
//
// The addon never reads node's fd 0. The program's stdin is a value you hand
// it: a string, or a Uint8Array when the input did not start out as text.

rule('stdin and argv');
const piped = lypning.run(
  'import sys\n' +
    'n = int(sys.argv[1])\n' +
    'for line in sys.stdin:\n' +
    '    line = line.strip()\n' +
    '    if line:\n' +
    '        print(line[:n].upper())\n',
  { args: ['4'], stdin: 'alpha\nbravo\ncharlie\n', filename: 'head.py' }
);
console.log('statusName', piped.statusName, '| exitCode', piped.exitCode);
console.log(indent(piped.stdout));

const bytesIn = lypning.run('import sys; print(len(sys.stdin.read().split()), "words")', {
  stdin: new Uint8Array([104, 105, 32, 116, 104, 101, 114, 101, 10]),
});
console.log('Uint8Array stdin ->', text(bytesIn.stdout));

// --- 5. the sandbox refusal -------------------------------------------------
//
// filesystem:false does not lie to the program — it is never told the file is
// missing. The run refuses, and a refusal is routable, so YOU decide whether
// this is a program you are willing to hand to CPython. The policy lives with
// the harness, which is the only place that knows it.

rule('sandbox: a refusal, not a lie');
const sandboxed = lypning.run('print(open("/etc/hostname").read().strip())', { filesystem: false });
console.log('statusName', sandboxed.statusName, '| exitCode', sandboxed.exitCode);
console.log('kind      ', sandboxed.kind, '| detail', sandboxed.detail);
console.log('stdout    ', sandboxed.stdout.length, 'bytes  (the commit barrier: nothing was written)');
console.log('committed ', sandboxed.committed, '| fallOnward', sandboxed.fallOnward);
console.log('stderr    ', text(sandboxed.stderr));
console.log('-> our policy said no, so we do NOT fall onward here. We drop it.');

// --- 6. the branch a harness must write -------------------------------------
//
// This is quickstart.js applied to three programs. The `if (r.fallOnward)` is
// the same one, with the same stepLimit reasoning; what is added is only the
// bookkeeping that lets the three lines print side by side.

rule('fall onward to CPython');

// `p.status` is null when the spawn itself failed (no python3 on PATH) or the
// child died on a signal, and `p.stdout` is null with it. Normalised here so
// the rest of this file stays true to its own header: nothing below throws,
// including on a machine that has no python3 to fall onward TO.
function runOnPython3(source) {
  const p = spawnSync('python3', ['-c', source], { encoding: 'buffer' });
  return {
    exitCode: p.status === null ? -1 : p.status,
    stdout: p.stdout || Buffer.alloc(0),
    stderr: p.stderr || Buffer.from(p.error ? String(p.error.message) + '\n' : ''),
  };
}

function dispatch(source) {
  // stepLimit because these are programs a model wrote: run() executes on THIS
  // thread, and a process can be killed where a function call cannot.
  const r = lypning.run(source, { stepLimit: 5000000 });
  if (r.fallOnward) {
    // lypning ran NOTHING. Not an error, not a warning, not a log line at
    // level ERROR — the program is outside the subset, and CPython gets it.
    const p = runOnPython3(source);
    return { engine: 'cpython', why: r.kind + ': ' + r.detail, ...p };
  }
  return { engine: 'lypning', why: '', exitCode: r.exitCode, stdout: r.stdout, stderr: r.stderr };
}

for (const source of [
  'print(", ".join(sorted("the quick brown fox".split())))',
  'import re; print(re.sub(r"\\s+", "-", "the quick brown fox"))',
  'import statistics; print(statistics.median([3, 1, 4, 1, 5, 9, 2, 6]))',
]) {
  const r = dispatch(source);
  console.log(r.engine.padEnd(8), text(r.stdout).padEnd(34), r.why ? '(fell onward: ' + r.why + ')' : '');
}

// The same predicate, for the OTHER interpreters a harness may chain. Ask it
// about a child process's exit code rather than re-deriving the exit-90
// contract yourself and getting the MemoryError case wrong.
rule("fallOnward() on somebody else's exit code");
const child = runOnPython3('import nonexistent_module');
console.log('python3 exit', child.exitCode, '-> fall onward?', lypning.fallOnward(child.exitCode, child.stderr));
console.log('exit 90      -> fall onward?', lypning.fallOnward(90, 'lypning: unsupported: module: import re\n'));
console.log('exit 0       -> fall onward?', lypning.fallOnward(0, ''));
console.log();
