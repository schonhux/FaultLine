import Link from "next/link";
import { Radio } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { listRecentRuns } from "@/lib/runs";

export const dynamic = "force-dynamic";

const STATUS_VARIANT: Record<
  string,
  "default" | "secondary" | "destructive" | "outline" | "success" | "warning"
> = {
  completed: "success",
  running: "warning",
  failed: "destructive",
};

export default async function RunsPage() {
  const runs = await listRecentRuns(30).catch(() => null);

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Live Investigation</h1>
          <p className="text-muted-foreground mt-1 text-sm">
            Recent scenario runs from Postgres. Launch a new one below and watch the
            agent investigate in real time.
          </p>
        </div>
        <Button asChild>
          <Link href="/runs/new">
            <Radio /> New investigation
          </Link>
        </Button>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent runs</CardTitle>
          <CardDescription>Newest first, from the runs table.</CardDescription>
        </CardHeader>
        <CardContent>
          {runs === null ? (
            <p className="text-muted-foreground text-sm">
              Can&apos;t reach Postgres. Make sure{" "}
              <code className="text-foreground">make up</code> is running.
            </p>
          ) : runs.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No runs yet — <code className="text-foreground">make run-scenario</code>{" "}
              to create one.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Scenario</TableHead>
                  <TableHead>Seed</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead>Run ID</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell className="font-medium">{run.scenario_id}</TableCell>
                    <TableCell className="text-muted-foreground">{run.seed}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[run.status] ?? "outline"}>
                        {run.status}
                      </Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">
                      {new Date(run.started_at).toLocaleString()}
                    </TableCell>
                    <TableCell className="text-muted-foreground font-mono text-xs">
                      {run.id.slice(0, 8)}
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
