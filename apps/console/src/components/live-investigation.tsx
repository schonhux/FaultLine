"use client";

import { useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  Radio,
  XCircle,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { approveRemediation, denyRemediation } from "@/app/remediations/actions";

interface ScenarioOption {
  id: string;
  title: string;
}

interface TimelineEntry {
  id: number;
  kind: string;
  at: string;
  data: Record<string, unknown>;
}

interface RemediationProposal {
  approvalId: string;
  tool: string;
  target: string;
  justification: string;
  status: string;
  decision?: "approved" | "denied_by_human";
}

type RunStatus = "idle" | "running" | "complete" | "error";

function nowIso() {
  return new Date().toISOString();
}

export function LiveInvestigation({
  scenarios,
  defaultScenarioId,
}: {
  scenarios: ScenarioOption[];
  defaultScenarioId?: string;
}) {
  const [scenarioId, setScenarioId] = useState(
    defaultScenarioId ?? scenarios[0]?.id ?? ""
  );
  const [seed, setSeed] = useState("42");
  const [enableRemediation, setEnableRemediation] = useState(false);
  const [status, setStatus] = useState<RunStatus>("idle");
  const [timeline, setTimeline] = useState<TimelineEntry[]>([]);
  const [alert, setAlert] = useState<{ name: string; condition: string } | null>(null);
  const [diagnosis, setDiagnosis] = useState<Record<string, unknown> | null>(null);
  const [proposal, setProposal] = useState<RemediationProposal | null>(null);
  const [deciding, setDeciding] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const idCounter = useRef(0);
  const pendingProposeArgs = useRef<{ target: string; justification: string; tool: string } | null>(
    null
  );

  useEffect(() => {
    return () => eventSourceRef.current?.close();
  }, []);

  function pushEntry(kind: string, data: Record<string, unknown>) {
    idCounter.current += 1;
    setTimeline((prev) => [...prev, { id: idCounter.current, kind, at: nowIso(), data }]);
  }

  function start() {
    if (!scenarioId) return;
    eventSourceRef.current?.close();
    setStatus("running");
    setTimeline([]);
    setAlert(null);
    setDiagnosis(null);
    setProposal(null);
    pendingProposeArgs.current = null;

    const params = new URLSearchParams({
      scenario: scenarioId,
      seed,
      remediate: String(enableRemediation),
    });
    const es = new EventSource(`/api/runs/stream?${params.toString()}`);
    eventSourceRef.current = es;

    es.addEventListener("phase", (e) => pushEntry("phase", JSON.parse(e.data)));
    es.addEventListener("controlplane_log", (e) => pushEntry("controlplane_log", JSON.parse(e.data)));
    es.addEventListener("controlplane_stderr", (e) => pushEntry("stderr", JSON.parse(e.data)));
    es.addEventListener("agent_stderr", (e) => pushEntry("stderr", JSON.parse(e.data)));
    es.addEventListener("agent_stdout", (e) => pushEntry("agent_stdout", JSON.parse(e.data)));

    es.addEventListener("alert_fired", (e) => {
      const data = JSON.parse(e.data);
      setAlert({ name: data.name, condition: data.condition });
      pushEntry("alert_fired", data);
    });

    es.addEventListener("agent_progress", (e) => {
      const data = JSON.parse(e.data);
      pushEntry("agent_progress", data);

      if (data.node === "remediate") {
        const proposeCall = (data.summary?.tool_calls ?? []).find((c: { name: string }) =>
          c.name?.startsWith("propose_")
        );
        if (proposeCall) {
          pendingProposeArgs.current = {
            target: proposeCall.args?.target,
            justification: proposeCall.args?.justification,
            tool: proposeCall.name,
          };
        }
      }

      if (data.node === "remediation_tools") {
        const result = data.summary?.last_tool_result;
        if (result?.status === "pending_approval" && result?.approval_id && pendingProposeArgs.current) {
          setProposal({
            approvalId: result.approval_id,
            tool: pendingProposeArgs.current.tool,
            target: pendingProposeArgs.current.target,
            justification: pendingProposeArgs.current.justification,
            status: "pending_approval",
          });
        }
      }
    });

    es.addEventListener("diagnosis", (e) => {
      setDiagnosis(JSON.parse(e.data));
    });

    es.addEventListener("error", (e) => {
      const data = (e as MessageEvent).data ? JSON.parse((e as MessageEvent).data) : null;
      if (data) pushEntry("error", data);
      setStatus("error");
      es.close();
    });

    // The route always ends with a "phase: complete" event before closing, so this
    // native error listener only fires on an actual dropped connection, not a clean
    // finish -- but guard status either way.
    es.onerror = () => {
      setStatus((current) => (current === "running" ? "error" : current));
      es.close();
    };

    es.addEventListener("phase", (e) => {
      const data = JSON.parse(e.data);
      if (data.phase === "complete") {
        setStatus("complete");
        es.close();
      }
    });
  }

  async function decide(decision: "approved" | "denied_by_human") {
    if (!proposal) return;
    setDeciding(true);
    try {
      if (decision === "approved") {
        await approveRemediation(proposal.approvalId, "console-live-view");
      } else {
        await denyRemediation(proposal.approvalId, "console-live-view");
      }
      setProposal({ ...proposal, decision });
    } finally {
      setDeciding(false);
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Launch a run</CardTitle>
          <CardDescription>
            Requires <code className="text-foreground">make up</code> (and{" "}
            <code className="text-foreground">make remediation-up</code> if you enable
            remediation) already running.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap items-end gap-4">
          <div className="space-y-1.5">
            <Label>Scenario</Label>
            <Select value={scenarioId} onValueChange={setScenarioId}>
              <SelectTrigger className="w-64">
                <SelectValue placeholder="Choose a scenario" />
              </SelectTrigger>
              <SelectContent>
                {scenarios.map((s) => (
                  <SelectItem key={s.id} value={s.id}>
                    {s.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="seed">Seed</Label>
            <Input
              id="seed"
              className="w-24"
              value={seed}
              onChange={(e) => setSeed(e.target.value)}
            />
          </div>
          <label className="flex items-center gap-2 pb-2 text-sm">
            <input
              type="checkbox"
              className="accent-primary size-4"
              checked={enableRemediation}
              onChange={(e) => setEnableRemediation(e.target.checked)}
            />
            Enable remediation
          </label>
          <Button onClick={start} disabled={status === "running" || !scenarioId}>
            {status === "running" ? <Loader2 className="animate-spin" /> : <Radio />}
            {status === "running" ? "Running…" : "Launch"}
          </Button>
        </CardContent>
      </Card>

      {status !== "idle" && (
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">Timeline</CardTitle>
              <StatusPill status={status} />
            </div>
            {alert && (
              <CardDescription>
                Alert: <span className="font-mono">{alert.name}</span> — {alert.condition}
              </CardDescription>
            )}
          </CardHeader>
          <CardContent>
            <div className="max-h-96 space-y-2 overflow-y-auto text-sm">
              {timeline.map((entry) => (
                <TimelineRow key={entry.id} entry={entry} />
              ))}
              {timeline.length === 0 && (
                <p className="text-muted-foreground">Waiting for the first event…</p>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {proposal && (
        <Card className="border-warning/40">
          <CardHeader>
            <CardTitle className="text-base">
              Remediation proposed: {proposal.tool} → {proposal.target}
            </CardTitle>
            <CardDescription>{proposal.justification}</CardDescription>
          </CardHeader>
          <CardContent className="flex items-center gap-3">
            {proposal.decision ? (
              <Badge variant={proposal.decision === "approved" ? "success" : "destructive"}>
                {proposal.decision === "approved" ? "Approved" : "Denied"} — the agent&apos;s
                execute_remediation call will pick this up within a few seconds
              </Badge>
            ) : (
              <>
                <Button variant="destructive" disabled={deciding} onClick={() => decide("denied_by_human")}>
                  <XCircle /> Deny
                </Button>
                <Button variant="success" disabled={deciding} onClick={() => decide("approved")}>
                  <CheckCircle2 /> Approve
                </Button>
              </>
            )}
          </CardContent>
        </Card>
      )}

      {diagnosis && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Diagnosis</CardTitle>
            <CardDescription>
              {String(diagnosis.affected_service)} · confidence{" "}
              {Math.round(Number(diagnosis.confidence ?? 0) * 100)}%
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <p className="font-medium">{String(diagnosis.root_cause)}</p>
            <p className="text-muted-foreground">{String(diagnosis.evidence_summary)}</p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: RunStatus }) {
  if (status === "running")
    return (
      <Badge variant="warning">
        <Loader2 className="animate-spin" /> Running
      </Badge>
    );
  if (status === "complete")
    return (
      <Badge variant="success">
        <CheckCircle2 /> Complete
      </Badge>
    );
  if (status === "error")
    return (
      <Badge variant="destructive">
        <AlertTriangle /> Error
      </Badge>
    );
  return <Badge variant="outline">Idle</Badge>;
}

function TimelineRow({ entry }: { entry: TimelineEntry }) {
  const time = new Date(entry.at).toLocaleTimeString();

  if (entry.kind === "phase") {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Activity className="size-3.5" />
        <span className="font-mono text-xs">{time}</span>
        <span className="font-medium text-foreground">{String(entry.data.phase)}</span>
      </div>
    );
  }
  if (entry.kind === "alert_fired") {
    return (
      <div className="flex items-center gap-2">
        <AlertTriangle className="text-warning size-3.5" />
        <span className="font-mono text-xs text-muted-foreground">{time}</span>
        <span>
          Alert fired: <span className="font-mono">{String(entry.data.name)}</span>
        </span>
      </div>
    );
  }
  if (entry.kind === "agent_progress") {
    const summary = (entry.data.summary as Record<string, unknown>) ?? {};
    return (
      <div className="flex items-start gap-2">
        <span className="font-mono text-xs text-muted-foreground">{time}</span>
        <div>
          <span className="font-medium">{String(entry.data.node)}</span>
          <span className="text-muted-foreground"> — {summarizeAgentEvent(summary)}</span>
        </div>
      </div>
    );
  }
  if (entry.kind === "controlplane_log") {
    const message = entry.data.fields
      ? (entry.data.fields as Record<string, unknown>).message
      : entry.data.raw;
    if (!message) return null;
    return (
      <div className="flex items-start gap-2">
        <span className="font-mono text-xs text-muted-foreground">{time}</span>
        <span className="text-muted-foreground">controlplane: {String(message)}</span>
      </div>
    );
  }
  if (entry.kind === "error") {
    return (
      <div className="flex items-center gap-2 text-destructive">
        <XCircle className="size-3.5" />
        <span className="font-mono text-xs">{time}</span>
        <span>{String(entry.data.message)}</span>
      </div>
    );
  }
  return null;
}

function summarizeAgentEvent(summary: Record<string, unknown>): string {
  if (typeof summary.hypothesis_count === "number") {
    return `${summary.hypothesis_count} hypotheses drafted`;
  }
  if (Array.isArray(summary.called_tools) && summary.called_tools.length > 0) {
    return `called ${summary.called_tools.join(", ")}`;
  }
  if (typeof summary.root_cause_preview === "string") {
    return `ranked: ${summary.root_cause_preview}`;
  }
  if (typeof summary.deployments_found === "number") {
    return `${summary.deployments_found} deployments, ${summary.error_traces_found} error traces, ${summary.runbooks_found} runbooks`;
  }
  return "done";
}
