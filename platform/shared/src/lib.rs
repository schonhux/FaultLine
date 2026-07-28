//! Shared infrastructure for all ShopGrid services:
//! - OpenTelemetry init (traces, metrics, logs → OTLP → Collector → ClickHouse)
//! - HTTP middleware recording RED metrics and propagating trace context
//! - Fault-injection state + admin API (dormant unless a scenario activates it)
//! - Instrumented HTTP client for service-to-service calls

pub mod fault;
pub mod http;
pub mod telemetry;

pub use fault::{FaultConfig, FaultState};
