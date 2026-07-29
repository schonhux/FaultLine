//! trafficgen — deterministic seeded load for ShopGrid.
//!
//! The scenario runner will eventually own lifecycle timing. For Layer 0 this
//! keeps gateway/checkout/catalog/notifications warm with reproducible traffic.

use rand::{rngs::StdRng, Rng, SeedableRng};
use reqwest::StatusCode;
use std::time::Duration;
use tokio::time::sleep;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    tracing_subscriber::fmt()
        .json()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| tracing_subscriber::EnvFilter::new("info")),
        )
        .init();

    let target_url = std::env::var("TARGET_URL").unwrap_or_else(|_| "http://localhost:8080".into());
    let seed = std::env::var("TRAFFIC_SEED")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(42);
    let rps = std::env::var("TRAFFIC_RPS")
        .ok()
        .and_then(|v| v.parse::<u64>().ok())
        .unwrap_or(4)
        .max(1);
    let sleep_for = Duration::from_millis(1000 / rps);
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(5))
        .build()?;
    let mut rng = StdRng::seed_from_u64(seed);

    tracing::info!(target_url, seed, rps, "traffic generator started");

    loop {
        let product_id = rng.gen_range(1..=5);
        let quantity = rng.gen_range(1..=3);
        let result = client
            .post(format!("{target_url}/checkout"))
            .header("x-shopgrid-api-key", "dev-shopgrid-key")
            .json(&serde_json::json!({
                "product_id": product_id,
                "quantity": quantity
            }))
            .send()
            .await;

        match result {
            Ok(resp) if resp.status().is_success() => {
                tracing::info!(
                    product_id,
                    quantity,
                    status = %resp.status(),
                    "checkout traffic succeeded"
                );
            }
            Ok(resp) if resp.status() == StatusCode::CONFLICT => {
                tracing::warn!(product_id, quantity, "checkout rejected for stock");
            }
            Ok(resp) => {
                tracing::warn!(product_id, quantity, status = %resp.status(), "checkout traffic failed");
            }
            Err(e) => {
                tracing::warn!(error = %e, "gateway request failed");
            }
        }

        sleep(sleep_for).await;
    }
}
