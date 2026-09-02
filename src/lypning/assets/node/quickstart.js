#!/usr/bin/env node
'use strict';
// quickstart.js — the smallest correct lypning host, in Node.
//
//   cd src/lypning/assets/node && cargo build --release && \
//     node quickstart.js 'print(sum(range(10)))'
//
// Usage: node quickstart.js "<python source>" [args...]

const { spawnSync } = require('child_process');
const lypning = require('./index.js');

const [src, ...args] = process.argv.slice(2);
if (src === undefined) {
  process.stderr.write('usage: node quickstart.js "<python source>" [args...]\n');
  process.exit(2);
}

// stepLimit: a model wrote this program and it runs on THIS thread. No process to kill.
const r = lypning.run(src, { args, stepLimit: 10000000 });

if (r.fallOnward) {
  // A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
  const p = spawnSync('python3', ['-c', src, ...args], { stdio: ['ignore', 'inherit', 'inherit'] });
  process.exit(p.status ?? 1);
} else {
  process.stdout.write(r.stdout);
  process.stderr.write(r.stderr);
  process.exitCode = r.exitCode;
}
