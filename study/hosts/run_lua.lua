-- run_lua.lua: the LuaJIT host, over the ffi binding in assets/lua.
--
-- Same walk as the C, C++, Rust, Node and Python drivers, and no fall-onward
-- for the same reason: this counts what the subset itself takes, and a driver
-- that quietly answered from CPython would report a coverage the subset does
-- not have.
--
-- It logs each run to $LYPNING_LOG in the shim's own record shape. An ffi call
-- spawns no interpreter, so neither of lypning's capture feeds can see it; see
-- study/hosts/capture.h for why that is the host's job. The JSON is written by
-- hand because LuaJIT ships no JSON module and this project adds no
-- dependencies (CLAUDE.md invariant 6).
--
--   luajit study/hosts/run_lua.lua <hostset-dir>

local here = arg[0]:match("^(.*)[/\\]") or "."
package.path = here .. "/../../src/lypning/assets/lua/?.lua;" .. package.path
local ffi = require("ffi")
local lypning = require("lypning")

-- Pure LuaJIT has no chdir, no getcwd, no pid and no sub-second clock; the
-- ffi has all four one declaration away. gettimeofday's struct differs by
-- platform in the width of tv_usec, so it is declared per OS rather than
-- read through padding.
ffi.cdef[[
int chdir(const char *path);
char *getcwd(char *buf, size_t size);
int getpid(void);
]]
if ffi.os == "OSX" then
  ffi.cdef[[ struct lyp_timeval { int64_t tv_sec; int32_t tv_usec; }; ]]
else
  ffi.cdef[[ struct lyp_timeval { long tv_sec; long tv_usec; }; ]]
end
ffi.cdef[[ int gettimeofday(struct lyp_timeval *tv, void *tz); ]]

local function now_ms()
  local tv = ffi.new("struct lyp_timeval")
  ffi.C.gettimeofday(tv, nil)
  return tonumber(tv.tv_sec) * 1000 + math.floor(tonumber(tv.tv_usec) / 1000)
end

local function getcwd()
  local buf = ffi.new("char[4096]")
  local p = ffi.C.getcwd(buf, 4096)
  return p ~= nil and ffi.string(p) or "."
end

local LOG = os.getenv("LYPNING_LOG") or ""
local SESSION = os.getenv("LYPNING_STUDY_SESSION") or ""

-- Enough of RFC 8259 for a record: quotes, backslashes and control characters
-- escaped; everything else, the program's UTF-8 included, passed through.
local ESCAPES = { ['"'] = '\\"', ["\\"] = "\\\\", ["\n"] = "\\n", ["\r"] = "\\r", ["\t"] = "\\t",
                  ["\b"] = "\\b", ["\f"] = "\\f" }
local function json_string(s)
  return '"' .. s:gsub('[%c"\\]', function(c)
    return ESCAPES[c] or string.format("\\u%04x", c:byte())
  end) .. '"'
end

local function json_list(t)
  local parts = {}
  for i, v in ipairs(t) do parts[i] = json_string(v) end
  return "[" .. table.concat(parts, ",") .. "]"
end

local function capture(host, program, args, exit_code, wall_ms)
  if LOG == "" then return end
  local rec = "{" .. table.concat({
    '"kind":"python_invocation"',
    '"ts":' .. json_string(os.date("!%Y-%m-%dT%H:%M:%SZ")),
    '"session":' .. (SESSION ~= "" and json_string(SESSION) or "null"),
    '"shim":' .. json_string(host),
    '"pid":' .. tostring(ffi.C.getpid()),
    '"program":' .. json_string(program),
    '"module":null',
    '"script":null',
    '"argv_tail":' .. json_list(args),
    '"stdin_pipe":true',
    '"stdin_kind":"bytes"',
    '"exit_code":' .. tostring(exit_code),
    '"wall_ms":' .. tostring(wall_ms),
  }, ",") .. "}"
  -- Best-effort, exactly like the shim: a lost sighting, never a failed run.
  local fh = io.open(LOG, "ab")
  if fh then
    fh:write(rec, "\n")
    fh:close()
  end
end

local function read_file(path)
  local f = io.open(path, "rb")
  if not f then return nil end
  local s = f:read("*a")
  f:close()
  return s
end

local function shell_quote(s) return "'" .. s:gsub("'", "'\\''") .. "'" end

local root = arg[1]
if not root then
  io.stderr:write("usage: luajit run_lua.lua <hostset-dir>\n")
  os.exit(2)
end

-- The set is a directory of directories (study/hosts/prepare.py), listed
-- through `ls` because pure LuaJIT has no readdir and dirent's layout is not
-- the same on two platforms. ls sorts, which is the order every host walks.
local names = {}
local ls = io.popen("ls -1 " .. shell_quote(root))
for name in ls:lines() do names[#names + 1] = name end
ls:close()

local ran, refused, other, n = 0, 0, 0, 0
local home = getcwd()
for _, name in ipairs(names) do
  local d = root .. "/" .. name
  local program = read_file(d .. "/program.py")
  if program then
    n = n + 1
    local stdin = read_file(d .. "/stdin") or ""
    local args = {}
    for line in (read_file(d .. "/args") or ""):gmatch("[^\n]+") do args[#args + 1] = line end
    -- The program runs in THIS process; give it the entry directory, where
    -- prepare.py put the fixtures it was written against.
    local moved = ffi.C.chdir(d) == 0
    local t0 = now_ms()
    local out = lypning.run(program, { args = args, stdin = stdin,
                                       step_limit = 200000000, output_limit = 1048576 })
    local ms = now_ms() - t0
    if moved then ffi.C.chdir(home) end
    if out.status == lypning.STATUS.ok then
      ran = ran + 1
    elseif out.status == lypning.STATUS.unsupported then
      refused = refused + 1
    else
      other = other + 1
    end
    capture("lua-embed", program, args, out.exit_code, ms)
  end
end

print(string.format("lua-embed    %d programs: %d ran, %d refused, %d other", n, ran, refused, other))
