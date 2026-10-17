import { fileURLToPath } from 'node:url';

import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Node, not jsdom: the interesting behaviour in `lib/api.ts` is the
    // *server-side* path, where `window` does not exist. Testing in jsdom
    // would hide the exact condition that produced
    // "Failed to parse URL from /api/news" in production.
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
  resolve: {
    alias: { '@': fileURLToPath(new URL('./', import.meta.url)) },
  },
});
