//! gateway — external ShopGrid edge.
//!
//! It keeps the public API small, attaches request IDs, applies a lightweight auth
//! check, and forwards traffic to checkout/catalog with trace context propagated
//! through the shared HTTP client.

use axum::{
    extract::{Path, State},
    http::{HeaderMap, HeaderValue, Request, StatusCode},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::{get, post},
    Json, Router,
};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use shared::http::{metrics_middleware, should_inject_error, Client, HttpMetrics};
use shared::FaultState;
use std::{
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc,
    },
    time::{SystemTime, UNIX_EPOCH},
};

#[derive(Clone)]
struct App {
    checkout_url: String,
    catalog_url: String,
    client: Client,
    faults: FaultState,
    limiter: Arc<WindowLimiter>,
}

#[derive(Debug, Deserialize, Serialize)]
struct CheckoutRequest {
    product_id: i64,
    quantity: i64,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let telemetry = shared::telemetry::init("gateway")?;

    let app = App {
        checkout_url: std::env::var("CHECKOUT_URL")
            .unwrap_or_else(|_| "http://checkout:8081".into()),
        catalog_url: std::env::var("CATALOG_URL")
            .unwrap_or_else(|_| "http://catalog:8082".into()),
        client: Client::new("gateway"),
        faults: FaultState::new(),
        limiter: Arc::new(WindowLimiter::new(
            std::env::var("GATEWAY_RPS_LIMIT")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(200),
        )),
    };

    let metrics = HttpMetrics::new("gateway");
    let router = Router::new()
        .route("/health", get(|| async { "ok" }))
        .route("/products", get(list_products))
        .route("/products/:id", get(get_product))
        .route("/checkout", post(create_checkout))
        .with_state(app.clone())
        .layer(middleware::from_fn_with_state(metrics, metrics_middleware))
        .layer(middleware::from_fn_with_state(app.clone(), edge_middleware))
        .merge(shared::fault::router(app.faults.clone()));

    let addr = "0.0.0.0:8080";
    tracing::info!(addr, "gateway listening");
    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, router)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    telemetry.shutdown();
    Ok(())
}

async fn edge_middleware(
    State(app): State<App>,
    mut req: Request<axum::body::Body>,
    next: Next,
) -> Response {
    let path = req.uri().path().to_string();
    if path.starts_with("/internal/") || path == "/health" {
        return next.run(req).await;
    }

    if !app.limiter.allow() {
        tracing::warn!("gateway rate limit exceeded");
        return StatusCode::TOO_MANY_REQUESTS.into_response();
    }

    let request_id = req
        .headers()
        .get("x-request-id")
        .and_then(|v| v.to_str().ok())
        .map(ToOwned::to_owned)
        .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());
    req.headers_mut()
        .insert("x-request-id", HeaderValue::from_str(&request_id).unwrap());

    let authorized = req
        .headers()
        .get("x-shopgrid-api-key")
        .and_then(|v| v.to_str().ok())
        .is_some_and(|v| v == "dev-shopgrid-key");
    if !authorized {
        tracing::warn!(request_id, "gateway request missing or invalid api key");
        return StatusCode::UNAUTHORIZED.into_response();
    }

    next.run(req).await
}

async fn list_products(State(app): State<App>) -> Result<Json<Value>, StatusCode> {
    let url = format!("{}/products", app.catalog_url);
    let (_, body) = app
        .client
        .get_json::<Value>("catalog", &url, Some(internal_headers("svc-gateway-token")))
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "catalog list failed");
            StatusCode::BAD_GATEWAY
        })?;
    body.map(Json).ok_or(StatusCode::BAD_GATEWAY)
}

async fn get_product(
    State(app): State<App>,
    Path(id): Path<i64>,
) -> Result<Json<Value>, StatusCode> {
    let fault = app.faults.get();
    if should_inject_error(fault.inject_error_rate, fault.seed, &format!("gateway-product-{id}")) {
        tracing::error!(product_id = id, "injected fault: synthetic gateway error");
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }

    let url = format!("{}/products/{id}", app.catalog_url);
    let (status, body) = app
        .client
        .get_json::<Value>("catalog", &url, Some(internal_headers("svc-gateway-token")))
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "catalog product lookup failed");
            StatusCode::BAD_GATEWAY
        })?;
    match (status, body) {
        (s, Some(body)) if s.is_success() => Ok(Json(body)),
        (StatusCode::NOT_FOUND, _) => Err(StatusCode::NOT_FOUND),
        (StatusCode::UNAUTHORIZED, _) => Err(StatusCode::BAD_GATEWAY),
        _ => Err(StatusCode::BAD_GATEWAY),
    }
}

async fn create_checkout(
    State(app): State<App>,
    Json(req): Json<CheckoutRequest>,
) -> Result<Json<Value>, StatusCode> {
    let fault = app.faults.get();
    if should_inject_error(
        fault.inject_error_rate,
        fault.seed,
        &format!("gateway-checkout-{}-{}", req.product_id, req.quantity),
    ) {
        tracing::error!("injected fault: synthetic gateway checkout error");
        return Err(StatusCode::INTERNAL_SERVER_ERROR);
    }

    let url = format!("{}/checkout", app.checkout_url);
    let (status, body) = app
        .client
        .post_json::<_, Value>("checkout", &url, &req, Some(internal_headers("svc-gateway-token")))
        .await
        .map_err(|e| {
            tracing::error!(error = %e, "checkout request failed");
            StatusCode::BAD_GATEWAY
        })?;
    match (status, body) {
        (s, Some(body)) if s.is_success() => Ok(Json(body)),
        (StatusCode::CONFLICT, _) => Err(StatusCode::CONFLICT),
        _ => Err(StatusCode::BAD_GATEWAY),
    }
}

fn internal_headers(token: &'static str) -> HeaderMap {
    let mut headers = HeaderMap::new();
    headers.insert("x-internal-token", HeaderValue::from_static(token));
    headers
}

struct WindowLimiter {
    max_per_second: u64,
    current_second: AtomicU64,
    count: AtomicU64,
}

impl WindowLimiter {
    fn new(max_per_second: u64) -> Self {
        Self {
            max_per_second,
            current_second: AtomicU64::new(0),
            count: AtomicU64::new(0),
        }
    }

    fn allow(&self) -> bool {
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let observed = self.current_second.load(Ordering::Relaxed);
        if observed != now {
            self.current_second.store(now, Ordering::Relaxed);
            self.count.store(0, Ordering::Relaxed);
        }
        self.count.fetch_add(1, Ordering::Relaxed) < self.max_per_second
    }
}
