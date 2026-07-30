import { Pool } from "pg";

/**
 * Same Postgres the whole rest of FaultLine talks to (evaluation/harness.py,
 * evaluation/approve.py, mcp/remediation-server/db.py) -- default DSN matches
 * docker-compose.yml's published port, override with POSTGRES_DSN /
 * DATABASE_URL if needed.
 */
declare global {
  var __faultlinePgPool: Pool | undefined;
}

function createPool(): Pool {
  const connectionString =
    process.env.DATABASE_URL ??
    process.env.POSTGRES_DSN ??
    "postgresql://shopgrid:shopgrid@localhost:5432/shopgrid";
  return new Pool({ connectionString, max: 5 });
}

export function getPool(): Pool {
  if (!global.__faultlinePgPool) {
    global.__faultlinePgPool = createPool();
  }
  return global.__faultlinePgPool;
}
