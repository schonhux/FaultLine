//! controlplane (binary: `faultline`) — the scenario runner.
//!
//! Automates the full fault-injection lifecycle: reset -> load known-good -> warm ->
//! baseline-health gate -> inject -> symptom gate -> session window -> verify ->
//! score-ready -> reset. Every run is recorded in Postgres (`runs`, `alerts`) with the
//! seed and ground truth, so `faultline run <scenario> --seed 42` is reproducible and
//! auditable, not a one-off script.

use anyhow::Context;
use clap::{Parser, Subcommand};
use serde::{Deserialize, Serialize};

const ALL_SERVICES: [&str; 4] = ["gateway", "checkout", "catalog", "notifications"];

#[derive(Parser)]
#[command(name = "faultline", about = "FaultLine scenario runner")]
struct Cli {
    #[command(subcommand)]
    command: Command,
}

#[derive(Subcommand)]
enum Command {
    /// Run a scenario end to end: reset -> inject -> observe -> verify -> reset.
    Run {
        /// Scenario id (directory name under scenarios/, e.g. db-pool-exhaustion)
        scenario: String,
        #[arg(long, default_value_t = 42)]
        seed: u64,
    },
}

#[derive(Debug, Deserialize)]
struct Scenario {
    #[allow(dead_code)]
    id: String,
    #[allow(dead_code)]
    title: String,
    #[serde(default)]
    #[allow(dead_code)]
    difficulty: String,
    #[serde(default)]
    deployment_marker: Option<DeploymentMarker>,
    inject: Vec<InjectTarget>,
    ground_truth: serde_json::Value,
    #[serde(default)]
    #[allow(dead_code)]
    expected_symptoms: Vec<String>,
    symptom_check: SymptomCheck,
    alert: AlertDef,
    #[serde(default)]
    #[allow(dead_code)]
    allowed_remediations: Vec<String>,
    #[serde(default)]
    #[allow(dead_code)]
    unsafe_actions: Vec<String>,
    #[serde(default)]
    #[allow(dead_code)]
    recovery_conditions: serde_json::Value,
    // Some faults (e.g. checkout's db_connection_leak) permanently consume a resource --
    // resetting the fault config stops *new* damage but cannot undo damage already done.
    // If set, the reset stage hard-restarts this container (by name) before proceeding,
    // rather than trusting a config toggle to fully restore baseline.
    #[serde(default)]
    reset_restart: Option<String>,
    // Deliberately required, not #[serde(default)]: LifecycleDef's per-field defaults only
    // apply when individual keys are missing from an existing `lifecycle:` block. If the
    // whole block were absent, a struct-level default would need its own Default impl, and
    // an auto-derived one would silently zero every timing field instead of using
    // default_warm()/default_session_window()/default_time_limit(). Requiring the block
    // keeps every scenario file explicit about its own timing instead of risking that trap.
    lifecycle: LifecycleDef,
}

#[derive(Debug, Deserialize)]
struct DeploymentMarker {
    service: String,
    version: String,
    git_commit: String,
    #[serde(default = "default_config_note")]
    config: String,
}
fn default_config_note() -> String {
    "{}".to_string()
}

#[derive(Debug, Deserialize, Serialize)]
struct InjectTarget {
    target: String,
    fault_config: shared::FaultConfig,
}

#[derive(Debug, Deserialize)]
struct SymptomCheck {
    /// ClickHouse SQL returning a single numeric value. May contain the literal
    /// `{injected_at}` placeholder, substituted with the fault-activation timestamp.
    query: String,
    operator: String,
    threshold: f64,
    #[serde(default = "default_poll_interval")]
    poll_interval_seconds: u64,
    #[serde(default = "default_max_wait")]
    max_wait_seconds: u64,
}
fn default_poll_interval() -> u64 {
    5
}
fn default_max_wait() -> u64 {
    60
}

#[derive(Debug, Deserialize)]
struct AlertDef {
    name: String,
    condition: String,
}

#[derive(Debug, Deserialize)]
struct LifecycleDef {
    #[serde(default = "default_warm")]
    warm_seconds: u64,
    #[serde(default = "default_session_window")]
    session_window_seconds: u64,
    #[serde(default = "default_time_limit")]
    #[allow(dead_code)]
    time_limit_seconds: u64,
}
fn default_warm() -> u64 {
    10
}
fn default_session_window() -> u64 {
    20
}
fn default_time_limit() -> u64 {
    300
}

fn service_port(target: &str) -> anyhow::Result<u16> {
    Ok(match target {
        "gateway" => 8080,
        "checkout" => 8081,
        "catalog" => 8082,
        "notifications" => 8083,
        other => anyhow::bail!("unknown fault target service: {other}"),
    })
}

fn service_url(target: &str) -> anyhow::Result<String> {
    let env_key = format!("{}_URL", target.to_uppercase());
    if let Ok(v) = std::env::var(&env_key) {
        return Ok(v);
    }
    Ok(format!("http://{}:{}", target, service_port(target)?))
}

async fn post_fault(
    client: &reqwest::Client,
    target: &str,
    cfg: &shared::FaultConfig,
) -> anyhow::Result<()> {
    let url = format!("{}/internal/fault", service_url(target)?);
    let resp = client
        .post(&url)
        .json(cfg)
        .send()
        .await
        .with_context(|| format!("posting fault to {target}"))?;
    if !resp.status().is_success() {
        anyhow::bail!("fault activation on {target} returned {}", resp.status());
    }
    Ok(())
}

async fn reset_fault(client: &reqwest::Client, target: &str) -> anyhow::Result<()> {
    let url = format!("{}/internal/fault/reset", service_url(target)?);
    let resp = client
        .post(&url)
        .send()
        .await
        .with_context(|| format!("resetting fault on {target}"))?;
    if !resp.status().is_success() {
        anyhow::bail!("fault reset on {target} returned {}", resp.status());
    }
    Ok(())
}

async fn get_fault(client: &reqwest::Client, target: &str) -> anyhow::Result<shared::FaultConfig> {
    let url = format!("{}/internal/fault", service_url(target)?);
    let cfg = client
        .get(&url)
        .send()
        .await
        .with_context(|| format!("reading fault state from {target}"))?
        .json::<shared::FaultConfig>()
        .await
        .with_context(|| format!("parsing fault state from {target}"))?;
    Ok(cfg)
}

async fn reset_all(client: &reqwest::Client) -> anyhow::Result<()> {
    for svc in ALL_SERVICES {
        reset_fault(client, svc).await?;
    }
    Ok(())
}

/// Hard-restart a service container by name (docker compose's default naming:
/// `faultline-<service>-1`). Requires the host Docker socket mounted into this
/// container. Used for scenarios whose fault permanently consumes a resource that a
/// config-level reset cannot reclaim.
async fn hard_restart(service: &str) -> anyhow::Result<()> {
    let container = format!("faultline-{service}-1");
    tracing::warn!(
        container = %container,
        "hard-restarting container to reclaim a resource the fault permanently consumed"
    );
    let status = tokio::process::Command::new("docker")
        .args(["restart", &container])
        .status()
        .await
        .with_context(|| format!("shelling out to `docker restart {container}`"))?;
    if !status.success() {
        anyhow::bail!("docker restart {container} exited with status {status}");
    }
    // Give the restarted process a moment to rebind its port and rebuild its pool
    // before the caller re-checks its fault/health state.
    tokio::time::sleep(std::time::Duration::from_secs(5)).await;
    Ok(())
}

struct Ch {
    client: reqwest::Client,
    url: String,
    user: String,
    password: String,
}

impl Ch {
    fn from_env() -> Self {
        Self {
            client: reqwest::Client::new(),
            url: std::env::var("CLICKHOUSE_URL").unwrap_or_else(|_| "http://clickhouse:8123".into()),
            user: std::env::var("CLICKHOUSE_USER").unwrap_or_else(|_| "default".into()),
            password: std::env::var("CLICKHOUSE_PASSWORD").unwrap_or_default(),
        }
    }

    async fn query_f64(&self, sql: &str) -> anyhow::Result<f64> {
        let resp = self
            .client
            .post(&self.url)
            .basic_auth(&self.user, Some(&self.password))
            .body(sql.to_string())
            .send()
            .await
            .context("clickhouse query request failed")?;
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        if !status.is_success() {
            anyhow::bail!("clickhouse query failed ({status}): {text}");
        }
        let trimmed = text.trim();
        if trimmed.is_empty() {
            return Ok(0.0);
        }
        trimmed
            .parse::<f64>()
            .with_context(|| format!("clickhouse query did not return a single number: {trimmed:?}"))
    }

    async fn execute(&self, sql: &str) -> anyhow::Result<()> {
        let resp = self
            .client
            .post(&self.url)
            .basic_auth(&self.user, Some(&self.password))
            .body(sql.to_string())
            .send()
            .await
            .context("clickhouse statement request failed")?;
        let status = resp.status();
        if !status.is_success() {
            let text = resp.text().await.unwrap_or_default();
            anyhow::bail!("clickhouse statement failed ({status}): {text}");
        }
        Ok(())
    }
}

fn compare(value: f64, operator: &str, threshold: f64) -> anyhow::Result<bool> {
    Ok(match operator {
        ">=" => value >= threshold,
        "<=" => value <= threshold,
        ">" => value > threshold,
        "<" => value < threshold,
        "==" => (value - threshold).abs() < 1e-9,
        other => anyhow::bail!("unsupported symptom_check operator: {other}"),
    })
}

async fn insert_run(
    pg: &sqlx::PgPool,
    run_id: uuid::Uuid,
    scenario_id: &str,
    seed: u64,
    scenario: &Scenario,
) -> anyhow::Result<()> {
    let fault_config_json = serde_json::to_string(&scenario.inject)?;
    let ground_truth_json = serde_json::to_string(&scenario.ground_truth)?;
    sqlx::query(
        "INSERT INTO runs (id, scenario_id, seed, status, fault_config, ground_truth) \
         VALUES ($1, $2, $3, 'running', $4, $5)",
    )
    .bind(run_id)
    .bind(scenario_id)
    .bind(seed as i64)
    .bind(fault_config_json)
    .bind(ground_truth_json)
    .execute(pg)
    .await?;
    Ok(())
}

async fn update_run_injected(
    pg: &sqlx::PgPool,
    run_id: uuid::Uuid,
    injected_at: chrono::DateTime<chrono::Utc>,
) -> anyhow::Result<()> {
    sqlx::query("UPDATE runs SET injected_at = $1 WHERE id = $2")
        .bind(injected_at)
        .bind(run_id)
        .execute(pg)
        .await?;
    Ok(())
}

async fn update_run_symptom_confirmed(
    pg: &sqlx::PgPool,
    run_id: uuid::Uuid,
    at: chrono::DateTime<chrono::Utc>,
    measured: f64,
) -> anyhow::Result<()> {
    sqlx::query(
        "UPDATE runs SET symptom_confirmed_at = $1, measured_value = $2, status = 'symptom_confirmed' \
         WHERE id = $3",
    )
    .bind(at)
    .bind(measured)
    .bind(run_id)
    .execute(pg)
    .await?;
    Ok(())
}

async fn update_run_score_ready(
    pg: &sqlx::PgPool,
    run_id: uuid::Uuid,
    still_present: bool,
) -> anyhow::Result<()> {
    let status = if still_present {
        "score_ready"
    } else {
        "score_ready_flaky"
    };
    sqlx::query("UPDATE runs SET status = $1 WHERE id = $2")
        .bind(status)
        .bind(run_id)
        .execute(pg)
        .await?;
    Ok(())
}

async fn update_run_ended(pg: &sqlx::PgPool, run_id: uuid::Uuid) -> anyhow::Result<()> {
    sqlx::query("UPDATE runs SET ended_at = now() WHERE id = $1")
        .bind(run_id)
        .execute(pg)
        .await?;
    Ok(())
}

async fn update_run_failed(pg: &sqlx::PgPool, run_id: uuid::Uuid, error: &str) -> anyhow::Result<()> {
    sqlx::query("UPDATE runs SET status = 'failed', error = $1, ended_at = now() WHERE id = $2")
        .bind(error)
        .bind(run_id)
        .execute(pg)
        .await?;
    Ok(())
}

async fn insert_alert(
    pg: &sqlx::PgPool,
    run_id: uuid::Uuid,
    name: &str,
    condition: &str,
    measured_value: f64,
) -> anyhow::Result<()> {
    sqlx::query(
        "INSERT INTO alerts (id, run_id, name, condition, measured_value) VALUES ($1, $2, $3, $4, $5)",
    )
    .bind(uuid::Uuid::new_v4())
    .bind(run_id)
    .bind(name)
    .bind(condition)
    .bind(measured_value)
    .execute(pg)
    .await?;
    Ok(())
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = shared::telemetry::init("controlplane")?;
    let cli = Cli::parse();
    let result = match cli.command {
        Command::Run { scenario, seed } => {
            // An interrupted run must never leave a fault stuck active -- that would
            // violate the "always dormant unless explicitly activated, always cleanly
            // reversible" safety property every other layer depends on. If Ctrl+C lands
            // mid-run, race it against a best-effort reset of every service instead of
            // just dying with whatever fault happened to be active at that moment.
            tokio::select! {
                r = run_scenario(&scenario, seed) => r,
                _ = tokio::signal::ctrl_c() => {
                    tracing::warn!("interrupted -- resetting all fault state before exit");
                    let http = reqwest::Client::new();
                    let _ = reset_all(&http).await;
                    Err(anyhow::anyhow!(
                        "interrupted by user (Ctrl+C); all services were reset before exit"
                    ))
                }
            }
        }
    };
    telemetry.shutdown();
    result
}

async fn run_scenario(scenario_id: &str, seed: u64) -> anyhow::Result<()> {
    let scenarios_dir =
        std::env::var("SCENARIOS_DIR").unwrap_or_else(|_| "/app/scenarios".to_string());
    let path = std::path::Path::new(&scenarios_dir)
        .join(scenario_id)
        .join("scenario.yaml");
    let raw = std::fs::read_to_string(&path)
        .with_context(|| format!("reading scenario file at {}", path.display()))?;
    let mut scenario: Scenario = serde_yaml::from_str(&raw)
        .with_context(|| format!("parsing scenario yaml at {}", path.display()))?;
    if scenario.inject.is_empty() {
        anyhow::bail!("scenario {scenario_id} has an empty inject list");
    }

    // Apply the CLI seed to every fault target for determinism -- this is what makes
    // `faultline run <scenario> --seed 42` reproducible run over run.
    for target in &mut scenario.inject {
        target.fault_config.seed = seed;
    }

    let run_id = uuid::Uuid::new_v4();
    let http = reqwest::Client::new();
    let pg = sqlx::postgres::PgPoolOptions::new()
        .max_connections(5)
        .connect(&std::env::var("POSTGRES_DSN")?)
        .await
        .context("connecting to postgres")?;
    let ch = Ch::from_env();

    tracing::info!(run_id = %run_id, scenario = scenario_id, seed, "scenario run starting");
    insert_run(&pg, run_id, scenario_id, seed, &scenario).await?;

    // 1. reset
    reset_all(&http).await.context("stage: reset")?;
    if let Some(service) = &scenario.reset_restart {
        hard_restart(service)
            .await
            .context("stage: reset (hard restart)")?;
    }
    tracing::info!(run_id = %run_id, "stage complete: reset");

    // 2. load known-good: confirm every service actually reads back fully dormant
    for svc in ALL_SERVICES {
        let cfg = get_fault(&http, svc)
            .await
            .context("stage: load known-good")?;
        let dormant = !cfg.db_connection_leak
            && cfg.redis_latency_ms == 0
            && cfg.inject_error_rate == 0.0
            && !cfg.pause_consumer
            && !cfg.auth_expired
            && !cfg.aggressive_retries;
        if !dormant {
            anyhow::bail!(
                "service {svc} did not return to a dormant baseline before injection: {cfg:?}"
            );
        }
    }
    tracing::info!(run_id = %run_id, "stage complete: load known-good");

    // 3. warm
    tokio::time::sleep(std::time::Duration::from_secs(scenario.lifecycle.warm_seconds)).await;
    tracing::info!(run_id = %run_id, seconds = scenario.lifecycle.warm_seconds, "stage complete: warm");

    // 4. baseline-health gate: refuse to inject onto a system that already looks unhealthy
    let now_str = chrono::Utc::now()
        .format("%Y-%m-%d %H:%M:%S%.3f")
        .to_string();
    let baseline_value = ch
        .query_f64(&scenario.symptom_check.query.replace("{injected_at}", &now_str))
        .await
        .context("stage: baseline-health gate")?;
    if compare(baseline_value, &scenario.symptom_check.operator, scenario.symptom_check.threshold)? {
        reset_all(&http).await.ok();
        update_run_failed(
            &pg,
            run_id,
            "baseline-health gate failed: symptom signal already present before injection",
        )
        .await?;
        anyhow::bail!(
            "baseline-health gate failed: symptom signal already present before injection \
             (value={baseline_value}); system is not in a clean baseline state"
        );
    }
    tracing::info!(run_id = %run_id, baseline_value, "stage complete: baseline-health gate");

    // 5. inject
    if let Some(marker) = &scenario.deployment_marker {
        let insert = format!(
            "INSERT INTO otel.deployment_events (service, version, git_commit, deployed_at, config) \
             VALUES ('{}', '{}', '{}', now64(3), '{}')",
            marker.service.replace('\'', "''"),
            marker.version.replace('\'', "''"),
            marker.git_commit.replace('\'', "''"),
            marker.config.replace('\'', "''"),
        );
        ch.execute(&insert)
            .await
            .context("stage: inject (deployment marker)")?;
    }
    for target in &scenario.inject {
        post_fault(&http, &target.target, &target.fault_config)
            .await
            .context("stage: inject")?;
    }
    let injected_at = chrono::Utc::now();
    update_run_injected(&pg, run_id, injected_at).await?;
    tracing::info!(run_id = %run_id, injected_at = %injected_at, "stage complete: inject");

    // 6. symptom gate: poll until the signature crosses threshold or we time out
    let injected_at_str = injected_at.format("%Y-%m-%d %H:%M:%S%.3f").to_string();
    let deadline = std::time::Instant::now()
        + std::time::Duration::from_secs(scenario.symptom_check.max_wait_seconds);
    let mut measured = 0.0;
    let mut confirmed = false;
    while std::time::Instant::now() < deadline {
        tokio::time::sleep(std::time::Duration::from_secs(
            scenario.symptom_check.poll_interval_seconds,
        ))
        .await;
        let q = scenario
            .symptom_check
            .query
            .replace("{injected_at}", &injected_at_str);
        measured = ch.query_f64(&q).await.context("stage: symptom gate")?;
        if compare(measured, &scenario.symptom_check.operator, scenario.symptom_check.threshold)? {
            confirmed = true;
            break;
        }
    }
    if !confirmed {
        reset_all(&http).await.ok();
        update_run_failed(
            &pg,
            run_id,
            "symptom gate timed out; fault never produced the expected signature",
        )
        .await?;
        anyhow::bail!(
            "symptom gate timed out after {}s (last measured value: {measured}); \
             fault never produced the expected signature",
            scenario.symptom_check.max_wait_seconds
        );
    }
    let symptom_confirmed_at = chrono::Utc::now();
    update_run_symptom_confirmed(&pg, run_id, symptom_confirmed_at, measured).await?;
    tracing::info!(run_id = %run_id, measured_value = measured, "stage complete: symptom gate");

    // fire the alert now that the symptom is confirmed
    insert_alert(&pg, run_id, &scenario.alert.name, &scenario.alert.condition, measured).await?;
    tracing::warn!(run_id = %run_id, alert = %scenario.alert.name, measured_value = measured, "ALERT fired");

    // 7. session window: hold the fault active long enough to leave a real signal to investigate
    tokio::time::sleep(std::time::Duration::from_secs(
        scenario.lifecycle.session_window_seconds,
    ))
    .await;
    tracing::info!(run_id = %run_id, seconds = scenario.lifecycle.session_window_seconds, "stage complete: session window");

    // 8. verify: re-check the signature is still present, not a one-off blip
    let q = scenario
        .symptom_check
        .query
        .replace("{injected_at}", &injected_at_str);
    let verify_value = ch.query_f64(&q).await.context("stage: verify")?;
    let still_present = compare(verify_value, &scenario.symptom_check.operator, scenario.symptom_check.threshold)?;
    tracing::info!(run_id = %run_id, verify_value, still_present, "stage complete: verify");

    // 9. score-ready
    update_run_score_ready(&pg, run_id, still_present).await?;
    tracing::info!(run_id = %run_id, "stage complete: score-ready");

    // 10. reset
    reset_all(&http).await.context("stage: reset (final)")?;
    if let Some(service) = &scenario.reset_restart {
        hard_restart(service)
            .await
            .context("stage: reset (final, hard restart)")?;
    }
    update_run_ended(&pg, run_id).await?;
    tracing::info!(run_id = %run_id, "stage complete: reset (final)");

    println!(
        "run {run_id} ({scenario_id}, seed {seed}): symptom confirmed at value {measured:.2}, \
         still present after session window: {still_present} (verify value {verify_value:.2})"
    );
    Ok(())
}
