import path from 'node:path';
import type { Server } from 'node:http';
import express, { type Express, type NextFunction, type Request, type Response } from 'express';
import type { AppConfig } from './config.js';
import type { AiProvider } from './ai/types.js';
import { logger, errorMessage } from './logger.js';
import { attachUser } from './security/auth.js';
import { apiLimiter, csrfGuard, makeAuthLimiter, securityHeaders } from './security/middleware.js';
import { authRouter } from './routes/auth.js';
import { chatRouter } from './routes/chat.js';
import { tasksRouter } from './routes/tasks.js';
import { approvalsRouter } from './routes/approvals.js';
import { runsRouter } from './routes/runs.js';
import { memoryRouter } from './routes/memory.js';
import { researchRouter } from './routes/research.js';
import { financeRouter } from './routes/finance.js';
import { complianceRouter } from './routes/compliance.js';
import { healthRouter, systemRouter } from './routes/system.js';

export interface AppDeps {
  config: AppConfig;
  provider: AiProvider;
}

export interface BuildOptions {
  /** Serve the Vite dev middleware or the built SPA. Disable in tests. */
  frontend?: boolean;
  httpServer?: Server;
}

export async function buildApp(deps: AppDeps, opts: BuildOptions = {}): Promise<Express> {
  const { config, provider } = deps;
  const startedAt = Date.now();
  const app = express();

  app.disable('x-powered-by');
  // Trust only the local reverse proxy (preview/tunnel), not arbitrary clients.
  app.set('trust proxy', 'loopback');

  app.use(securityHeaders(config.isProduction));
  app.use(express.json({ limit: '256kb' }));
  app.use(attachUser);
  app.use(csrfGuard);
  app.use('/api', apiLimiter);

  app.use('/api/auth', makeAuthLimiter(config.authRateLimit), authRouter(config));
  app.use('/api/chat', chatRouter(deps));
  app.use('/api/tasks', tasksRouter());
  app.use('/api/approvals', approvalsRouter(config));
  app.use('/api/runs', runsRouter());
  app.use('/api/memory', memoryRouter());
  app.use('/api/research', researchRouter());
  app.use('/api/finance', financeRouter());
  app.use('/api/compliance', complianceRouter());
  app.use('/api/system', systemRouter(deps));
  app.use('/api', healthRouter(config, provider, startedAt));

  app.use('/api', (_req: Request, res: Response) => {
    res.status(404).json({ error: 'not_found' });
  });

  if (opts.frontend !== false) {
    if (config.isProduction) {
      const dist = path.join(process.cwd(), 'web', 'dist');
      app.use(express.static(dist, { index: false, maxAge: '1h' }));
      app.get('*', (_req: Request, res: Response, next: NextFunction) => {
        res.sendFile(path.join(dist, 'index.html'), (err) => {
          if (err) next(err);
        });
      });
    } else {
      const { createServer: createViteServer } = await import('vite');
      const vite = await createViteServer({
        server: {
          middlewareMode: true,
          hmr: opts.httpServer ? { server: opts.httpServer } : undefined,
          allowedHosts: true,
          host: '0.0.0.0',
        },
        appType: 'spa',
      });
      logger.info('vite dev middleware attached', { mode: 'development' });
      app.use(vite.middlewares);
    }
  }

  app.use((err: unknown, req: Request, res: Response, _next: NextFunction) => {
    logger.error('unhandled request error', {
      path: req.path,
      method: req.method,
      error: errorMessage(err),
    });
    if (res.headersSent) return;
    res.status(500).json({ error: 'internal_error' });
  });

  return app;
}
