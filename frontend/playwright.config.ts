import { defineConfig, devices } from "@playwright/test";

// The real Next.js server runs (login route, middleware, proxy); backend calls made through
// /api/proxy are intercepted per test, so these run in CI without a database or AWS.
const PORT = 3100;

export default defineConfig({
  testDir: "tests/e2e",
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: `http://127.0.0.1:${PORT}`, trace: "retain-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: `npm run build && npx next start -p ${PORT}`,
    url: `http://127.0.0.1:${PORT}/login`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
    env: {
      APP_ENV: "local",
      API_BASE_URL: "http://127.0.0.1:9",
      AUTH_PROVIDER: "local",
      LOCAL_AUTH_SECRET: "e2e-only-secret-not-used-anywhere-else-0123456789",
      LOCAL_DEMO_USERS: JSON.stringify([
        { email: "advisor@e2e.test", password: "advisor-e2e-pass", role: "advisor" },
        { email: "admin@e2e.test", password: "admin-e2e-pass", role: "admin" },
      ]),
    },
  },
});
