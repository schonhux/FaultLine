-- Layer 2: scenario runner run records + generated alerts.
-- fault_config/ground_truth are stored as TEXT (JSON-encoded) rather than JSONB so the
-- controlplane binary doesn't need sqlx's "json" feature -- kept deliberately simple for
-- Layer 2; a later layer can migrate these to JSONB if querying by field becomes necessary.

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    seed BIGINT NOT NULL,
    status TEXT NOT NULL,
    fault_config TEXT NOT NULL,
    ground_truth TEXT NOT NULL,
    measured_value DOUBLE PRECISION,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    injected_at TIMESTAMPTZ,
    symptom_confirmed_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS alerts (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id),
    name TEXT NOT NULL,
    condition TEXT NOT NULL,
    measured_value DOUBLE PRECISION NOT NULL,
    fired_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_runs_scenario ON runs (scenario_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_run ON alerts (run_id);
