# lypning for Node

Runs the bottom slice of agent-typed Python — the one-liners — *inside* this
node process: no child process, no pipe, no serialisation. Everything else it
REFUSES, and the refusal is the design, not a failure.

```js
const lypning = require('./index.js');
const r = lypning.run(src);
if (r.fallOnward) { runOnPython3(src); }  // lypning ran NOTHING; your existing path
else { use(r.stdout); }                   // Buffer — output is not always UTF-8
```

`fallOnward` is true exactly when lypning declined *and left nothing behind* —
nothing printed, no file touched, no stdin consumed — which is what makes the
retry safe. `run()` never throws for a refusal, only for a caller type error;
`r.kind`/`r.detail` say what it declined. `route(src)` asks without running.

Build: `cargo build --release`. Zero npm deps, no node-gyp, no napi-rs — the
Node-API symbols are declared in `src/napi.rs`. Worked example: `example.js`.
