import { NextRequest } from "next/server";

import { REPO_ROOT } from "@/lib/paths";
import { runStreaming } from "@/lib/process-stream";
import { getLatestAlertForRun } from "@/lib/runs";

export const dynamic = "force-dynamic";

/**
 * Launches one scenario run live: `docker compose run --rm controlplane run <scenario>
 * --seed <seed>` (the same thing `make run-scenario` does), then once that finishes
 * and Postgres has the alert it fired, `docker compose run --rm agent --alert-name ...
 * --alert-condition ...`. Both processes' stdout gets streamed to the browser as
 * Server-Sent Events. The agent itself still only ever receives an alert name and
 * condition -- this route just makes the injection and investigation visible as they
 * happen instead of only after both finish.
 *
 * GET (not POST) because EventSource can only issue GET requests.
 */
export async function GET(req: NextRequest) {
  const scenario = req.nextUrl.searchParams.get("scenario");
  const seed = req.nextUrl.searchParams.get("seed") ?? "42";
  const enableRemediation = req.nextUrl.searchParams.get("remediate") === "true";

  if (!scenario) {
    return new Response("missing ?scenario=", { status: 400 });
  }

  const encoder = new TextEncoder();
  const abortController = new AbortController();
  req.signal.addEventListener("abort", () => abortController.abort());

  const stream = new ReadableStream({
    async start(controller) {
      const send = (event: string, data: unknown) => {
        controller.enqueue(
          encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`)
        );
      };

      let capturedRunId: string | null = null;

      try {
        send("phase", { phase: "injecting", scenario, seed });

        const controlplaneExit = await runStreaming(
          "docker",
          ["compose", "run", "--rm", "controlplane", "run", scenario, "--seed", seed],
          { cwd: REPO_ROOT, signal: abortController.signal },
          (event) => {
            if (event.kind === "exit") return;
            const line = event.line;
            if (event.kind === "stderr") {
              send("controlplane_stderr", { line });
              return;
            }
            try {
              const parsed = JSON.parse(line);
              const runId = parsed?.fields?.run_id ?? parsed?.run_id;
              if (typeof runId === "string" && !capturedRunId) {
                capturedRunId = runId;
              }
              send("controlplane_log", parsed);
            } catch {
              send("controlplane_log", { raw: line });
            }
          }
        );

        if (controlplaneExit !== 0) {
          send("error", {
            stage: "controlplane",
            message: `controlplane exited with code ${controlplaneExit}`,
          });
          controller.close();
          return;
        }

        if (!capturedRunId) {
          send("error", {
            stage: "controlplane",
            message: "controlplane finished but no run_id was found in its logs",
          });
          controller.close();
          return;
        }

        send("phase", { phase: "run_injected", run_id: capturedRunId });

        // Same lookup evaluation/harness.py does: read the alert *this run* fired
        // straight from Postgres rather than "whatever fired most recently".
        const alert = await getLatestAlertForRun(capturedRunId);
        if (!alert) {
          send("error", {
            stage: "alert_lookup",
            message: `no alert found in Postgres for run_id ${capturedRunId}`,
          });
          controller.close();
          return;
        }

        send("alert_fired", {
          run_id: capturedRunId,
          name: alert.name,
          condition: alert.condition,
          measured_value: alert.measured_value,
        });

        send("phase", { phase: "investigating", run_id: capturedRunId });

        const agentArgs = [
          "compose",
          "run",
          "--rm",
          "agent",
          "--alert-name",
          alert.name,
          "--alert-condition",
          alert.condition,
          "--run-id",
          capturedRunId,
        ];
        if (enableRemediation) agentArgs.push("--enable-remediation");

        let diagnosis: unknown = null;

        const agentExit = await runStreaming(
          "docker",
          agentArgs,
          { cwd: REPO_ROOT, signal: abortController.signal },
          (event) => {
            if (event.kind === "exit") return;
            const line = event.line;
            if (event.kind === "stderr") {
              send("agent_stderr", { line });
              return;
            }
            const trimmed = line.trim();
            if (!trimmed.startsWith("{")) {
              send("agent_stdout", { raw: line });
              return;
            }
            try {
              const parsed = JSON.parse(trimmed);
              if (parsed && typeof parsed === "object" && "root_cause" in parsed) {
                // Keep streaming (there could still be a remediation phase running
                // after this line), but remember it as the diagnosis.
                diagnosis = parsed;
                send("diagnosis", parsed);
              } else {
                send("agent_progress", parsed);
              }
            } catch {
              send("agent_stdout", { raw: line });
            }
          }
        );

        if (agentExit !== 0 && !diagnosis) {
          send("error", { stage: "agent", message: `agent exited with code ${agentExit}` });
          controller.close();
          return;
        }

        send("phase", { phase: "complete", run_id: capturedRunId });
      } catch (err) {
        send("error", { stage: "unknown", message: String(err) });
      } finally {
        controller.close();
      }
    },
    cancel() {
      abortController.abort();
    },
  });

  return new Response(stream, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
    },
  });
}
