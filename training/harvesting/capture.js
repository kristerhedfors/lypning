// Observation only: never modify tool arguments, routing, or permissions.
export async function capture() {
  const fs = await import("node:fs");
  const append = (record) => {
    try {
      if (process.env.LYPNING_CAPTURE === "0") return;
      const row = JSON.stringify({host: "opencode", ts: new Date().toISOString(), ...record});
      if (Buffer.byteLength(row) <= 4 * 1024 * 1024)
        fs.appendFileSync("/work/output/invocations.jsonl", row + "\n", {mode: 0o600});
    } catch (_) { /* A failed observation must never refuse a tool call. */ }
  };
  return {
    "tool.execute.before": async (input, output) => {
      try {
        append({kind: "tool_before", session: input.sessionID, run: input.callID,
          tool: input.tool, args: output.args, cwd: "/work/project", trainable: false});
      } catch (_) { /* never deny */ }
    },
    "tool.execute.after": async (input, output) => {
      try {
        append({kind: "tool_after", session: input.sessionID, run: input.callID,
          tool: input.tool, output, cwd: "/work/project", trainable: false});
      } catch (_) { /* never deny */ }
    },
  };
}
