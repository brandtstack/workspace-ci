import { defineConfig } from 'prisma/config';

// Generate only: no database is ever contacted, so the URL is a placeholder.
export default defineConfig({
  schema: 'prisma/schema',
  datasource: { url: process.env.DATABASE_URL ?? 'postgresql://fixture:fixture@127.0.0.1:5432/fixture' },
});
