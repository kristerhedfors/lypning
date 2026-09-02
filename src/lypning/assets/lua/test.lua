-- test.lua: the LuaJIT binding held to the contract. Plain asserts; a failed
-- one is a non-zero exit.
--
--   cd src/lypning/assets/lua && luajit test.lua
--
-- What a binding can get wrong that the library cannot: the copy out of a
-- handle before it is freed, the length of a buffer with a NUL in it, a
-- refusal turned into an error, and the header-derived cdef not having loaded
-- at all. The refusal contract comes first because it is the half that has
-- only ever broken silently.

package.path = (arg[0]:match("^(.*)[/\\]") or ".") .. "/?.lua;" .. package.path
local ffi = require("ffi")
local lypning = require("lypning")

local checks = 0
local function check(cond, what, got)
  checks = checks + 1
  if not cond then
    error(string.format("FAIL: %s%s", what, got == nil and "" or (" (got " .. string.format("%q", tostring(got)) .. ")")), 2)
  end
end

local function shell_quote(s) return "'" .. s:gsub("'", "'\\''") .. "'" end

-- The header-derived cdef loaded: the enum lives in ffi.C, not in this file.
check(ffi.C.LYPNING_UNSUPPORTED == 2, "the enum came from lypning.h", ffi.C.LYPNING_UNSUPPORTED)
check(lypning.STATUS.ok == 0 and lypning.STATUS.error == 1 and lypning.STATUS.unsupported == 2
      and lypning.STATUS.busy == 3 and lypning.STATUS.panic == 4, "STATUS mirrors the enum")
check(lypning.STATUS_NAMES[2] == "unsupported", "status names")
check(lypning.header_path:match("lypning%.h$") ~= nil, "header path", lypning.header_path)
check(lypning.library_path:match("liblypning%.") ~= nil, "library path", lypning.library_path)
check(lypning.abi_version() == lypning.ABI_VERSION, "abi version", lypning.abi_version())
check(type(lypning.version()) == "string" and #lypning.version() > 0, "version", lypning.version())

-- The refusal contract, the same five properties `lypning build --lib` pins:
-- status, exit 90, an empty stdout, exactly the one line, and a request to
-- be routed onward. Plus the kind/detail halves, and committed false.
local r = lypning.run("import subprocess")
check(r.status == lypning.STATUS.unsupported, "refusal: status", r.status_name)
check(r.status_name == "unsupported", "refusal: status_name", r.status_name)
check(r.exit_code == 90, "refusal: exit code", r.exit_code)
check(r.stdout == "", "refusal: stdout is empty", r.stdout)
check(r.stderr == "lypning: unsupported: module: import subprocess\n", "refusal: the one line", r.stderr)
check(r.kind == "module", "refusal: kind", r.kind)
check(r.detail == "import subprocess", "refusal: detail", r.detail)
check(r.committed == false, "refusal: not committed", r.committed)
check(r.fall_onward == true, "refusal: routes onward", r.fall_onward)

-- An ok run, in-process.
r = lypning.run("print(sum(range(10)))")
check(r.status == lypning.STATUS.ok and r.status_name == "ok", "ok: status", r.status_name)
check(r.exit_code == 0, "ok: exit code", r.exit_code)
check(r.stdout == "45\n", "ok: stdout", r.stdout)
check(r.stderr == "", "ok: stderr", r.stderr)
check(r.committed == true, "ok: committed", r.committed)
check(r.fall_onward == false, "ok: not routed onward", r.fall_onward)
check(r.kind == "" and r.detail == "", "ok: no refusal halves")

-- A traceback is the program's own answer: exit 1, never retried.
r = lypning.run("print(1/0)")
check(r.status == lypning.STATUS.error and r.status_name == "error", "traceback: status", r.status_name)
check(r.exit_code == 1, "traceback: exit code", r.exit_code)
check(r.stdout == "", "traceback: stdout", r.stdout)
check(r.stderr:find("ZeroDivisionError", 1, true) ~= nil, "traceback: stderr", r.stderr)
check(r.fall_onward == false, "traceback: NOT routed onward", r.fall_onward)
check(r.committed == true, "traceback: committed", r.committed)

-- sys.exit(n) is the program's exit code.
r = lypning.run("import sys; sys.exit(3)")
check(r.status == lypning.STATUS.ok and r.exit_code == 3, "sys.exit: exit code", r.exit_code)
check(r.fall_onward == false, "sys.exit: not routed onward")

-- argv, and the filename as argv[0].
r = lypning.run("import sys; print(sys.argv[1:])", { args = { "a", "b" } })
check(r.stdout == "['a', 'b']\n", "argv", r.stdout)
r = lypning.run("import sys; print(sys.argv[0])", { filename = "prog.py" })
check(r.stdout == "prog.py\n", "filename", r.stdout)
r = lypning.run("import sys; print(sys.argv[0])")
check(r.stdout == "-c\n", "no filename is the -c shape", r.stdout)

-- stdin is bytes with a length: a NUL inside must survive both directions.
r = lypning.run("import sys; print(sys.stdin.read().strip().upper())", { stdin = "hello\n" })
check(r.stdout == "HELLO\n", "stdin", r.stdout)
r = lypning.run("import sys; d = sys.stdin.read(); print(len(d))", { stdin = "a\0b" })
check(r.stdout == "3\n", "stdin with a NUL is passed by length", r.stdout)
r = lypning.run("import sys; sys.stdout.write('a\\x00b')")
check(r.stdout == "a\0b" and #r.stdout == 3, "stdout with a NUL is copied by length", r.stdout)
r = lypning.run("import sys; print(repr(sys.stdin.read()))")
check(r.stdout == "''\n", "unset stdin is an empty stream", r.stdout)

-- The step limit: a hang becomes a refusal, routable, with nothing written.
r = lypning.run("while True: pass", { step_limit = 1000 })
check(r.status == lypning.STATUS.unsupported, "step limit: refused", r.status_name)
check(r.fall_onward == true and r.stdout == "" and r.committed == false, "step limit: routes onward, wrote nothing")
check(r.kind ~= "", "step limit: has a kind", r.kind)
r = lypning.run("print(sum(range(10**9)))", { step_limit = 1000 })
check(r.status == lypning.STATUS.unsupported, "step limit: ticks on iterator advances too", r.status_name)

-- The sandbox: a denied file operation is a refusal, never a lie.
local probe = os.tmpname()
os.remove(probe)
r = lypning.run("open(" .. string.format("%q", probe) .. ", 'w').write('x')", { filesystem = false })
check(r.status == lypning.STATUS.unsupported and r.fall_onward, "sandbox: refused", r.status_name)
check(io.open(probe, "rb") == nil, "sandbox: nothing was written")
r = lypning.run("open(" .. string.format("%q", probe) .. ", 'w').write('x')")
check(r.status == lypning.STATUS.ok, "filesystem allowed by default", r.stderr)
check(io.open(probe, "rb") ~= nil, "filesystem allowed: the file exists")
os.remove(probe)

-- The output limit: refuse rather than grow a buffer in this address space.
r = lypning.run("print('a' * 10000)", { output_limit = 100 })
check(r.status == lypning.STATUS.unsupported and r.fall_onward, "output limit: refused", r.status_name)
check(r.stdout == "", "output limit: stdout is empty", #r.stdout)

-- Nothing leaks between runs: not a name, not stdin, not the commit flag.
lypning.run("x = 1")
r = lypning.run("print(x)")
check(r.status == lypning.STATUS.error and r.stderr:find("NameError", 1, true), "no leak: names", r.stderr)
lypning.run("import sys; sys.stdin.read()", { stdin = "leftover" })
r = lypning.run("import sys; print(repr(sys.stdin.read()))")
check(r.stdout == "''\n", "no leak: stdin", r.stdout)
lypning.run("print('committed')")
r = lypning.run("import subprocess")
check(r.committed == false and r.fall_onward == true, "no leak: the commit flag is not latched")
for _ = 1, 200 do
  r = lypning.run("print(sum(range(100)))")
  check(r.stdout == "4950\n", "repeatable", r.stdout)
end

-- Source that is not UTF-8 is a refusal of kind "source", not an error: that
-- program still needs an answer and CPython gives it one with its own message.
r = lypning.run("print('\xff')")
check(r.status == lypning.STATUS.unsupported and r.fall_onward, "not UTF-8: routes onward", r.status_name)
check(r.exit_code == 90 and r.kind == "source" and r.stdout == "", "not UTF-8: the refusal shape", r.kind)
check(r.stderr == "lypning: unsupported: source: not UTF-8\n", "not UTF-8: the one line", r.stderr)
r = lypning.run("print('a\0b')")
check(r.status == lypning.STATUS.unsupported and r.kind == "source", "a NUL in the source is a refusal", r.kind)

-- route() agrees with run(), and answers without running.
local ro = lypning.route("import subprocess")
check(ro.engine == "cpython" and ro.kind == "module" and ro.detail == "import subprocess", "route: refused program", ro.engine)
check(#ro.imports == 1 and ro.imports[1] == "subprocess", "route: imports", table.concat(ro.imports, ","))
ro = lypning.route("print(sum(range(10)))")
check(ro.engine == "lypning" and ro.kind == "" and ro.detail == "" and #ro.imports == 0, "route: accepted program", ro.engine)
ro = lypning.route("import sys, os, sys; print(1)")
check(table.concat(ro.imports, ",") == "os,sys", "route: imports sorted and deduplicated", table.concat(ro.imports, ","))
ro = lypning.route("print('\xff')")
check(ro.engine == "cpython" and ro.kind == "source", "route: not UTF-8 is cpython's", ro.engine)
for _, src in ipairs({ "print(1)", "import subprocess", "import sys; print(sys.argv)", "x = [i*i for i in range(5)]; print(x)" }) do
  local want_refused = lypning.route(src).engine ~= "lypning"
  check(lypning.run(src).fall_onward == want_refused, "route agrees with run: " .. src)
end

-- The dispatcher's predicate.
check(lypning.fall_onward(90, "lypning: unsupported: module: import re\n") == true, "fall_onward: exit 90")
check(lypning.fall_onward(1, "Traceback (most recent call last):\nZeroDivisionError: division by zero\n") == false, "fall_onward: a traceback with exit 1 is the answer")
check(lypning.fall_onward(0, "") == false, "fall_onward: exit 0")
check(lypning.fall_onward(3, "") == false, "fall_onward: sys.exit(3)")

-- Caller type errors are errors; nothing a program does is.
check(pcall(lypning.run, 42) == false, "run: a non-string program is a caller error")
check(pcall(lypning.run, "print(1)", { args = { 1 } }) == false, "run: a non-string arg is a caller error")

-- Honest degradation: the library absent, or named wrongly, is one clear
-- message that says what to run. Checked in a child so this process keeps
-- its loaded library. LYPNING_LIB is an instruction: a bad value is an error
-- naming it, never a fall back to the checkout's library.
local here = arg[0]:match("^(.*)[/\\]") or "."
local function child(envs, code)
  local cmd = envs .. " luajit -e " .. shell_quote(
    "package.path = " .. string.format("%q", here .. "/?.lua;") .. " .. package.path; " .. code) .. " 2>&1"
  local p = io.popen(cmd)
  local out = p:read("*a")
  p:close()
  return out
end
local out = child("LYPNING_LIB=/nonexistent/liblypning.so", "require('lypning')")
check(out:find("LYPNING_LIB", 1, true) and out:find("/nonexistent/liblypning.so", 1, true), "bad $LYPNING_LIB names itself", out)
check(out:find("lypning build --lib", 1, true), "bad $LYPNING_LIB names the build command", out)
check(not out:find("release%-lib"), "bad $LYPNING_LIB does not fall back to the checkout", out)
-- A copy of the module in a directory with no ../rust and no $LYPNING_HOME
-- library: the message names the build command and every place it looked.
local tmp = os.tmpname()
os.remove(tmp)
-- os.execute answers 0 (raw status) or true (LUA52COMPAT) for success.
local copied = os.execute("mkdir -p " .. shell_quote(tmp) .. " && cp " .. shell_quote(here .. "/lypning.lua") .. " " ..
      shell_quote(tmp) .. "/")
check(copied == 0 or copied == true, "copied the module aside", copied)
-- $LYPNING_LIB naming a directory, or an empty file, is "not a readable file"
-- too: fopen() on a directory succeeds and only the read fails.
out = child("LYPNING_LIB=" .. shell_quote(tmp), "require('lypning')")
check(out:find("LYPNING_LIB", 1, true) and out:find("not a readable file", 1, true), "$LYPNING_LIB=<dir> is named, not dlopen'd", out)
local function far(code)
  local p = io.popen("LYPNING_HOME=/nonexistent luajit -e " .. shell_quote(
    "package.path = " .. string.format("%q", tmp .. "/?.lua;") .. " .. package.path; " .. code) .. " 2>&1")
  local o = p:read("*a")
  p:close()
  return o
end
out = far("require('lypning')")
check(out:find("lypning build --lib", 1, true), "absent library or header names the build command", out)
check(out:find("/nonexistent/", 1, true), "absent: names where it looked", out)
os.execute("rm -rf " .. shell_quote(tmp))

-- The quickstart, end to end, through the shell: the five probes.
local qs = shell_quote(here .. "/quickstart.lua")
local function probe(src, extra)
  local errfile = os.tmpname()
  local p = io.popen("luajit " .. qs .. " " .. shell_quote(src) .. " " .. (extra or "") ..
                     " 2>" .. shell_quote(errfile) .. "; echo \"exit=$?\"")
  local o = p:read("*a")
  p:close()
  local e = io.open(errfile, "rb")
  local err = e and e:read("*a") or ""
  if e then e:close() end
  os.remove(errfile)
  return o, err
end
out = probe("print(sum(range(10)))")
check(out == "45\nexit=0\n", "quickstart: in-process", out)
out = probe("import subprocess; print(1)")
check(out == "1\nexit=0\n", "quickstart: refused, CPython answers once", out)
out = probe("import sys; print(sys.argv[1:])", "a b")
check(out == "['a', 'b']\nexit=0\n", "quickstart: argv", out)
local err
out, err = probe("print(1/0)")
check(out == "exit=1\n" and err:find("ZeroDivisionError", 1, true) and not err:find("Traceback.*Traceback"), "quickstart: a traceback is exit 1, not retried", out .. err)
out = probe("import sys; sys.exit(3)")
check(out == "exit=3\n", "quickstart: sys.exit(3)", out)
out = probe("import subprocess; print(1/0)")
check(out == "exit=1\n", "quickstart: CPython's own exit code comes back", out)
out = probe("import subprocess, sys; print(sys.argv[1:])", "\"it's\" b")
check(out == "[\"it's\", 'b']\nexit=0\n", "quickstart: shell quoting survives a quote", out)

print(string.format("lua: %d checks passed against %s (ABI %d, %s)", checks, lypning.library_path,
      lypning.abi_version(), lypning.version()))
