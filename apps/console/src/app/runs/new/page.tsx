import { listScenarios } from "@/lib/scenarios";
import { LiveInvestigation } from "@/components/live-investigation";

export default async function NewRunPage({
  searchParams,
}: {
  searchParams: Promise<{ scenario?: string }>;
}) {
  const scenarios = listScenarios();
  const { scenario } = await searchParams;

  return (
    <div className="mx-auto max-w-3xl px-8 py-8">
      <div className="mb-6">
        <h1 className="text-2xl font-semibold tracking-tight">Live Investigation</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Injects a real fault into the running ShopGrid stack and streams the
          agent&apos;s investigation as it happens — the same{" "}
          <code className="text-foreground">docker compose run</code> commands{" "}
          <code className="text-foreground">make run-scenario</code> /{" "}
          <code className="text-foreground">make run-agent</code> use, just relayed
          live instead of run from a terminal.
        </p>
      </div>

      <LiveInvestigation
        scenarios={scenarios.map((s) => ({ id: s.id, title: s.title }))}
        defaultScenarioId={scenario}
      />
    </div>
  );
}
