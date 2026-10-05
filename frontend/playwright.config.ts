import { defineConfig } from "@playwright/test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("..", import.meta.url));
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
    baseURL: "http://127.0.0.1:5174",
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
      url: "http://127.0.0.1:8011/api/health",
      reuseExistingServer: false,
      timeout: 60000,
      env: {
        PLATFORM_ACCESS_TOKEN: process.env.PLATFORM_ACCESS_TOKEN,
        PLATFORM_AUTH_ENABLED: "false",
      },
    },
    {
      command: "npm run dev -- --port 5174",
      url: "http://127.0.0.1:5174",
      reuseExistingServer: false,
      timeout: 60000,
      env: { PLATFORM_API_TARGET: "http://127.0.0.1:8011" },
    },
  ],
});
