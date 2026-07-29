//! checkout — order validation, inventory check via catalog, PostgreSQL
//! transaction, Kafka event publication.
//!
//! Fault hooks (dormant unless a scenario activates them):
//! - db_connection_leak: permanently removes a connection from the pool per
//!   affected request → pool-exhaustion scenario
//! - auth_expired: internal token to catalog treated as expired → 401 cascade
//! - aggressive_retries: retries catalog 5x with no backoff → retry storm
//! - inject_error_rate: synthetic 500s

use axum::{
    extract::State, http::StatusCode, middleware, routing::{get, post}, Json, Router,
};
use opentelemetry::{global, metrics::Histogram, KeyValue};
use rdkafka::producer::{FutureProducer, FutureRecord};
use serde::{Deserialize, Serialize};
use shared::http::{metrics_middleware, should_inject_error, Client, HttpMetrics};
use shared::FaultState;
use std::time::{Duration, Instant};
use tracing::Instrument;

const POOL_MAX: u32 = 20;

#[derive(Clone)]
struct App {
    db: sqlx::PgPool,
    kafka: FutureProducer,
    catalog: Client,
    catalog_url: String,
    faults: FaultState,
    acquire_ms: Histogram<f64>,
}

#[derive(Debug, Deserialize)]
struct CheckoutRequest {
    product_id: i64,
    quantity: i64,
}

#[derive(Debug, Serialize)]
struct CheckoutResponse {
    order_id: String,
    status: String,
}

#[derive(Debug, Deserialize)]
struct Product {
    id: i64,
    price_cents: i64,
    stock: i64,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = shared::telemetry::init("checkout")?;

    let db = sqlx::postgres::PgPoolOptions::new()
        .max_connections(POOL_MAX)
        .acquire_timeout(Duration::from_secs(3))
        .connect(&std::env::var("POSTGRES_DSN")?)
        .await?;

    // Connection-pool observability — the exact signals the pool-exhaustion
    // scenario is diagnosed from.
    let meter = global::meter("checkout");
    {
        let pool = db.clone();
        meter
            .u64_observable_gauge("db.pool.active")
            .with_description("Connections currently checked out or open")
            .with_callback(move |obs| {
                let size = pool.size() as u64;
                let idle = pool.num_idle() as u64;
                obs.observe(size - idle.min(size), &[]);
            })
            .build();
    }
    {
        let pool = db.clone();
        meter
            .u64_observable_gauge("db.pool.idle")
            .with_callback(move |obs| obs.observe(pool.num_idle() as u64, &[]))
            .build();
    }
    meter
        .u64_observable_gauge("db.pool.max")
        .with_callback(|obs| obs.observe(POOL_MAX as u64, &[]))
        .build();

    let kafka: FutureProducer = rdkafka::ClientConfig::new()
        .set(
            "bootstrap.servers",
            std::env::var("KAFKA_BROKERS").unwrap_or_else(|_| "localhost:9092".into()),
        )
        .set("message.timeout.ms", "5000")
        .create()?;

    let app = App {
        db,
        kafka,
        catalog: Client::new("checkout"),
        catalog_url: std::env::var("CATALOG_URL")
            .unwrap_or_else(|_| "http://catalog:8082".into()),
        faults: FaultState::new(),
        acquire_ms: meter
            .f64_histogram("db.pool.acquire.duration_ms")
            .with_description("Time spent waiting to acquire a DB connection")
            .build(),
    };

    let metrics = HttpMetrics::new("checkout");
    let router = Router::new()
        .route("/checkout", post(create_order))
        .route("/health", get(|| async { "ok" }))
        .with_state(app.clone())
        .layer(middleware::from_fn_with_state(metrics, metrics_middleware))
        .merge(shared::fault::router(app.faults.clone()));

    let addr = "0.0.0.0:8081";
    tracing::info!(addr, "checkout listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, router)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    telemetry.shutdown();
    Ok(())
}

async fn create_order(
    State(app): State<App>,
    Json(req): Json<CheckoutRequest>,
) -> Result<Json<CheckoutResponse>, StatusCode> {
    let fault = app.faults.get();
    let order_id = uuid::Uuid::new_v4().to_string();

    if should_inject_error(fault.inject_error_rate, fault.seed, &order_id) {
        tracing::error!(order_id, "injected fault: synthetic checkout error");
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }

    // 1. Validate product + stock via catalog (internal auth header).
    let mut headers = reqwest::header::HeaderMap::new();
    let token = if fault.auth_expired { "expired-token" } else { "svc-checkout-token" };
    headers.insert("x-internal-token", token.parse().unwrap());

    let url = format!("{}/products/{}", app.catalog_url, req.product_id);
    let attempts = if fault.aggressive_retries { 5 } else { 1 };
    let mut product: Option<Product> = None;
    for attempt in 1..=attempts {
        match app
            .catalog
            .get_json::<Product>("catalog", &url, Some(headers.clone()))
            .await
        {
            Ok((status, Some(p))) if status.is_success() => {
                product = Some(p);
                break;
            }
            Ok((status, _)) => {
                tracing::warn!(attempt, %status, "catalog validation failed");
            }
            Err(e) => {
                tracing::warn!(attempt, error = %e, "catalog unreachable");
            }
        }
    }
    let product = product.ok_or(StatusCode::BAD_GATEWAY)?;
    if product.stock < req.quantity {
        return Err(StatusCode::CONFLICT);
    }

    // 2. FAULT HOOK — db_connection_leak: the buggy v1.8.3 code path. A connection
    // is acquired and never returned, exactly like a leak on an error path.
    if fault.db_connection_leak {
        if let Ok(conn) = app.db.acquire().await {
            tracing::debug!("connection acquired for timeout handling"); // deliberately mundane
            conn.leak();
        }
    }

    // 3. Transaction: insert order, decrement stock. Spanned explicitly so the
    // pool-exhaustion signature (wait before SQL executes) is visible in traces,
    // not just in the db.pool.acquire.duration_ms histogram.
    let tx_span = tracing::info_span!("db.transaction", "db.system" = "postgresql");
    let order_id_for_tx = order_id.clone();
    let product_for_tx = &product;
    let req_for_tx = &req;
    let db = &app.db;
    let acquire_ms = &app.acquire_ms;
    let tx_result: Result<(), StatusCode> = async {
        let start = Instant::now();
        let acquired = db.begin().await;
        acquire_ms.record(start.elapsed().as_secs_f64() * 1000.0, &[]);

        let mut tx = acquired.map_err(|e| {
            tracing::error!(
                error = %e,
                pool_active = db.size(),
                pool_max = POOL_MAX,
                "database acquisition timeout"
            );
            StatusCode::INTERNAL_SERVER_ERROR
        })?;

        sqlx::query("INSERT INTO orders (id, product_id, quantity, total_cents, status) VALUES ($1, $2, $3, $4, 'confirmed')")
            .bind(&order_id_for_tx)
            .bind(req_for_tx.product_id)
            .bind(req_for_tx.quantity)
            .bind(product_for_tx.price_cents * req_for_tx.quantity)
            .execute(&mut *tx)
            .await
            .map_err(|e| { tracing::error!(error = %e, "order insert failed"); StatusCode::INTERNAL_SERVER_ERROR })?;

        sqlx::query("UPDATE products SET stock = stock - $1 WHERE id = $2 AND stock >= $1")
            .bind(req_for_tx.quantity)
            .bind(product_for_tx.id)
            .execute(&mut *tx)
            .await
            .map_err(|e| { tracing::error!(error = %e, "stock update failed"); StatusCode::INTERNAL_SERVER_ERROR })?;

        tx.commit()
            .await
            .map_err(|e| { tracing::error!(error = %e, "commit failed"); StatusCode::INTERNAL_SERVER_ERROR })?;

        Ok(())
    }
    .instrument(tx_span)
    .await;
    tx_result?;

    // 4. Publish orders.created event for the notification worker.
    let payload = serde_json::json!({
        "order_id": order_id,
        "product_id": req.product_id,
        "quantity": req.quantity,
        "ts": chrono::Utc::now().to_rfc3339(),
    })
    .to_string();
    let kafka_span = tracing::info_span!("kafka.publish", "messaging.system" = "kafka", "messaging.destination" = "orders.created");
    async {
        if let Err((e, _)) = app
            .kafka
            .send(
                FutureRecord::to("orders.created").key(&order_id).payload(&payload),
                Duration::from_secs(2),
            )
            .await
        {
            // Order is committed; notification delay is acceptable. Log, don't fail.
            tracing::error!(error = %e, order_id, "failed to publish orders.created");
        }
    }
    .instrument(kafka_span)
    .await;

    let attrs = [KeyValue::new("status", "confirmed")];
    global::meter("checkout")
        .u64_counter("orders.created")
        .build()
        .add(1, &attrs);

    Ok(Json(CheckoutResponse { order_id, status: "confirmed".into() }))
}
