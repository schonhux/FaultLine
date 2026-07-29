CREATE DATABASE IF NOT EXISTS otel;

CREATE TABLE IF NOT EXISTS otel.deployment_events
(
    service LowCardinality(String),
    version String,
    git_commit String,
    deployed_at DateTime64(3, 'UTC'),
    config String
)
ENGINE = MergeTree
ORDER BY (service, deployed_at);

INSERT INTO otel.deployment_events
SELECT *
FROM
(
    SELECT 'gateway' AS service, 'v1.8.2' AS version, 'local-layer0-gateway' AS git_commit,
           toDateTime64('2026-07-27 16:00:00', 3, 'UTC') AS deployed_at,
           '{"routes":["products","checkout"],"rate_limit_rps":200}' AS config
    UNION ALL
    SELECT 'checkout', 'v1.8.2', 'local-layer0-checkout',
           toDateTime64('2026-07-27 16:05:00', 3, 'UTC'),
           '{"pool_max":20,"faults_dormant":true}'
    UNION ALL
    SELECT 'checkout', 'v1.8.3-buggy', 'scenario-db-pool-leak',
           toDateTime64('2026-07-27 17:30:00', 3, 'UTC'),
           '{"pool_max":20,"db_connection_leak":true}'
    UNION ALL
    SELECT 'catalog', 'v1.8.2', 'local-layer0-catalog',
           toDateTime64('2026-07-27 16:10:00', 3, 'UTC'),
           '{"cache_ttl_seconds":60,"redis_latency_ms":0}'
    UNION ALL
    SELECT 'notifications', 'v1.8.2', 'local-layer0-notifications',
           toDateTime64('2026-07-27 16:15:00', 3, 'UTC'),
           '{"consumer_group":"shopgrid-notifications"}'
)
WHERE NOT EXISTS (
    SELECT 1 FROM otel.deployment_events WHERE git_commit = 'local-layer0-gateway'
);
