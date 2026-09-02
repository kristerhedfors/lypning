// Types for the lypning Node addon.
//
// The one thing these declarations exist to make unmissable: `run()` returns
// and does not throw for a program lypning declines, so there is no `Error`
// type here to catch. `fallOnward` is a plain boolean field, and TypeScript
// will not let a harness pretend it is not there.

/** The tier that should run a program. These three strings and no others. */
export type Engine = 'lypning' | 'lypning-mp' | 'cpython';

/** `Status`, spelled. `'unsupported'` is a refusal — not a failure. */
export type StatusName = 'ok' | 'error' | 'unsupported' | 'busy' | 'panic';

export interface Route {
  /** Which interpreter should run it. */
  engine: Engine;
  /** The construct that pushed it past lypning (`'module'`, `'async'`), or `''`. */
  kind: string;
  /** That construct's detail (`'import re'`), or `''`. */
  detail: string;
  /**
   * Every module the program imports. SORTED AND DEDUPLICATED, not in source
   * order: the question is which modules a program needs, which has no order.
   * For the one import that decided the tier, read `detail`.
   */
  imports: string[];
}

export interface RunOptions {
  /** `sys.argv[1:]`. */
  args?: string[];
  /** `sys.argv[0]`. Unset gives CPython's `-c` shape, which is what a one-liner is. */
  filename?: string;
  /**
   * The program's stdin. Unset is an empty stream — the addon never reads fd 0.
   *
   * A string is sent as UTF-8; a `Uint8Array` (`Buffer` included) is sent as
   * its bytes. A wider typed array — `Uint16Array`, `Float64Array` — throws
   * rather than being reinterpreted, because there is no reading of it that is
   * obviously the one you meant.
   */
  stdin?: string | Uint8Array;
  /**
   * May the program touch the filesystem? Default `true`. `false` makes every
   * file operation a REFUSAL rather than a lie: the program is never told a
   * file is missing, you are told lypning would not run this — and since a
   * refusal is routable, you decide whether it goes to CPython.
   */
  filesystem?: boolean;
  /** Refuse once captured output passes this many bytes. `0`/unset is no limit. */
  outputLimit?: number;
  /**
   * Refuse once the program has taken this many statements or loop iterations.
   * `0`/unset is no limit.
   *
   * SET THIS if you run programs a language model wrote. `run()` executes on
   * the calling thread — node's event loop, unless you moved it — and a process
   * can be killed where a function call cannot. Passing the limit is a refusal
   * like any other, so the program still gets its answer from CPython.
   */
  stepLimit?: number;
}

export interface RunResult {
  /** `0` ok, `1` raised, `2` refused, `3` busy, `4` interpreter bug. */
  status: number;
  statusName: StatusName;
  /** The program's own exit code; `1` for a traceback, `90` for a refusal. */
  exitCode: number;
  /** Bytes, because a program's output is not always UTF-8. Empty after a refusal. */
  stdout: Buffer;
  /** The traceback, or the one `lypning: unsupported: <kind>: <detail>` line. */
  stderr: Buffer;
  /** The refusal's two halves: `'module'` / `'import re'`. `''` when it ran. */
  kind: string;
  detail: string;
  /**
   * Did the run pass the commit point — where staged output and staged file
   * writes are flushed and the run stops being reversible? `true` for any run
   * that finished, whether or not it touched a file; `false` for a refusal,
   * which is what makes the program safe to hand to CPython. Branch on
   * `fallOnward`, which already folds this in.
   */
  committed: boolean;
  /**
   * Run this program on CPython now. True for every outcome that is not the
   * program's own answer and left nothing behind: a refusal, a `busy` that ran
   * nothing, and a `panic` before the commit point. Never true for `ok` or
   * `error` — a traceback is as much of an answer as a printed line.
   * **This is the field to branch on.**
   */
  fallOnward: boolean;
}

/** The runtime version, e.g. `'0.1.0'`. */
export function version(): string;

/** The C ABI version this addon was built against. */
export function abiVersion(): number;

/** Which interpreter should run this? One parse, no execution. */
export function route(source: string): Route;

/**
 * Run it here, in this thread, with its output captured. Never spawns anything.
 *
 * Throws only for a caller type error (source that is not a string, `opts`
 * that is not an object, an option of the wrong type). A program outside the
 * subset comes back with `fallOnward === true` and is not an error.
 *
 * If reading `opts` throws — an accessor of yours, a `Proxy` trap — that
 * exception propagates and the program is NOT run, so a `catch` that falls
 * onward to CPython cannot execute it twice.
 */
export function run(source: string, opts?: RunOptions): RunResult;

/**
 * The dispatcher's own predicate, for a harness that chains OTHER interpreters
 * — `lypning-mp`, or a sandboxed `python3` in a child process. True for exit
 * 90, for a MemoryError, and for a traceback reported with exit 0.
 */
export function fallOnward(exitCode: number, stderr?: string | Uint8Array): boolean;

/** Which file `index.js` loaded. Diagnostic only. */
export const addonPath: string;
