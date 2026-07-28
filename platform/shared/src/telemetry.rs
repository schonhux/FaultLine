use opentelemetry::{global, trace::TracerProvider as _, KeyValue};
use opentelemetry_appender_tracing::layer::OpenTelemetryTracingBridge;
use opentelemetry_otlp::{LogExporter, MetricExporter, SpanExporter};
use opentelemetry_sdk::{
    logs::LoggerProvider,
    metrics::{PeriodicReader, SdkMeterProvider},
    runtime,
    trace::TracerProvider,
    Resource,
};
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt, EnvFilter};

/// Handles kept alive for graceful shutdown/flush.
pub struct Telemetry {
    pub tracer_provider: TracerProvider,
    pub meter_provider: SdkMeterProvider,
    pub logger_provider: LoggerProvider,
}

/// Initialize traces, metrics and logs over OTLP/gRPC.
/// Endpoint comes from OTEL_EXPORTER_OTLP_ENDPOINT (default http://localhost:4317).
pub fn init(service_name: &str) -> anyhow::Result<Telemetry> {
    let version =
        std::env::var("SERVICE_VERSION").unwrap_or_else(|_| env!("CARGO_PKG_VERSION").into());

    let resource = Resource::new(vec![
        KeyValue::new("service.name", service_name.to_string()),
        KeyValue::new("service.version", version.clone()),
        KeyValue::new("deployment.environment", "faultline-local"),
    ]);

    // Traces
    let span_exporter = SpanExporter::builder().with_tonic().build()?;
    let tracer_provider = TracerProvider::builder()
        .with_batch_exporter(span_exporter, runtime::Tokio)
        .with_resource(resource.clone())
        .build();
    global::set_tracer_provider(tracer_provider.clone());
    global::set_text_map_propagator(
        opentelemetry_sdk::propagation::TraceContextPropagator::new(),
    );

    // Metrics (exported every 5s so scenario symptoms appear quickly)
    let metric_exporter = MetricExporter::builder().with_tonic().build()?;
    let reader = PeriodicReader::builder(metric_exporter, runtime::Tokio)
        .with_interval(std::time::Duration::from_secs(5))
        .build();
    let meter_provider = SdkMeterProvider::builder()
        .with_reader(reader)
        .with_resource(resource.clone())
        .build();
    global::set_meter_provider(meter_provider.clone());

    // Logs: every `tracing` event is bridged to OTel logs (structured, trace-correlated)
    let log_exporter = LogExporter::builder().with_tonic().build()?;
    let logger_provider = LoggerProvider::builder()
        .with_batch_exporter(log_exporter, runtime::Tokio)
        .with_resource(resource)
        .build();

    let tracer = tracer_provider.tracer(service_name.to_string());
    tracing_subscriber::registry()
        .with(EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")))
        .with(tracing_subscriber::fmt::layer().json()) // stdout for `docker compose logs`
        .with(OpenTelemetryTracingBridge::new(&logger_provider))
        .with(tracing_opentelemetry::layer().with_tracer(tracer))
        .try_init()?;

    tracing::info!(service = service_name, version = %version, "telemetry initialized");

    Ok(Telemetry {
        tracer_provider,
        meter_provider,
        logger_provider,
    })
}

impl Telemetry {
    /// Flush and shut down exporters (call on SIGTERM).
    pub fn shutdown(self) {
        let _ = self.tracer_provider.shutdown();
        let _ = self.meter_provider.shutdown();
        let _ = self.logger_provider.shutdown();
    }
}
