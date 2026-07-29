//! HTTP plumbing shared by all services:
//! - `metrics_middleware`: RED metrics (rate, errors, duration) per route
//! - trace-context extraction/injection so one trace spans gateway→checkout→catalog
//! - `Client`: instrumented reqwest wrapper for service-to-service calls

use axum::{
    body::Body,
    extract::Request,
    http::{HeaderMap, StatusCode},
    middleware::Next,
    response::Response,
};
use opentelemetry::{
    global,
    metrics::{Counter, Histogram},
    propagation::{Extractor, Injector},
    KeyValue,
};
use std::time::Instant;
use tracing::Instrument;
use tracing_opentelemetry::OpenTelemetrySpanExt;

// ── Metrics ──────────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct HttpMetrics {
    pub requests: Counter<u64>,
    pub errors: Counter<u64>,
    pub duration_ms: Histogram<f64>,
}

impl HttpMetrics {
    pub fn new(service: &'static str) -> Self {
        let meter = global::meter(service);
        Self {
            requests: meter
                .u64_counter("http.server.request.count")
                .with_description("Total HTTP requests")
                .build(),
            errors: meter
                .u64_counter("http.server.error.count")
                .with_description("HTTP responses with status >= 500")
                .build(),
            duration_ms: meter
                .f64_histogram("http.server.request.duration_ms")
                .with_description("HTTP request duration in milliseconds")
                .build(),
        }
    }
}

/// Axum middleware: starts a server span (joining upstream trace context if present)
/// and records RED metrics tagged by route + status.
pub async fn metrics_middleware(
    axum::extract::State(metrics): axum::extract::State<HttpMetrics>,
    req: Request,
    next: Next,
) -> Response {
    let method = req.method().clone();
    let route = req.uri().path().to_string();

    // Join the caller's trace (traceparent header) if one exists.
    let parent_cx =
        global::get_text_map_propagator(|p| p.extract(&HeaderExtractor(req.headers())));

    let span = tracing::info_span!(
        "http.request",
        http.method = %method,
        http.route = %route,
        http.status_code = tracing::field::Empty,
    );
    span.set_parent(parent_cx);

    let start = Instant::now();
    let response = next.run(req).instrument(span.clone()).await;
    let elapsed_ms = start.elapsed().as_secs_f64() * 1000.0;

    let status = response.status();
    span.record("http.status_code", status.as_u16());

    let attrs = [
        KeyValue::new("http.route", route),
        KeyValue::new("http.method", method.to_string()),
        KeyValue::new("http.status_code", status.as_u16() as i64),
    ];
    metrics.requests.add(1, &attrs);
    metrics.duration_ms.record(elapsed_ms, &attrs);
    if status.is_server_error() {
        metrics.errors.add(1, &attrs);
    }
    response
}

/// Probabilistic 500s for the `inject_error_rate` fault. Deterministic per request
/// id via a seeded hash so runs with the same seed produce the same failures.
pub fn should_inject_error(rate: f64, seed: u64, request_key: &str) -> bool {
    if rate <= 0.0 {
        return false;
    }
    use std::hash::{Hash, Hasher};
    let mut h = std::collections::hash_map::DefaultHasher::new();
    seed.hash(&mut h);
    request_key.hash(&mut h);
    (h.finish() % 10_000) as f64 / 10_000.0 < rate
}

pub fn injected_error_response() -> Response {
    tracing::error!("injected fault: synthetic internal error");
    Response::builder()
        .status(StatusCode::INTERNAL_SERVER_ERROR)
        .body(Body::from(r#"{"error":"internal error"}"#))
        .unwrap()
}

// ── Trace propagation helpers ────────────────────────────────────────────

struct HeaderExtractor<'a>(&'a HeaderMap);
impl Extractor for HeaderExtractor<'_> {
    fn get(&self, key: &str) -> Option<&str> {
        self.0.get(key).and_then(|v| v.to_str().ok())
    }
    fn keys(&self) -> Vec<&str> {
        self.0.keys().map(|k| k.as_str()).collect()
    }
}

struct HeaderInjector<'a>(&'a mut reqwest::header::HeaderMap);
impl Injector for HeaderInjector<'_> {
    fn set(&mut self, key: &str, value: String) {
        if let (Ok(k), Ok(v)) = (
            reqwest::header::HeaderName::from_bytes(key.as_bytes()),
            reqwest::header::HeaderValue::from_str(&value),
        ) {
            self.0.insert(k, v);
        }
    }
}

// ── Instrumented outbound client ─────────────────────────────────────────

#[derive(Clone)]
pub struct Client {
    inner: reqwest::Client,
    dependency_duration_ms: Histogram<f64>,
}

impl Client {
    pub fn new(service: &'static str) -> Self {
        let meter = global::meter(service);
        Self {
            inner: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(5))
                .build()
                .expect("reqwest client"),
            dependency_duration_ms: meter
                .f64_histogram("http.client.dependency.duration_ms")
                .with_description("Outbound dependency call duration in milliseconds")
                .build(),
        }
    }

    /// GET with current trace context injected and dependency duration recorded.
    pub async fn get_json<T: serde::de::DeserializeOwned>(
        &self,
        dependency: &'static str,
        url: &str,
        headers: Option<reqwest::header::HeaderMap>,
    ) -> anyhow::Result<(StatusCode, Option<T>)> {
        let span = tracing::info_span!("dependency.call", dependency, url);
        let cx = span.context();
        let mut hdrs = headers.unwrap_or_default();
        global::get_text_map_propagator(|p| p.inject_context(&cx, &mut HeaderInjector(&mut hdrs)));

        let start = Instant::now();
        let result = self
            .inner
            .get(url)
            .headers(hdrs)
            .send()
            .instrument(span)
            .await;
        let elapsed = start.elapsed().as_secs_f64() * 1000.0;

        let attrs = [KeyValue::new("dependency", dependency)];
        self.dependency_duration_ms.record(elapsed, &attrs);

        let resp = result?;
        let status = resp.status();
        if status.is_success() {
            Ok((status, Some(resp.json::<T>().await?)))
        } else {
            Ok((status, None))
        }
    }

    /// POST JSON with current trace context injected and dependency duration recorded.
    pub async fn post_json<B: serde::Serialize, T: serde::de::DeserializeOwned>(
        &self,
        dependency: &'static str,
        url: &str,
        body: &B,
        headers: Option<reqwest::header::HeaderMap>,
    ) -> anyhow::Result<(StatusCode, Option<T>)> {
        let span = tracing::info_span!("dependency.call", dependency, url);
        let cx = span.context();
        let mut hdrs = headers.unwrap_or_default();
        global::get_text_map_propagator(|p| p.inject_context(&cx, &mut HeaderInjector(&mut hdrs)));

        let start = Instant::now();
        let result = self
            .inner
            .post(url)
            .headers(hdrs)
            .json(body)
            .send()
            .instrument(span)
            .await;
        let elapsed = start.elapsed().as_secs_f64() * 1000.0;

        let attrs = [KeyValue::new("dependency", dependency)];
        self.dependency_duration_ms.record(elapsed, &attrs);

        let resp = result?;
        let status = resp.status();
        if status.is_success() {
            Ok((status, Some(resp.json::<T>().await?)))
        } else {
            Ok((status, None))
        }
    }
}
