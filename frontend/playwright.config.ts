import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
const apiPort = Number(process.env.PLATFORM_E2E_API_PORT ?? "8011");
const frontendPort = Number(process.env.PLATFORM_E2E_FRONTEND_PORT ?? "5174");
if (
  [apiPort, frontendPort].some(
    (port) => !Number.isInteger(port) || port < 1024 || port > 65535,
  ) ||
  apiPort === frontendPort
)
  throw new Error("Invalid isolated test ports");
const python = fileURLToPath(
  new URL(
    process.platform === "win32"
      ? "../.venv/Scripts/python.exe"
      : "../.venv/bin/python",
    import.meta.url,
  ),
);
process.env.PLATFORM_ACCESS_TOKEN =
  "isolated-ui-test-token-with-at-least-32-characters";
export default defineConfig({
  testDir: "./tests",
  workers: 1,
  timeout: 60000,
  outputDir: "../artifacts/playwright",
  reporter: "list",
  use: {
    baseURL: `http://127.0.0.1:${frontendPort}`,
    viewport: { width: 1440, height: 1000 },
    browserName: "chromium",
    channel: process.platform === "win32" ? "msedge" : undefined,
    screenshot: "only-on-failure",
    trace: "off",
  },
  webServer: [
    {
      command: `"${python}" scripts/e2e_services.py`,
      cwd: root,
      url: `http://127.0.0.1:${apiPort}/api/health`,
      reuseExistingServer: false,
      timeout: 60000,
      env: {
        PLATFORM_ACCESS_TOKEN: process.env.PLATFORM_ACCESS_TOKEN,
        PLATFORM_AUTH_ENABLED: "false",
        PLATFORM_E2E_API_PORT: String(apiPort),
      },
    },
    {
      command: `npm run dev -- --port ${frontendPort}`,
      url: `http://127.0.0.1:${frontendPort}`,
      reuseExistingServer: false,
      timeout: 60000,
      env: { PLATFORM_API_TARGET: `http://127.0.0.1:${apiPort}` },
    },
  ],
});
