//! catalog — product data with Redis read-through cache over PostgreSQL.
//! Fault hooks: redis_latency_ms (slow cache scenario), inject_error_rate.

use axum::{
    extract::{Path, State},
    http::{HeaderMap, StatusCode},
    middleware,
    routing::get,
    Json, Router,
};
use opentelemetry::{global, metrics::Counter, KeyValue};
use serde::{Deserialize, Serialize};
use shared::http::{metrics_middleware, should_inject_error, HttpMetrics};
use shared::FaultState;

#[derive(Clone)]
struct App {
    db: sqlx::PgPool,
    cache: redis::aio::ConnectionManager,
    faults: FaultState,
    cache_hits: Counter<u64>,
    cache_misses: Counter<u64>,
}

#[derive(Debug, Serialize, Deserialize, sqlx::FromRow)]
struct Product {
    id: i64,
    name: String,
    price_cents: i64,
    stock: i64,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = shared::telemetry::init("catalog")?;

    let db = sqlx::postgres::PgPoolOptions::new()
        .max_connections(10)
        .connect(&std::env::var("POSTGRES_DSN")?)
        .await?;
    let redis_client = redis::Client::open(format!(
        "redis://{}",
        std::env::var("REDIS_ADDR").unwrap_or_else(|_| "localhost:6379".into())
    ))?;
    let cache = redis::aio::ConnectionManager::new(redis_client).await?;

    let meter = global::meter("catalog");
    let app = App {
        db,
        cache,
        faults: FaultState::new(),
        cache_hits: meter.u64_counter("cache.hits").build(),
        cache_misses: meter.u64_counter("cache.misses").build(),
    };

    let metrics = HttpMetrics::new("catalog");
    let router = Router::new()
        .route("/products", get(list_products))
        .route("/products/:id", get(get_product))
        .route("/health", get(|| async { "ok" }))
        .with_state(app.clone())
        .layer(middleware::from_fn_with_state(metrics, metrics_middleware))
        .merge(shared::fault::router(app.faults.clone()));

    let addr = "0.0.0.0:8082";
    tracing::info!(addr, "catalog listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, router)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    telemetry.shutdown();
    Ok(())
}

/// Apply the redis_latency_ms fault before any cache operation.
async fn cache_delay(faults: &FaultState) {
    let ms = faults.get().redis_latency_ms;
    if ms > 0 {
        tokio::time::sleep(std::time::Duration::from_millis(ms)).await;
    }
}

async fn get_product(
    State(app): State<App>,
    headers: HeaderMap,
    Path(id): Path<i64>,
) -> Result<Json<Product>, StatusCode> {
    authorize_internal_call(&headers)?;

    let fault = app.faults.get();
    if should_inject_error(fault.inject_error_rate, fault.seed, &format!("prod-{id}")) {
        tracing::error!(product_id = id, "injected fault: synthetic catalog error");
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }

    // Read-through cache
    let key = format!("product:{id}");
    cache_delay(&app.faults).await;
    let mut cache = app.cache.clone();
    let cached: Option<String> = redis::cmd("GET")
        .arg(&key)
        .query_async(&mut cache)
        .await
        .unwrap_or(None);

    if let Some(json) = cached {
        app.cache_hits.add(1, &[KeyValue::new("key_space", "product")]);
        if let Ok(p) = serde_json::from_str::<Product>(&json) {
            return Ok(Json(p));
        }
    }
    app.cache_misses.add(1, &[KeyValue::new("key_space", "product")]);

    let product = sqlx::query_as::<_, Product>(
        "SELECT id, name, price_cents, stock FROM products WHERE id = $1",
    )
    .bind(id)
    .fetch_optional(&app.db)
    .await
    .map_err(|e| {
        tracing::error!(error = %e, "database read failed");
        StatusCode::INTERNAL_SERVER_ERROR
    })?
    .ok_or(StatusCode::NOT_FOUND)?;

    cache_delay(&app.faults).await;
    let _: Result<(), _> = redis::cmd("SET")
        .arg(&key)
        .arg(serde_json::to_string(&product).unwrap())
        .arg("EX")
        .arg(60)
        .query_async(&mut cache)
        .await;

    Ok(Json(product))
}

async fn list_products(State(app): State<App>) -> Result<Json<Vec<Product>>, StatusCode> {
    let products = sqlx::query_as::<_, Product>(
        "SELECT id, name, price_cents, stock FROM products ORDER BY id LIMIT 50",
    )
    .fetch_all(&app.db)
    .await
    .map_err(|e| {
        tracing::error!(error = %e, "database list failed");
        StatusCode::INTERNAL_SERVER_ERROR
    })?;
    Ok(Json(products))
}

fn authorize_internal_call(headers: &HeaderMap) -> Result<(), StatusCode> {
    let Some(token) = headers.get("x-internal-token").and_then(|v| v.to_str().ok()) else {
        tracing::warn!("catalog request missing internal token");
        return Err(StatusCode::UNAUTHORIZED);
    };

    match token {
        "svc-checkout-token" | "svc-gateway-token" => Ok(()),
        _ => {
            tracing::warn!("catalog request had invalid internal token");
            Err(StatusCode::UNAUTHORIZED)
        }
    }
}
