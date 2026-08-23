// run_node.js — the Node host, over the Node-API addon.
//
// Same walk as the C, C++ and Rust drivers, and no fall-onward for the same
// reason: this counts what the subset itself takes.
//
// It logs each run to $LYPNING_LOG in the shim's own record shape. An addon
// call spawns no interpreter, so neither of lypning's capture feeds can see it
// — see study/hosts/capture.h for why that is the host's job.
'use strict';

const fs = require('fs');
const path = require('path');
const lypning = require(path.join(__dirname, '..', '..', 'src', 'lypning', 'assets', 'node'));

const LOG = process.env.LYPNING_LOG || '';
const SESSION = process.env.LYPNING_STUDY_SESSION || '';

function capture(host, program, args, exitCode, wallMs) {
  if (!LOG) return;
  const rec = {
    kind: 'python_invocation',
    ts: new Date().toISOString(),
    session: SESSION || null,
    shim: host,
    pid: process.pid,
    program,
    module: null,
    script: null,
    argv_tail: args,
    stdin_pipe: true,
    stdin_kind: 'bytes',
    exit_code: exitCode,
    wall_ms: wallMs,
  };
  try {
    fs.appendFileSync(LOG, JSON.stringify(rec) + '\n');
  } catch (e) {
    // Best-effort, exactly like the shim: a lost sighting, never a failed run.
  }
}

const dir = process.argv[2];
if (!dir) {
  process.stderr.write('usage: node run_node.js <hostset-dir>\n');
  process.exit(2);
}

const names = fs.readdirSync(dir).filter((n) => !n.startsWith('.')).sort();
let ran = 0, refused = 0, other = 0, n = 0;

for (const name of names) {
  const base = path.join(dir, name);
  let program;
  try {
    program = fs.readFileSync(path.join(base, 'program.py'), 'utf8');
  } catch (e) {
    continue;
  }
  n++;
  let stdin = Buffer.alloc(0);
  try { stdin = fs.readFileSync(path.join(base, 'stdin')); } catch (e) { /* none */ }
  let args = [];
  try {
    args = fs.readFileSync(path.join(base, 'args'), 'utf8').split('\n').filter((s) => s.length);
  } catch (e) { /* none */ }

  // The program runs in THIS process; give it the entry directory, where
  // prepare.py put the fixtures it was written against.
  const home = process.cwd();
  let moved = false;
  try { process.chdir(path.resolve(base)); moved = true; } catch (e) { /* best effort */ }

  const t0 = process.hrtime.bigint();
  const r = lypning.run(program, { args, stdin, stepLimit: 200000000, outputLimit: 1 << 20 });
  const ms = Number((process.hrtime.bigint() - t0) / 1000000n);
  if (moved) { try { process.chdir(home); } catch (e) { /* best effort */ } }

  if (r.statusName === 'ok') ran++;
  else if (r.statusName === 'unsupported') refused++;
  else other++;

  capture('node-embed', program, args, r.exitCode, ms);
}

process.stdout.write(`node-embed   ${n} programs: ${ran} ran, ${refused} refused, ${other} other\n`);
