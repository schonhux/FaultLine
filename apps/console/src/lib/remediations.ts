import { getPool } from "./db";

/** Mirrors infrastructure/docker/postgres/003-remediations.sql. */
export interface RemediationRow {
  id: string;
  run_id: string | null;
  tool: "restart_service" | "rollback_deployment" | string;
  target: string;
  class: number | null;
  justification: string;
  policy_decision: "allowed" | "denied";
  policy_reason: string | null;
  status:
    | "denied"
    | "pending_approval"
    | "approved"
    | "denied_by_human"
    | "executed"
    | "timed_out"
    | "execution_failed";
  requested_at: string;
  decided_at: string | null;
  decided_by: string | null;
  executed_at: string | null;
  execution_result: string | null;
}

export async function listRemediations(limit = 50): Promise<RemediationRow[]> {
  const { rows } = await getPool().query<RemediationRow>(
    "SELECT * FROM remediations ORDER BY requested_at DESC LIMIT $1",
    [limit]
  );
  return rows;
}

export async function listPendingRemediations(): Promise<RemediationRow[]> {
  const { rows } = await getPool().query<RemediationRow>(
    "SELECT * FROM remediations WHERE status = 'pending_approval' ORDER BY requested_at ASC"
  );
  return rows;
}

export async function getRemediation(
  id: string
): Promise<RemediationRow | undefined> {
  const { rows } = await getPool().query<RemediationRow>(
    "SELECT * FROM remediations WHERE id = $1",
    [id]
  );
  return rows[0];
}

/** Same UPDATE evaluation/approve.py issues from the CLI -- the console's
 * Approve/Deny buttons are just a second front-end onto the same queue. */
export async function decideRemediation(
  id: string,
  decision: "approved" | "denied_by_human",
  decidedBy: string
): Promise<RemediationRow | undefined> {
  const { rows } = await getPool().query<RemediationRow>(
    `UPDATE remediations
     SET status = $2, decided_at = now(), decided_by = $3
     WHERE id = $1 AND status = 'pending_approval'
     RETURNING *`,
    [id, decision, decidedBy]
  );
  return rows[0];
}
