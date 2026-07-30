import Link from "next/link";
import { ArrowRight } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { listScenarios } from "@/lib/scenarios";

const DIFFICULTY_VARIANT: Record<
  string,
  "default" | "secondary" | "outline" | "warning"
> = {
  easy: "secondary",
  intermediate: "outline",
  hard: "warning",
};

export default function ScenariosPage() {
  const scenarios = listScenarios();

  return (
    <div className="mx-auto max-w-5xl px-8 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Scenarios</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          {scenarios.length} fault scenarios, each with a known root cause and
          recovery condition. Click one to launch it live.
        </p>
      </div>

      {scenarios.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">No scenarios found</CardTitle>
            <CardDescription>
              Expected to find <code className="text-foreground">scenario.yaml</code>{" "}
              files under <code className="text-foreground">scenarios/*/</code>{" "}
              relative to the repo root. Set{" "}
              <code className="text-foreground">FAULTLINE_REPO_ROOT</code> if this
              console isn&apos;t running from{" "}
              <code className="text-foreground">apps/console</code>.
            </CardDescription>
          </CardHeader>
        </Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {scenarios.map((s) => (
            <Link key={s.id} href={`/runs/new?scenario=${s.id}`} className="group block">
              <Card className="hover:border-primary/50 h-full transition-colors hover:shadow-[0_0_0_1px_var(--primary)]">
                <CardHeader>
                  <div className="flex items-start justify-between gap-2">
                    <CardTitle className="group-hover:text-primary text-base transition-colors">
                      {s.title}
                    </CardTitle>
                    <Badge variant={DIFFICULTY_VARIANT[s.difficulty] ?? "outline"}>
                      {s.difficulty}
                    </Badge>
                  </div>
                  <CardDescription className="font-mono text-xs">{s.id}</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div>
                    <div className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wide">
                      Ground truth
                    </div>
                    <div className="text-sm">
                      <span className="font-medium">{s.ground_truth.root_cause}</span>
                      {" · "}
                      <span className="text-muted-foreground">
                        {s.ground_truth.affected_service}
                      </span>
                    </div>
                  </div>
                  <div>
                    <div className="text-muted-foreground mb-1 text-xs font-medium uppercase tracking-wide">
                      Allowed remediations
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {s.allowed_remediations.map((r) => (
                        <Badge key={r} variant="outline">
                          {r}
                        </Badge>
                      ))}
                    </div>
                  </div>
                  <div className="text-muted-foreground flex items-center justify-between pt-1 text-xs">
                    <span>
                      Alert: <span className="font-mono">{s.alert.name}</span>
                    </span>
                    <span className="text-primary flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100">
                      Launch <ArrowRight className="size-3" />
                    </span>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
