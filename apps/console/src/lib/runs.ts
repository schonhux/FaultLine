import { getPool } from "./db";

/** Mirrors infrastructure/docker/postgres/002-runs.sql. */
export interface RunRow {
  id: string;
  scenario_id: string;
  seed: number;
  status: string;
  fault_config: string;
  ground_truth: string;
  measured_value: number | null;
  error: string | null;
  started_at: string;
  injected_at: string | null;
  symptom_confirmed_at: string | null;
  ended_at: string | null;
}

export interface AlertRow {
  id: string;
  run_id: string;
  name: string;
  condition: string;
  measured_value: number;
  fired_at: string;
}

export async function listRecentRuns(limit = 20): Promise<RunRow[]> {
  const { rows } = await getPool().query<RunRow>(
    "SELECT * FROM runs ORDER BY started_at DESC LIMIT $1",
    [limit]
  );
  return rows;
}

export async function getRun(runId: string): Promise<RunRow | undefined> {
  const { rows } = await getPool().query<RunRow>(
    "SELECT * FROM runs WHERE id = $1",
    [runId]
  );
  return rows[0];
}

export async function getAlertsForRun(runId: string): Promise<AlertRow[]> {
  const { rows } = await getPool().query<AlertRow>(
    "SELECT * FROM alerts WHERE run_id = $1 ORDER BY fired_at ASC",
    [runId]
  );
  return rows;
}

export async function getLatestAlertForRun(
  runId: string
): Promise<AlertRow | undefined> {
  const { rows } = await getPool().query<AlertRow>(
    "SELECT * FROM alerts WHERE run_id = $1 ORDER BY fired_at DESC LIMIT 1",
    [runId]
  );
  return rows[0];
}
