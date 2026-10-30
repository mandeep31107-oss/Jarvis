import 'dotenv/config';
import http from 'node:http';
import { buildApp } from './app.js';
import { loadConfig } from './config.js';
import { closeDatabase, initDatabase } from './db/index.js';
import { createProvider } from './ai/providers/index.js';
import { logger, errorMessage } from './logger.js';

async function main(): Promise<void> {
  const config = loadConfig();
  initDatabase(config.dbFile);
  const provider = createProvider(config);

  const server = http.createServer();
  const app = await buildApp({ config, provider }, { httpServer: server });
  server.on('request', app);

  const host = '0.0.0.0';
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(config.port, host, () => resolve());
  });

  logger.info('J.A.R.V.I.S. online', {
    url: `http://localhost:${config.port}`,
    env: config.env,
    provider: provider.describe(),
    autonomyLevel: config.autonomyLevel,
    research: config.allowOutboundFetch,
  });
  if (config.ephemeralSessionSecret) {
    logger.warn('SESSION_SECRET is not set — sessions will be invalidated on restart (development only).');
  }
  if (!provider.isConfigured()) {
    logger.warn('AI provider is not configured; chat will fail until it is.', { provider: provider.id });
  }

  let shuttingDown = false;
  const shutdown = (signal: string) => {
    if (shuttingDown) return;
    shuttingDown = true;
    logger.info('shutting down', { signal });
    server.close(() => {
      closeDatabase();
      process.exit(0);
    });
    setTimeout(() => process.exit(1), 8000).unref();
  };
  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('unhandledRejection', (reason) => {
    logger.error('unhandled rejection', { error: errorMessage(reason) });
  });
}

main().catch((err) => {
  logger.error('fatal startup error', { error: errorMessage(err) });
  process.exit(1);
});
