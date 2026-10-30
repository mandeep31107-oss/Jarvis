import { defineConfig } from 'vitest/config';

export default defineConfig({
  esbuild: { jsx: 'automatic' },
  plugins: [
    {
      name: 'externalize-node-builtins',
      enforce: 'pre',
      resolveId(id) {
        // Vite tries to resolve node:sqlite as a package; keep built-ins external.
        if (id === 'node:sqlite' || id === 'sqlite') return { id, external: true };
        return null;
      },
    },
  ],
  test: {
    include: ['tests/**/*.test.{ts,tsx}'],
    environment: 'node',
    globals: false,
    testTimeout: 20_000,
    hookTimeout: 20_000,
  },
});
