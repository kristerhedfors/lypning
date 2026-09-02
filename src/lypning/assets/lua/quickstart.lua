#!/usr/bin/env luajit
-- quickstart.lua: the smallest correct lypning host, in LuaJIT. No build step
-- of its own; the library is the one `lypning build --lib` makes. Build and run:
--   lypning build --lib && cd src/lypning/assets/lua && luajit quickstart.lua 'print(sum(range(10)))'
-- Usage: luajit quickstart.lua "<python source>" [args...]   (args become sys.argv[1:])

package.path = (arg[0]:match("^(.*)[/\\]") or ".") .. "/?.lua;" .. package.path
local lypning = require("lypning")

local src = arg[1]
if not src then
  io.stderr:write('usage: luajit quickstart.lua "<python source>" [args...]\n')
  os.exit(2)
end
local args = {}
for i = 2, #arg do args[#args + 1] = arg[i] end

-- step_limit: a model wrote this program and it runs on THIS thread. No process to kill.
local r = lypning.run(src, { args = args, step_limit = 10000000 })

if r.fall_onward then
  -- A refusal is not an error: lypning ran none of it and wrote nothing, so CPython runs it once.
  local function sq(s) return "'" .. s:gsub("'", "'\\''") .. "'" end
  local cmd = "python3 -c " .. sq(src)
  for _, a in ipairs(args) do cmd = cmd .. " " .. sq(a) end
  io.stdout:flush()
  local status, how, code = os.execute(cmd .. " </dev/null")
  if type(status) == "number" then
    how = (status > 0 and status < 256) and "signal" or "exit"
    code = status >= 256 and math.floor(status / 256) or status % 128
  end
  os.exit(how == "signal" and 128 + code or code)
else
  io.stdout:write(r.stdout)
  io.stderr:write(r.stderr)
  os.exit(r.exit_code)
end
