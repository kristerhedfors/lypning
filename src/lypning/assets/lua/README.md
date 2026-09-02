# lypning for LuaJIT

**LuaJIT only: this binding is the `ffi` over the C ABI, and PUC Lua has no
`ffi`** (`require("lypning")` under PUC Lua stops with a message saying so).

Runs the bottom slice of agent-typed Python, the one-liners, *inside* this
LuaJIT process: no child process, no pipe, no serialisation. Everything else it
REFUSES, and the refusal is the design, not a failure.

The whole host, as `quickstart.lua` has it (runnable: `luajit quickstart.lua
'print(sum(range(10)))'`):

```lua
local lypning = require("lypning")

-- step_limit: a model wrote this program and it runs on THIS thread. No process to kill.
local r = lypning.run(src, { args = args, step_limit = 10000000 })

if r.fall_onward then
  -- A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
  io.stdout:flush()
  os.exit(run_on_python3(src, args))     -- your existing path, unchanged
else
  io.stdout:write(r.stdout)
  io.stderr:write(r.stderr)
  os.exit(r.exit_code)
end
```

`fall_onward` is true exactly when lypning did not answer *and left nothing
behind*: nothing printed, no file touched, no stdin consumed, which is what
makes the retry safe. A traceback is not that: it is the program's own answer,
exit code 1, and `fall_onward` is false. `run()` never errors for a refusal,
only for a caller type error; `r.kind` / `r.detail` say what it declined.
`route(src)` asks without running.

## No build step

There is nothing to compile. The module reads `lypning.h` at load, strips it to
its declarations and hands them to `ffi.cdef`, so the prototypes and the status
enum come from the real header rather than a copy of it; the only two things
restated are the `#define` constants (ABI version `1`, refusal exit `90`), and
the ABI one is checked against `lypning_abi_version()` before any other call. A
header edit that `cdef` cannot follow stops `require` with a message naming the
header, which is the loud failure a single source of truth is for.

It needs the library and the header, and looks for them where `lypning build
--lib` puts them and where a checkout builds them, in this order:

| what | first | then | then |
|---|---|---|---|
| library | `$LYPNING_LIB` | `$LYPNING_HOME/lib/liblypning.{dylib,so}` (default `~/.lypning/lib`) | `../rust/target/release-lib/` beside this file |
| header | | `$LYPNING_HOME/include/lypning.h` | `../include/lypning.h` beside this file |

`$LYPNING_LIB` is an instruction, not a hint: when it is set it is the only
candidate, and a value that is not a readable file is an error naming it, never
a fall back onto a library you did not name. With neither found, `require`
stops with one message that says `lypning build --lib` and lists every path it
tried. `lypning.library_path` and `lypning.header_path` say which files
answered.

The build+run line, from the repository root:

```sh
lypning build --lib && cd src/lypning/assets/lua && luajit quickstart.lua 'print(sum(range(10)))'
```

(a source checkout that has already built the crate needs only the second half.)

The quickstart's onward path is `os.execute("python3 -c ...")` with the source
and each argument single-quoted for the shell and stdin from `/dev/null`, so the
two paths agree on stdin (lypning sees an empty stream). It exits with the
child's own code, or `128 + n` when the child died of signal `n`, which is the
shell's convention; both shapes `os.execute` can return (LuaJIT's raw wait
status, or the `ok, "exit"|"signal", code` triple of a `LUA52COMPAT` build) are
decoded. A traceback is the program's own answer: exit 1, never retried.

## API

```lua
lypning.version()        -- the runtime's, e.g. "0.1.0"
lypning.abi_version()    -- what the loaded library answers; 1
lypning.STATUS           -- { ok = 0, error = 1, unsupported = 2, busy = 3, panic = 4 }, from the header's enum
lypning.STATUS_NAMES     -- the inverse
lypning.UNSUPPORTED_EXIT -- 90

lypning.route(src)       -- { engine, kind, detail, imports }: one parse, no execution
lypning.run(src, opts)   -- { status, status_name, exit_code, stdout, stderr,
                         --   kind, detail, committed, fall_onward }
lypning.fall_onward(exit_code, stderr)  -- the dispatcher's predicate, for chaining other engines
```

`opts`, all optional: `args` (a table of strings, `sys.argv[1:]`), `filename`
(`sys.argv[0]`; unset is CPython's `-c` shape), `stdin` (a string of bytes;
unset is an empty stream, never this process's fd 0), `filesystem` (`false`
turns every file operation into a refusal rather than a lie), `step_limit`
(refuse after this many statements or iterator advances; set it for anything a
model wrote, because there is no process to kill inside your own thread; it
bounds work, not time) and `output_limit` (refuse once captured output passes
this many bytes).

`stdout` and `stderr` are byte strings copied out of the result before its
handle is freed, by length, so a NUL in the program's output survives. Every
handle is freed inside the call that made it; `ffi.gc` is attached only as a
net for an error between allocation and the explicit free.

Source that is not UTF-8 never reaches the library: `run` answers it as a
refusal of kind `source` with `fall_onward` true, because that program too still
needs an answer and CPython gives it one with its own message.

## Neovim

This is what a plugin would call instead of
`vim.fn.system({"python3", "-c", src})`: the program runs in the editor's own
process, with its own thread's stack, and returns as a table. Two things follow.
`run()` is **synchronous on the calling thread**; a program that does not stop
is a hang in the editor, which is what `step_limit` is for and why the
quickstart sets one. And the program sees the editor's working directory and
environment, which are process-wide and not this module's to change; `filesystem
= false` refuses every file operation rather than sandboxing one. Add the same
branch on `fall_onward`, with `vim.fn.system` as the path it falls onward to.

## Tests

```sh
cd src/lypning/assets/lua && luajit test.lua
```

Plain asserts, non-zero exit on the first failure. The refusal contract comes
first: status `unsupported`, exit `90`, an empty stdout, exactly the one line,
`committed` false and `fall_onward` true. Then an ok run, a traceback not
retried, argv and filename, stdin (with a NUL, by length), the step limit, the
sandbox, the output limit, no state leaking between runs, `route` agreeing with
`run`, the dispatcher predicate, that the header-derived `cdef` loaded, the
absent-library message, and the five quickstart probes through the shell.

`study/hosts/run_lua.lua` is the same host driving the prompting study's
program set, so this binding is scored against the others rather than assumed
to agree with them.
