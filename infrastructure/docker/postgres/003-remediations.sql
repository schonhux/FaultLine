-- Layer 5: guarded remediation. Every proposed remediation action is recorded here,
-- whether or not it passes policy -- this single table doubles as both the approval
-- queue (a human approves/denies a pending row via evaluation/approve.py) and the
-- permanent audit log (rows are never deleted or overwritten beyond a status change).

CREATE TABLE IF NOT EXISTS remediations (
    id UUID PRIMARY KEY,
    run_id TEXT,
    tool TEXT NOT NULL,             -- restart_service | rollback_deployment
    target TEXT NOT NULL,           -- gateway | checkout | catalog | notifications
    class SMALLINT,                 -- 1 (low-risk) or 2 (consequential); null if the
                                     -- tool itself was unrecognized
    justification TEXT NOT NULL,
    policy_decision TEXT NOT NULL,  -- allowed | denied
    policy_reason TEXT,
    status TEXT NOT NULL,           -- denied | pending_approval | approved |
                                     -- denied_by_human | executed | timed_out |
                                     -- execution_failed
    requested_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    decided_by TEXT,
    executed_at TIMESTAMPTZ,
    execution_result TEXT
);

CREATE INDEX IF NOT EXISTS idx_remediations_run ON remediations (run_id);
CREATE INDEX IF NOT EXISTS idx_remediations_status ON remediations (status);
