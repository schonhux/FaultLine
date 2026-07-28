//! Fault-injection state and admin API.
//!
//! Every service mounts `fault::router()` under `/internal`. Faults are DORMANT by
//! default; the scenario runner activates them via `POST /internal/fault`. This is
//! FaultLine's deterministic alternative to chaos tooling (see ADR-001): the fault
//! *is* a code path, so ground truth is exact and runs are reproducible.

use axum::{extract::State, routing::get, Json, Router};
use serde::{Deserialize, Serialize};
use std::sync::{Arc, RwLock};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct FaultConfig {
    /// checkout: leak a DB connection from the pool on each affected request
    #[serde(default)]
    pub db_connection_leak: bool,
    /// catalog: add artificial latency (ms) to every Redis operation
    #[serde(default)]
    pub redis_latency_ms: u64,
    /// any service: fail this fraction of requests with HTTP 500 (0.0..=1.0)
    #[serde(default)]
    pub inject_error_rate: f64,
    /// notifications: stop consuming from Kafka (consumer-lag scenario)
    #[serde(default)]
    pub pause_consumer: bool,
    /// checkout→catalog internal auth token treated as expired (401s)
    #[serde(default)]
    pub auth_expired: bool,
    /// checkout: retry aggressively on dependency failure (retry-storm scenario)
    #[serde(default)]
    pub aggressive_retries: bool,
    /// deterministic seed for any probabilistic fault behavior
    #[serde(default)]
    pub seed: u64,
}

#[derive(Clone, Default)]
pub struct FaultState(Arc<RwLock<FaultConfig>>);

impl FaultState {
    pub fn new() -> Self {
        Self::default()
    }
    pub fn get(&self) -> FaultConfig {
        self.0.read().unwrap().clone()
    }
    pub fn set(&self, cfg: FaultConfig) {
        *self.0.write().unwrap() = cfg;
    }
}

/// GET  /internal/fault  → current fault config
/// POST /internal/fault  → replace fault config (scenario runner only)
/// POST /internal/fault/reset → back to dormant
pub fn router(state: FaultState) -> Router {
    Router::new()
        .route(
            "/internal/fault",
            get(get_fault).post(set_fault),
        )
        .route("/internal/fault/reset", axum::routing::post(reset_fault))
        .with_state(state)
}

async fn get_fault(State(state): State<FaultState>) -> Json<FaultConfig> {
    Json(state.get())
}

async fn set_fault(
    State(state): State<FaultState>,
    Json(cfg): Json<FaultConfig>,
) -> Json<FaultConfig> {
    tracing::warn!(?cfg, "fault configuration activated");
    state.set(cfg);
    Json(state.get())
}

async fn reset_fault(State(state): State<FaultState>) -> Json<FaultConfig> {
    tracing::info!("fault configuration reset to dormant");
    state.set(FaultConfig::default());
    Json(state.get())
}
