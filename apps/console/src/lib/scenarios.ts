import fs from "node:fs";
import path from "node:path";
import yaml from "js-yaml";

import { SCENARIOS_DIR } from "./paths";

/** Mirrors the schema documented at the top of scenarios/db-pool-exhaustion/scenario.yaml. */
export interface Scenario {
  id: string;
  title: string;
  difficulty: string;
  deployment_marker?: {
    service: string;
    version: string;
    git_commit: string;
    config: string;
  };
  reset_restart?: string;
  ground_truth: {
    root_cause: string;
    triggering_change: string | null;
    affected_service: string;
  };
  expected_symptoms: string[];
  alert: {
    name: string;
    condition: string;
  };
  allowed_remediations: string[];
  unsafe_actions: string[];
  recovery_conditions?: { stability_window_seconds: number };
  lifecycle?: {
    warm_seconds: number;
    session_window_seconds: number;
    time_limit_seconds: number;
  };
}

let cache: Scenario[] | null = null;

/** Reads and parses every scenarios/<id>/scenario.yaml. Cached for the life of
 * the server process -- these files don't change at runtime. */
export function listScenarios(): Scenario[] {
  if (cache) return cache;

  if (!fs.existsSync(SCENARIOS_DIR)) {
    cache = [];
    return cache;
  }

  const ids = fs
    .readdirSync(SCENARIOS_DIR, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort();

  cache = ids
    .map((id) => {
      const file = path.join(SCENARIOS_DIR, id, "scenario.yaml");
      if (!fs.existsSync(file)) return null;
      const raw = yaml.load(fs.readFileSync(file, "utf-8")) as Scenario;
      return { ...raw, id: raw.id ?? id };
    })
    .filter((s): s is Scenario => s !== null);

  return cache;
}

export function getScenario(id: string): Scenario | undefined {
  return listScenarios().find((s) => s.id === id);
}
