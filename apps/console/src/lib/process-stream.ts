import { spawn } from "node:child_process";

/** One decoded stdout/stderr line from a spawned process, or a final exit summary. */
export type ProcessLine =
  | { kind: "stdout"; line: string }
  | { kind: "stderr"; line: string }
  | { kind: "exit"; code: number | null };

/**
 * Runs one command to completion, invoking onLine for every stdout/stderr line as
 * it arrives (not buffered until exit) and finally once with the exit code. This is
 * the primitive the /api/runs/stream route uses to relay controlplane's and the
 * agent's stdout to the browser in real time -- see agent/main.py's module docstring
 * for why the agent's stdout is safe to stream line-by-line (every line is either a
 * `{"event": ...}` progress line or the final `{"root_cause": ...}` diagnosis).
 *
 * signal aborts (kills) the child process if the caller disconnects mid-run --
 * important here because these are real `docker compose run` invocations that
 * otherwise keep running (and holding a container) after the browser tab closes.
 */
export function runStreaming(
  command: string,
  args: string[],
  options: { cwd: string; signal?: AbortSignal },
  onLine: (event: ProcessLine) => void
): Promise<number | null> {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: options.cwd, signal: options.signal });

    let stdoutBuf = "";
    let stderrBuf = "";

    child.stdout.on("data", (chunk: Buffer) => {
      stdoutBuf += chunk.toString("utf-8");
      const lines = stdoutBuf.split("\n");
      stdoutBuf = lines.pop() ?? "";
      for (const line of lines) {
        if (line.length > 0) onLine({ kind: "stdout", line });
      }
    });

    child.stderr.on("data", (chunk: Buffer) => {
      stderrBuf += chunk.toString("utf-8");
      const lines = stderrBuf.split("\n");
      stderrBuf = lines.pop() ?? "";
      for (const line of lines) {
        if (line.length > 0) onLine({ kind: "stderr", line });
      }
    });

    child.on("error", (err) => reject(err));

    child.on("close", (code) => {
      if (stdoutBuf.length > 0) onLine({ kind: "stdout", line: stdoutBuf });
      if (stderrBuf.length > 0) onLine({ kind: "stderr", line: stderrBuf });
      onLine({ kind: "exit", code });
      resolve(code);
    });
  });
}
