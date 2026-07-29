//! notifications — consumes `orders.created` and simulates confirmation sends.
//!
//! Fault hook: `pause_consumer`, which creates an observable lag signature while
//! checkout continues to publish orders.

use axum::{middleware, routing::get, Router};
use opentelemetry::global;
use rdkafka::{
    consumer::{Consumer, StreamConsumer},
    message::Message,
    ClientConfig,
};
use shared::http::{metrics_middleware, HttpMetrics};
use shared::FaultState;
use std::sync::{
    atomic::{AtomicI64, Ordering},
    Arc,
};
use tokio::time::{sleep, Duration};

#[derive(Clone)]
struct App {
    faults: FaultState,
    observed_lag: Arc<AtomicI64>,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = shared::telemetry::init("notifications")?;

    let app = App {
        faults: FaultState::new(),
        observed_lag: Arc::new(AtomicI64::new(0)),
    };

    let meter = global::meter("notifications");
    {
        let lag = app.observed_lag.clone();
        meter
            .i64_observable_gauge("queue.consumer_lag")
            .with_description("Approximate unprocessed orders.created messages")
            .with_callback(move |obs| obs.observe(lag.load(Ordering::Relaxed), &[]))
            .build();
    }

    let consumer: StreamConsumer = ClientConfig::new()
        .set(
            "bootstrap.servers",
            std::env::var("KAFKA_BROKERS").unwrap_or_else(|_| "localhost:9092".into()),
        )
        .set("group.id", "shopgrid-notifications")
        .set("enable.partition.eof", "false")
        .set("session.timeout.ms", "6000")
        .set("enable.auto.commit", "true")
        .create()?;
    consumer.subscribe(&["orders.created"])?;

    tokio::spawn(run_consumer(consumer, app.clone()));

    let metrics = HttpMetrics::new("notifications");
    let router = Router::new()
        .route("/health", get(|| async { "ok" }))
        .with_state(app.clone())
        .layer(middleware::from_fn_with_state(metrics, metrics_middleware))
        .merge(shared::fault::router(app.faults.clone()));

    let addr = "0.0.0.0:8083";
    tracing::info!(addr, "notifications listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, router)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    telemetry.shutdown();
    Ok(())
}

async fn run_consumer(consumer: StreamConsumer, app: App) {
    loop {
        if app.faults.get().pause_consumer {
            app.observed_lag.fetch_add(1, Ordering::Relaxed);
            tracing::warn!("notifications consumer paused by fault injection");
            sleep(Duration::from_secs(1)).await;
            continue;
        }

        match consumer.recv().await {
            Ok(message) => {
                let payload = message
                    .payload_view::<str>()
                    .and_then(Result::ok)
                    .unwrap_or("{}");
                tracing::info!(
                    topic = message.topic(),
                    partition = message.partition(),
                    offset = message.offset(),
                    payload,
                    "order confirmation simulated"
                );
                app.observed_lag.fetch_update(
                    Ordering::Relaxed,
                    Ordering::Relaxed,
                    |lag| Some(lag.saturating_sub(1)),
                )
                .ok();
            }
            Err(e) => {
                tracing::warn!(error = %e, "orders.created consume failed");
                sleep(Duration::from_millis(500)).await;
            }
        }
    }
}
