// lypning — the opencode plugin: observe, and get out of the way.
// lypning-opencode-plugin: written by `lypning install --harness opencode`.
// This marker line is how uninstall decides the file is ours to remove;
// delete it and lypning will treat the file as somebody else's and leave it.
//
// opencode auto-discovers `{plugin,plugins}/*.{ts,js}` under its config
// directories, so this file is the whole installation: no config entry, no
// merge into anything the user owns. lypning writes exactly one filename and
// decides ownership from the file's own bytes (the marker line above).
//
// The invariant this file exists to hold is the same one the Claude Code hook
// holds: it NEVER blocks a tool call and NEVER decides permission. Every write
// is wrapped, a failed log is a lost sighting rather than a failed command, and
// nothing here throws — in opencode, throwing from `tool.execute.before` IS the
// deny mechanism, so an unhandled exception in a capture plugin would refuse
// the user's command.
//
// WHY THIS IS CHEAPER THAN THE CLAUDE HOOK. These hooks run in-process in Bun.
// The Claude `PreToolUse` hook has to keep its no-match path fork-free because
// it pays a process per Bash call; here there is no process at all, so the
// screen can simply run.
//
// SHAPE CONSTRAINT — the one most likely to burn an editor of this file.
// opencode's loader iterates EVERY export of the module and requires each to be
// a plugin function, throwing `TypeError("Plugin export is not a function")` on
// the first that is not. So this file has EXACTLY ONE top-level export and it is
// a function; every constant and helper lives inside the closure. A test
// (tests/test_harness_opencode.py) asserts that, because the failure happens
// inside Bun where pytest cannot see it.
//
// MUTATION RULE. opencode passes hook output through a fresh wrapper object and
// then reads its own binding. Assigning a FIELD (`output.args.command = x`,
// `output.env.PATH = y`) propagates; REASSIGNING the container
// (`output.args = {...}`) is dropped with no error and no warning. Only field
// assignment appears below, and a test greps for the other form.
//
// Environment:
//   LYPNING_LOG        log path (default $LYPNING_HOME/invocations.jsonl, and
//                      $HOME/.lypning/invocations.jsonl when that is unset)
//   LYPNING_CAPTURE=0  disable capture (hooks still run, doing nothing)
//   LYPNING_HOME       state dir, and where the python3 shim lives

export function lypning({ $, directory, worktree }) {
  const fs = require("node:fs");
  const os = require("node:os");
  const path = require("node:path");

  // The four screens from capture.py, ported verbatim. Deliberately BROADER
  // than precise: an over-match costs one wasted log line, a miss loses a
  // corpus entry forever. capture.looks_pythonish stays the precise filter and
  // harvest re-applies it, so a loose screen here can never put noise in the
  // corpus.
  const PYTHONISH = [
    /(?:^|[\s;&|(){}`$"'=])python[0-9.]*(?:\s|$)/,
    /(?:^|[\s;&|(){}`$])py\s+-c(?:\s|$)/,
    /(?:^|[\s;&|(){}`$])(?:uv|pipx|poetry|hatch|pdm|rye)\s+run(?:\s|$)/,
    /<<-?\s*['"]?(?:PY|PYTHON|PYEOF|EOFPY)\b/,
  ];

  // The routing paragraph, baked in so the hot path never spawns to fetch it.
  // A test asserts this equals assets/prompt/routing.md byte for byte — one
  // measured paragraph, not three variants that drifted.
  const ROUTING_PROMPT = [
    "Python one-liners in this session should be typed as `lypning -c '<program>'`",
    "rather than `python3 -c '<program>'`.",
    "",
    "lypning is a small, fast interpreter for the subset of Python that coding",
    "agents actually type. It tries every program first and runs it in roughly a",
    "fourteenth of CPython's time. If the program uses anything it does not",
    "implement it refuses cleanly — exit code 90 and a single",
    "`lypning: unsupported: <kind>: <detail>` line on stderr — and the program is",
    "re-run on real CPython, which costs one wasted process spawn on top of the",
    "full CPython price. That refusal is not an error, it needs no action from you,",
    "and the answer arrives one spawn later.",
    "",
    "You are not told which features the small interpreter implements. Use your",
    "judgement about what a deliberately minimal Python subset — one sized to the",
    "one-liners a coding agent actually types — would and would not have, and write",
    "programs that stay inside it. Correctness comes first: a wrong answer is far",
    "worse than a fallback, so never contort a program to avoid an import.",
  ].join("\n");

  const isPythonish = (s) =>
    typeof s === "string" && s.length > 0 && PYTHONISH.some((rx) => rx.test(s));

  const enabled = () => (process.env.LYPNING_CAPTURE || "1").trim() !== "0";

  // Must spell paths.log_path() identically or the two feeds land in different
  // files and harvest merges neither.
  const stateDir = () => {
    const home = (process.env.LYPNING_HOME || "").trim();
    if (home) return home;
    return path.join(os.homedir() || "", ".lypning");
  };
  const logPath = () => {
    const explicit = (process.env.LYPNING_LOG || "").trim();
    if (explicit) return explicit;
    return path.join(stateDir(), "invocations.jsonl");
  };
  const shimDir = () => path.join(stateDir(), "bin");

  // UTC, milliseconds, Z — the shim's format, so one log has one clock.
  const now = () => new Date().toISOString().replace(/(\.\d{3})\d*Z$/, "$1Z");

  // One appendFileSync of one COMPLETE line. That single-write atomicity is
  // what lets the shim and this plugin interleave as whole records instead of
  // shredding each other's.
  const append = (record) => {
    try {
      const target = logPath();
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.appendFileSync(target, JSON.stringify(record) + "\n", "utf8");
    } catch (_) {
      // A capture failure must never surface to the tool call.
    }
  };

  const sessionOf = (input) => {
    if (input && typeof input.sessionID === "string" && input.sessionID) return input.sessionID;
    const env = (process.env.LYPNING_SESSION_ID || "").trim();
    return env || null;
  };

  const cwd = (typeof worktree === "string" && worktree) ||
              (typeof directory === "string" && directory) ||
              process.cwd();

  // --- the PATH self-assertion ---------------------------------------------
  //
  // `shell.env` PATH injection was measured working on the bash tool, but it is
  // the kind of thing that stops working silently: opencode's V2 bash tool
  // passes no env at all and carries a TODO to add plugin env augmentation
  // "once V2 plugin hooks exist", and the `!command` session-shell path uses a
  // login shell whose parent PATH wins. On Linux and Windows it is unmeasured.
  //
  // So the plugin proves it rather than assuming it: once per instance, resolve
  // python3 under the same environment and check it landed in our shim dir. If
  // it did not, STOP injecting, write a note the Python side can find, and say
  // so on stderr. Degrading loudly is the whole point — an unreached shim and
  // an uninstalled shim have the same symptom, an empty log, and that symptom
  // has cost this project a day of capture before.
  let pathOk = null;      // null = not yet checked
  let injectPath = process.platform !== "win32";  // never guess on Windows
  let noted = false;

  const note = (detail, session) => {
    if (noted) return;
    noted = true;
    append({ kind: "note", ts: now(), session: session || null,
             host: "opencode", detail: detail });
    try {
      console.error("lypning: " + detail);
    } catch (_) { /* a plugin that cannot warn still must not throw */ }
  };

  const assertShimReached = async (session) => {
    if (pathOk !== null || !injectPath) return;
    pathOk = false;
    try {
      const dir = shimDir();
      const env = { ...process.env, PATH: dir + path.delimiter + (process.env.PATH || "") };
      const out = await $`command -v python3`.env(env).quiet().nothrow();
      const resolved = String(out.stdout || "").trim();
      if (resolved && resolved.startsWith(dir)) {
        pathOk = true;
      } else {
        injectPath = false;
        note("PATH shim not reached: python3 resolved to " +
             (resolved || "nothing") + " — the shim feed is inert this session",
             session);
      }
    } catch (_) {
      injectPath = false;
      note("PATH shim could not be verified; not injecting", session);
    }
  };

  return {
    // Fires for every tool. We only ever read.
    "tool.execute.before": async (input, output) => {
      try {
        if (!enabled()) return;
        // The exposed tool id is `bash` — opencode pins it that way for
        // compatibility with existing plugins and saved permissions. `shell` is
        // accepted only as a forward alias; matching `shell` alone would
        // observe nothing, forever, and say nothing about it.
        if (input.tool !== "bash" && input.tool !== "shell") return;
        const command = output && output.args ? output.args.command : null;
        if (!isPythonish(command)) return;
        append({
          kind: "bash_command",
          ts: now(),
          session: sessionOf(input),
          cwd: cwd,
          tool: input.tool,
          command: command,
          description: (output.args && output.args.description) || null,
          transcript: null,
          host: "opencode",
          run: input.callID || null,
        });
      } catch (_) {
        // Throwing here is opencode's DENY mechanism. Never.
      }
    },

    // Ground truth, free: harvest ignores {"kind":"exit"} entirely, so this
    // adds what the run actually did without touching the corpus contract and
    // without double-counting the program.
    "tool.execute.after": async (input, output) => {
      try {
        if (!enabled()) return;
        if (input.tool !== "bash" && input.tool !== "shell") return;
        const code = output && output.metadata ? output.metadata.exit : undefined;
        if (typeof code !== "number") return;
        append({ kind: "exit", ts: now(), session: sessionOf(input),
                 host: "opencode", run: input.callID || null, exit_code: code });
      } catch (_) { /* never */ }
    },

    // Put the shim on the child's PATH so the second feed — the one that proves
    // a program actually RAN — works here too. Field assignment only.
    "shell.env": async (input, output) => {
      try {
        if (!enabled()) return;
        await assertShimReached(sessionOf(input));
        if (!output || !output.env) return;
        output.env.LYPNING_HOST = "opencode";
        const session = sessionOf(input);
        if (session) output.env.LYPNING_SESSION_ID = session;
        if (!injectPath) return;
        output.env.PATH = shimDir() + path.delimiter + (process.env.PATH || "");
      } catch (_) { /* never */ }
    },

    // The agent-facing half. Capture is automatic; ROUTING IS NOT — the shim
    // logs and then execs real CPython, so it delivers no speedup at all.
    // Speed needs the agent to type `lypning`, and this is where it is told to,
    // attached to the exact tool it concerns.
    "tool.definition": async (input, output) => {
      try {
        if (input.toolID !== "bash") return;
        if (!output || typeof output.description !== "string") return;
        output.description = output.description + "\n\n" + ROUTING_PROMPT;
      } catch (_) { /* never */ }
    },

    // `event.type` is an OPEN string set, not a closed union — live runs emit
    // types absent from the published SDK types. Strict-equal the one string
    // needed, never switch exhaustively, never throw on an unknown type.
    event: async ({ event }) => {
      try {
        if (!event || event.type !== "session.idle") return;
        if (!enabled()) return;
        await $`lypning harvest --export --quiet`.quiet().nothrow();
      } catch (_) { /* never */ }
    },

    dispose: async () => {
      try {
        await $`lypning harvest --export --quiet`.quiet().nothrow();
      } catch (_) { /* never */ }
    },
  };
}
