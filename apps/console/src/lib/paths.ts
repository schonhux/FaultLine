import path from "node:path";

/**
 * The console app lives at apps/console inside the FaultLine repo and reads
 * repo-level artifacts directly off disk (scenario.yaml files, evaluation
 * reports) -- the same pattern evaluation/harness.py and evaluation/approve.py
 * already use for Postgres, just extended to the filesystem. Override with
 * FAULTLINE_REPO_ROOT if the console is ever run from somewhere else.
 */
export const REPO_ROOT =
  process.env.FAULTLINE_REPO_ROOT ?? path.resolve(process.cwd(), "..", "..");

export const SCENARIOS_DIR = path.join(REPO_ROOT, "scenarios");
export const REPORTS_DIR = path.join(REPO_ROOT, "evaluation", "reports");
