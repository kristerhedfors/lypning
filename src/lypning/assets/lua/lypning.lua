-- lypning.lua: the lypning Python-subset runtime, embedded in a LuaJIT process.
--
-- Runs the bottom slice of agent-typed Python, the one-liners, inside THIS
-- process: no fork, no exec, no pipe, no serialisation. Everything else it
-- REFUSES, and the refusal is the design, not a failure.
--
--   local lypning = require("lypning")
--   local r = lypning.run(src, { args = argv, step_limit = 10000000 })
--   if r.fall_onward then run_on_python3(src)   -- lypning ran NOTHING of it
--   else                  use(r.stdout, r.exit_code) end
--
-- The one invariant this file holds is that a refusal is a VALUE. run() never
-- errors for a program, never prints, and never spawns; every outcome, including
-- "this Lua string was not UTF-8", arrives as a table whose fall_onward says
-- whether CPython should answer now. fall_onward is true exactly when lypning
-- declined and left nothing behind (nothing printed, no file touched, no stdin
-- consumed), which is what makes the retry safe. A traceback is not that: it is
-- the program's own answer, exit code 1, and is never routed onward.
--
-- LuaJIT only. This is the ffi over the unchanged C ABI in ../include/lypning.h,
-- and PUC Lua has no ffi. The prototypes and the status enum are not restated
-- here: the header is read at load, stripped to its declarations and handed to
-- ffi.cdef, so there is one source of truth and a header edit that this file
-- cannot follow fails loudly, naming the header, rather than drifting.
--
-- What errors, and what does not. require() errors when the library or the
-- header cannot be found or do not match each other, because nothing after
-- that could be trusted, and the message says what to run. run() and route()
-- error only for a caller type error (a non-string program, a non-table opts).
-- Nothing a PROGRAM does reaches error().
--
-- A run is confined to one thread, and a LuaJIT state is one thread, so run()
-- is synchronous on the caller's thread and returns when the program has. The
-- program sees this process's working directory and environment, which are
-- process-wide and not this module's to change.

local ok, ffi = pcall(require, "ffi")
if not ok then
  error("lypning.lua needs LuaJIT: require('ffi') failed (" .. tostring(ffi) ..
        "). PUC Lua has no ffi; run this under luajit.", 0)
end

local M = {}

-- The two #define constants of lypning.h, restated: the preprocessor lines are
-- stripped before cdef sees them. ABI_VERSION is asserted against the library
-- below, so this literal cannot silently disagree with the header for long.
M.ABI_VERSION = 1
M.UNSUPPORTED_EXIT = 90

M.BUILD_COMMAND = "lypning build --lib"

-- --- discovery ---------------------------------------------------------------

local function dirname_of_this_file()
  local src = debug.getinfo(1, "S").source
  if src:sub(1, 1) == "@" then
    return src:sub(2):match("^(.*)[/\\][^/\\]*$") or "."
  end
  return "."
end

local HERE = dirname_of_this_file()

-- A file that can be OPENED and READ: fopen() on a directory succeeds on macOS
-- and glibc and only the read fails, so open alone would let $LYPNING_LIB=/tmp
-- through to a dlopen error that names neither the variable nor the fix.
local function readable(path)
  local f = io.open(path, "rb")
  if not f then return false end
  local ok = f:read(0) ~= nil
  f:close()
  return ok
end

local function env(name)
  local v = os.getenv(name)
  if v == nil or v:match("^%s*$") then return nil end
  return v
end

local function state_dir()
  return env("LYPNING_HOME") or ((env("HOME") or env("USERPROFILE") or ".") .. "/.lypning")
end

-- liblypning.dylib on macOS, liblypning.so everywhere else: the same rule
-- embed.shared_library_name() holds for the Python side.
local LIB_NAME = (ffi.os == "OSX") and "liblypning.dylib" or "liblypning.so"

-- $LYPNING_LIB, then $LYPNING_HOME/lib, then the checkout's own build tree:
-- the same order and the same reasoning as embed.find_library(). An explicit
-- override wins, an installed artefact beats a build tree, and a build tree is
-- still better than nothing in a checkout that has not run `lypning build
-- --lib` yet.
local function find_library()
  local explicit = env("LYPNING_LIB")
  if explicit then
    -- An instruction, not a hint: when it is set it is the ONLY candidate. An
    -- override that silently fell back to discovery would load a library the
    -- user did not name, most likely the stale one they set it to bypass.
    if readable(explicit) then return explicit end
    error("lypning: $LYPNING_LIB points at " .. explicit ..
          ", which is not a readable file. Unset it to search the usual places, " ..
          "or point it at a `" .. M.BUILD_COMMAND .. "` library.", 0)
  end
  local tried = {
    state_dir() .. "/lib/" .. LIB_NAME,
    HERE .. "/../rust/target/release-lib/" .. LIB_NAME,
  }
  for _, p in ipairs(tried) do
    if readable(p) then return p end
  end
  error("lypning: " .. LIB_NAME .. " is not built. Run `" .. M.BUILD_COMMAND ..
        "`, or set $LYPNING_LIB to a built library. Looked in:\n    " ..
        table.concat(tried, "\n    "), 0)
end

-- The header is found the same way, minus the override: $LYPNING_HOME/include
-- is where `lypning build --lib` installs it, ../include is the checkout.
local function find_header()
  local tried = {
    state_dir() .. "/include/lypning.h",
    HERE .. "/../include/lypning.h",
  }
  for _, p in ipairs(tried) do
    if readable(p) then return p end
  end
  error("lypning: lypning.h not found. Run `" .. M.BUILD_COMMAND ..
        "`, which installs it. Looked in:\n    " .. table.concat(tried, "\n    "), 0)
end

-- --- the header, as ffi.cdef sees it ------------------------------------------

-- lypning.h minus what cdef cannot take: comments, preprocessor lines (the
-- guards, the includes, the two #defines) and the extern "C" braces. What is
-- left is the enum and the prototypes, which is exactly the part that must not
-- be restated by hand.
local function declarations(text)
  text = "\n" .. text .. "\n"
  text = text:gsub("/%*.-%*/", "")             -- block comments
  text = text:gsub("//[^\n]*", "")              -- line comments
  text = text:gsub("\n%s*#[^\n]*", "\n")        -- preprocessor lines
  text = text:gsub('extern%s+"C"%s*{', "")      -- the opening brace, and
  text = text:gsub("\n%s*}%s*\n", "\n")         -- its closing one, alone on a line
  return text
end

local function load_header(path)
  local f = io.open(path, "rb")
  if not f then error("lypning: cannot read " .. path, 0) end
  local text = f:read("*a")
  f:close()
  local decls = declarations(text)
  -- Two symbols that every version of the ABI has. Their absence means the
  -- stripping above ate the declarations, and cdef would then succeed on an
  -- empty string and every call after it would fail one at a time.
  if not (decls:find("lypning_run", 1, true) and decls:find("LYPNING_UNSUPPORTED", 1, true)) then
    error("lypning: " .. path .. " did not yield the lypning declarations after " ..
          "stripping; the header's shape changed and lypning.lua must follow it.", 0)
  end
  local cdef_ok, why = pcall(ffi.cdef, decls)
  if not cdef_ok then
    error("lypning: ffi.cdef rejected the declarations read from " .. path ..
          ": " .. tostring(why) .. ". A header edit that cdef cannot parse must " ..
          "be followed here, not worked around.", 0)
  end
end

-- --- load ----------------------------------------------------------------------

M.header_path = find_header()
load_header(M.header_path)
M.library_path = find_library()

local load_ok, lib = pcall(ffi.load, M.library_path)
if not load_ok then
  error("lypning: cannot load " .. M.library_path .. ": " .. tostring(lib) ..
        ". Rebuild it for this machine with `" .. M.BUILD_COMMAND .. "`.", 0)
end

-- What the LIBRARY says, checked against what this file remembers, before any
-- other call: a header and a library that have drifted apart are caught at
-- the door rather than one wrong answer at a time.
do
  local got = tonumber(lib.lypning_abi_version())
  if got ~= M.ABI_VERSION then
    error("lypning: " .. M.library_path .. " speaks ABI " .. tostring(got) ..
          ", this binding speaks " .. M.ABI_VERSION .. "; rebuild one of them (`" ..
          M.BUILD_COMMAND .. "`) so they agree.", 0)
  end
end

-- The status values, read from the enum the header declared rather than typed
-- again. ffi.C resolves cdef'd constants regardless of which library holds
-- the functions.
M.STATUS = {
  ok = ffi.C.LYPNING_OK,
  error = ffi.C.LYPNING_ERROR,
  unsupported = ffi.C.LYPNING_UNSUPPORTED,
  busy = ffi.C.LYPNING_BUSY,
  panic = ffi.C.LYPNING_PANIC,
}
M.STATUS_NAMES = {}
for name, value in pairs(M.STATUS) do M.STATUS_NAMES[value] = name end

-- --- helpers -------------------------------------------------------------------

local function str(p)
  -- Every `const char *` accessor answers "" and never NULL, but a binding that
  -- relied on that would fault on the day it stopped being true.
  if p == nil then return "" end
  return ffi.string(p)
end

local size_out = ffi.new("size_t[1]")

-- Copy a result buffer out, BEFORE the handle is freed: the pointer belongs to
-- the handle and dies with it. ffi.string with a length is one memcpy and is
-- NUL-safe, which matters because a program's stdout is whatever it printed.
local function bytes(accessor, handle)
  size_out[0] = 0
  local p = accessor(handle, size_out)
  local n = tonumber(size_out[0])
  if p == nil or n == 0 then return "" end
  return ffi.string(p, n)
end

-- The shape a refusal has when the ABI will not even build a handle for the
-- source: a Lua string that is not UTF-8. The same shape the runtime gives a
-- NUL byte in the source, kind "source", so a host branches on it like any
-- other refusal. That program too still needs an answer, and CPython can give
-- it one with its own message.
local NOT_UTF8 = "not UTF-8"

-- --- the API -------------------------------------------------------------------

-- The runtime version, e.g. "0.1.0".
function M.version()
  return str(lib.lypning_version())
end

-- What the loaded library answers, which after the check above is also
-- ABI_VERSION.
function M.abi_version()
  return tonumber(lib.lypning_abi_version())
end

-- Which interpreter should run src, at the cost of one parse and no execution.
-- Returns { engine = "lypning" | "lypning-mp" | "cpython", kind = "", detail =
-- "", imports = { ... } }. kind/detail name the construct that pushed it past
-- lypning ("module", "import re"); imports is every module the program
-- imports, sorted and deduplicated.
function M.route(src)
  if type(src) ~= "string" then
    error("lypning.route: the program must be a string, got " .. type(src), 2)
  end
  local h = lib.lypning_route_new(src, #src)
  if h == nil then
    return { engine = "cpython", kind = "source", detail = NOT_UTF8, imports = {} }
  end
  ffi.gc(h, lib.lypning_route_free)
  local imports = {}
  local n = tonumber(lib.lypning_route_import_count(h))
  for i = 0, n - 1 do
    imports[#imports + 1] = str(lib.lypning_route_import(h, i))
  end
  local out = {
    engine = str(lib.lypning_route_engine(h)),
    kind = str(lib.lypning_route_kind(h)),
    detail = str(lib.lypning_route_detail(h)),
    imports = imports,
  }
  -- Freed here, in this call, with the gc hook cleared first so the collector
  -- does not free it a second time. The hook above is a net for an error
  -- between _new and here, not the mechanism.
  ffi.gc(h, nil)
  lib.lypning_route_free(h)
  return out
end

-- Run src in this thread, capturing its output. Never spawns anything, never
-- errors for a program. opts, all optional:
--
--   args         sys.argv[1:], a table of strings
--   filename     sys.argv[0]; unset gives CPython's `-c` shape
--   stdin        the program's whole stdin, as a string of bytes; unset is an
--                empty stream, never this process's fd 0
--   filesystem   false turns every file operation into a REFUSAL rather than a
--                lie; the default is true
--   step_limit   refuse after this many statements or iterator advances. SET IT
--                for anything a model wrote: there is no process to kill inside
--                your own thread, so `while True: pass` with no limit is a hang
--                with no way back. It bounds work, not time. 0 is no limit.
--   output_limit refuse once captured output passes this many bytes. 0 is none.
--
-- Returns { status, status_name, exit_code, stdout, stderr, kind, detail,
-- committed, fall_onward }. stdout/stderr are byte strings. fall_onward is THE
-- field to branch on.
function M.run(src, opts)
  if type(src) ~= "string" then
    error("lypning.run: the program must be a string, got " .. type(src), 2)
  end
  opts = opts or {}
  if type(opts) ~= "table" then
    error("lypning.run: opts must be a table, got " .. type(opts), 2)
  end
  local q = lib.lypning_request_new(src, #src)
  if q == nil then
    return {
      status = M.STATUS.unsupported,
      status_name = "unsupported",
      exit_code = M.UNSUPPORTED_EXIT,
      stdout = "",
      stderr = "lypning: unsupported: source: " .. NOT_UTF8 .. "\n",
      kind = "source",
      detail = NOT_UTF8,
      committed = false,
      fall_onward = true,
    }
  end
  ffi.gc(q, lib.lypning_request_free)
  if opts.filename ~= nil then
    lib.lypning_request_set_filename(q, opts.filename, #opts.filename)
  end
  for _, a in ipairs(opts.args or {}) do
    if type(a) ~= "string" then
      error("lypning.run: every entry of opts.args must be a string", 2)
    end
    lib.lypning_request_add_arg(q, a, #a)
  end
  if opts.stdin ~= nil then
    lib.lypning_request_set_stdin(q, opts.stdin, #opts.stdin)
  end
  if opts.filesystem == false then
    lib.lypning_request_set_filesystem(q, 0)
  end
  lib.lypning_request_set_step_limit(q, opts.step_limit or 0)
  lib.lypning_request_set_output_limit(q, opts.output_limit or 0)

  local r = lib.lypning_run(q)
  if r == nil then
    -- Only for a NULL request, which cannot be the case here. Not a refusal
    -- and not the program's: the library itself misbehaved.
    ffi.gc(q, nil)
    lib.lypning_request_free(q)
    error("lypning.run: lypning_run returned NULL", 0)
  end
  ffi.gc(r, lib.lypning_result_free)
  local status = tonumber(lib.lypning_result_status(r))
  local out = {
    status = status,
    status_name = M.STATUS_NAMES[status] or ("status-" .. tostring(status)),
    exit_code = tonumber(lib.lypning_result_exit_code(r)),
    -- Copied out here, before the two frees below take the pointers back.
    stdout = bytes(lib.lypning_result_stdout, r),
    stderr = bytes(lib.lypning_result_stderr, r),
    kind = str(lib.lypning_result_kind(r)),
    detail = str(lib.lypning_result_detail(r)),
    committed = lib.lypning_result_committed(r) ~= 0,
    fall_onward = lib.lypning_result_should_fall_onward(r) ~= 0,
  }
  ffi.gc(r, nil)
  lib.lypning_result_free(r)
  ffi.gc(q, nil)
  lib.lypning_request_free(q)
  return out
end

-- The dispatcher's own predicate, for a host that chains OTHER interpreters
-- too (lypning-mp, or a sandboxed python3) and has only their exit code and
-- stderr to go on. True for exit 90, for a MemoryError, and for a traceback
-- reported with exit 0; deliberately false for an ordinary non-zero exit with
-- a traceback, which is very often the program's own correct answer.
function M.fall_onward(exit_code, stderr)
  stderr = stderr or ""
  return lib.lypning_fall_onward(exit_code, stderr, #stderr) ~= 0
end

return M
