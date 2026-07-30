import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { getLatestReport, listAllReports } from "@/lib/reports";

function pct(n: number) {
  return `${Math.round(n * 100)}%`;
}

function CorrectBadge({ correct }: { correct: boolean | undefined }) {
  if (correct === undefined) return <Badge variant="outline">—</Badge>;
  return correct ? (
    <Badge variant="success">Correct</Badge>
  ) : (
    <Badge variant="destructive">Miss</Badge>
  );
}

export default function ScorecardsPage() {
  const latest = getLatestReport();
  const history = listAllReports();

  if (!latest) {
    return (
      <div className="mx-auto max-w-5xl px-8 py-8">
        <h1 className="mb-6 text-2xl font-semibold tracking-tight">Scorecards</h1>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">No eval reports yet</CardTitle>
            <CardDescription>
              Run <code className="text-foreground">make eval</code> to produce one —
              this page reads whatever lands in{" "}
              <code className="text-foreground">evaluation/reports/*.json</code>.
            </CardDescription>
          </CardHeader>
        </Card>
      </div>
    );
  }

  const { report } = latest;
  const { summary, runs } = report;

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Scorecards</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Latest run of <code className="text-foreground">make eval</code>, generated{" "}
          {new Date(report.generated_at).toLocaleString()}.
        </p>
      </div>

      <div className="mb-6 grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-0">
            <CardDescription>Root cause accuracy</CardDescription>
            <CardTitle className="text-2xl">
              {pct(summary.root_cause_accuracy)}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0" />
        </Card>
        <Card>
          <CardHeader className="pb-0">
            <CardDescription>Affected service accuracy</CardDescription>
            <CardTitle className="text-2xl">
              {pct(summary.affected_service_accuracy)}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0" />
        </Card>
        <Card>
          <CardHeader className="pb-0">
            <CardDescription>Triggering change accuracy</CardDescription>
            <CardTitle className="text-2xl">
              {pct(summary.triggering_change_accuracy)}
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0" />
        </Card>
        <Card>
          <CardHeader className="pb-0">
            <CardDescription>Avg diagnosis time</CardDescription>
            <CardTitle className="text-2xl">
              {summary.avg_diagnosis_time_seconds.toFixed(0)}s
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0" />
        </Card>
      </div>

      <Card className="mb-6">
        <CardHeader>
          <CardTitle className="text-base">Per-scenario results</CardTitle>
          <CardDescription>
            {summary.scored_runs}/{summary.total_runs} runs scored ·{" "}
            {summary.total_unsupported_claims} unsupported claims flagged across all
            evidence summaries
          </CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Scenario</TableHead>
                <TableHead>Root cause</TableHead>
                <TableHead>Service</TableHead>
                <TableHead>Trigger</TableHead>
                <TableHead>Confidence</TableHead>
                <TableHead>Time</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => (
                <TableRow key={run.run_id}>
                  <TableCell className="font-medium">{run.scenario_id}</TableCell>
                  <TableCell>
                    <CorrectBadge correct={run.score?.root_cause_correct} />
                  </TableCell>
                  <TableCell>
                    <CorrectBadge correct={run.score?.affected_service_correct} />
                  </TableCell>
                  <TableCell>
                    <CorrectBadge correct={run.score?.triggering_change_correct} />
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {run.diagnosis
                      ? `${Math.round(run.diagnosis.confidence * 100)}%`
                      : "—"}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {run.diagnosis_time_seconds
                      ? `${run.diagnosis_time_seconds.toFixed(1)}s`
                      : "—"}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {history.length > 1 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">History</CardTitle>
            <CardDescription>
              Every previous <code className="text-foreground">make eval</code> run,
              newest first.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Generated</TableHead>
                  <TableHead>Root cause</TableHead>
                  <TableHead>Service</TableHead>
                  <TableHead>Trigger</TableHead>
                  <TableHead>Runs</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {history.map(({ fileName, report: r }) => (
                  <TableRow key={fileName}>
                    <TableCell className="text-muted-foreground">
                      {new Date(r.generated_at).toLocaleString()}
                    </TableCell>
                    <TableCell>{pct(r.summary.root_cause_accuracy)}</TableCell>
                    <TableCell>{pct(r.summary.affected_service_accuracy)}</TableCell>
                    <TableCell>{pct(r.summary.triggering_change_accuracy)}</TableCell>
                    <TableCell className="text-muted-foreground">
                      {r.summary.scored_runs}/{r.summary.total_runs}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
