import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RemediationStatusBadge } from "@/components/status-badge";
import { RemediationReviewDialog } from "@/components/remediation-review-dialog";
import { listRemediations } from "@/lib/remediations";

export const dynamic = "force-dynamic";

function shortId(id: string | null) {
  if (!id) return "—";
  return id.slice(0, 8);
}

function timeAgo(iso: string) {
  const diffMs = Date.now() - new Date(iso).getTime();
  const mins = Math.round(diffMs / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default async function RemediationsPage() {
  const remediations = await listRemediations(100).catch(() => null);

  if (remediations === null) {
    return (
      <div className="mx-auto max-w-5xl px-8 py-8">
        <h1 className="mb-6 text-2xl font-semibold tracking-tight">
          Remediations
        </h1>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Can&apos;t reach Postgres</CardTitle>
            <CardDescription>
              This page reads the same <code className="text-foreground">remediations</code>{" "}
              table as <code className="text-foreground">evaluation/approve.py</code>. Make
              sure <code className="text-foreground">make up</code> is running and{" "}
              <code className="text-foreground">POSTGRES_DSN</code> / DATABASE_URL points at
              it (defaults to localhost:5432).
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const pending = remediations.filter((r) => r.status === "pending_approval");
  const resolved = remediations.filter((r) => r.status !== "pending_approval");

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Remediations</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Every proposed action from the guarded remediation server — the full
          audit log, plus anything waiting on your approval right now.
        </p>
      </div>

      {pending.length > 0 && (
        <Card className="mb-6 border-warning/40">
          <CardHeader>
            <CardTitle className="text-base">
              {pending.length} awaiting approval
            </CardTitle>
            <CardDescription>
              Nothing executes until you approve or deny — the agent has already
              passed policy, but the action itself hasn&apos;t happened yet.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Tool</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Class</TableHead>
                  <TableHead>Run</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pending.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell className="font-medium">{r.tool}</TableCell>
                    <TableCell>{r.target}</TableCell>
                    <TableCell>{r.class ?? "—"}</TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {shortId(r.run_id)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {timeAgo(r.requested_at)}
                    </TableCell>
                    <TableCell className="text-right">
                      <RemediationReviewDialog remediation={r} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Audit log</CardTitle>
          <CardDescription>
            Every remediation ever proposed, in order — nothing is ever deleted,
            only status-updated.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {resolved.length === 0 && pending.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No remediations proposed yet. Run{" "}
              <code className="text-foreground">make run-agent-remediate</code>{" "}
              against a live scenario to see one land here.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead>Tool</TableHead>
                  <TableHead>Target</TableHead>
                  <TableHead>Run</TableHead>
                  <TableHead>Requested</TableHead>
                  <TableHead>Decided by</TableHead>
                  <TableHead>Result</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {remediations.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell>
                      <RemediationStatusBadge status={r.status} />
                    </TableCell>
                    <TableCell className="font-medium">{r.tool}</TableCell>
                    <TableCell>{r.target}</TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {shortId(r.run_id)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {timeAgo(r.requested_at)}
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {r.decided_by ?? "—"}
                    </TableCell>
                    <TableCell className="text-muted-foreground max-w-xs truncate text-xs">
                      {r.execution_result ?? r.policy_reason ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
