# lypning for Node

Runs the bottom slice of agent-typed Python — the one-liners — *inside* this
node process: no child process, no pipe, no serialisation. Everything else it
REFUSES, and the refusal is the design, not a failure.

The whole host, as `quickstart.js` has it (runnable: `node quickstart.js
'print(sum(range(10)))'`):

```js
const { spawnSync } = require('child_process');
const lypning = require('./index.js');

const [src, ...args] = process.argv.slice(2);

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
```

`fallOnward` is true exactly when lypning did not answer *and left nothing
behind* — nothing printed, no file touched, no stdin consumed — which is what
makes the retry safe. A traceback is not that: it is the program's own answer,
exit code 1, and `fallOnward` is false. `run()` never throws for a refusal,
only for a caller type error; `r.kind`/`r.detail` say what it declined.
`route(src)` asks without running.

Build: `cargo build --release` (a plain build links on Linux and macOS; see
`build.rs`). Zero npm deps, no node-gyp, no napi-rs — the Node-API symbols are
declared in `src/napi.rs` and resolved from the `node` process at load. Nothing
else builds the addon, `lypning build` included; on a read-only install (a
wheel) build the crate somewhere writable and copy the cdylib from
`target/release` into `$LYPNING_HOME/lib` by hand. `index.js` names every place
it looked when the addon is not there.

The tour, in order: `quickstart.js` is the host above and nothing more;
`example.js` is the whole binding exercised — routing, stdin and argv, the
sandbox, and the same branch applied to three programs. `npm test` runs both.
