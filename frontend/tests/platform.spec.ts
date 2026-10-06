import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";

test("multi-timeframe research preset, rolling windows and account exposure do not start trading", async ({
  page,
  request,
}) => {
  const seed = await (await request.get("/api/trading/status")).json();
  let configuration = {
    ...structuredClone(seed.configuration),
    symbols: ["BTCUSDT", "ETHUSDT"],
  };
  const writes: { path: string; payload: Record<string, unknown> }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/trading/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") {
      const payload = route.request().postDataJSON();
      writes.push({ path, payload });
      if (path.endsWith("/configuration")) configuration = payload;
      return route.fulfill({
        json: {
          ...seed,
          configuration,
          comparison_id: "hourly-test",
          jobs: [],
        },
      });
    }
    if (path.endsWith("/status"))
      return route.fulfill({
        json: {
          ...seed,
          configuration,
          available: true,
          enabled: false,
          health: { ...seed.health, snapshot_fresh: true },
          account_exposure: {
            groups: {
              system: {
                positions: 0,
                long_notional: "0",
                short_notional: "0",
                stop_risk: "0",
              },
              external: {
                positions: 1,
                long_notional: "100",
                short_notional: "0",
                stop_risk: "0",
              },
              unknown: {
                positions: 0,
                long_notional: "0",
                short_notional: "0",
                stop_risk: "0",
              },
            },
            available_balance: "50",
            reserve_amount: String(
              (configuration.budget * (configuration.cash_reserve_pct ?? 0)) /
                100,
            ),
            spendable_balance: String(
              Math.max(
                0,
                50 -
                  (configuration.budget *
                    (configuration.cash_reserve_pct ?? 0)) /
                    100,
              ),
            ),
            reserved_external_notional: "20",
            known_stop_risk: "0",
            risk_limit: "2",
            risk_complete: false,
            unprotected_symbols: ["ETHUSDT"],
            invalid_symbols: [],
            reasons: [],
            semantics: "外部持仓只查看，不接管。",
          },
        },
      });
    if (path.endsWith("/portfolio"))
      return route.fulfill({
        json: [
          {
            id: 991,
            status: "completed",
            parameters: {
              evaluation_phase: "research",
              start_at: "2025-01-01T00:00:00Z",
              end_at: "2025-04-01T00:00:00Z",
              portfolio_config: configuration,
            },
          },
        ],
      });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "交易助手" }).click();
  await expect(page.locator(".account-exposure-panel")).toContainText(
    "无止损持仓：ETHUSDT",
  );
  await page
    .getByRole("navigation", { name: "交易助手功能" })
    .getByRole("button", { name: /^策略设置/ })
    .click();
  await page
    .getByRole("button", { name: "填入15分钟研究起点", exact: true })
    .click();
  expect(writes).toHaveLength(0);
  await page
    .getByRole("button", { name: "保存扫描与风险配置", exact: true })
    .click();
  await expect.poll(() => configuration.rules.hourly_trend_filter).toBe(true);
  expect(configuration.interval).toBe("15");
  expect(configuration.cash_reserve_pct).toBe(20);
  expect(configuration.research_guard.min_validation_windows).toBe(3);
  await page
    .getByRole("navigation", { name: "交易助手功能" })
    .getByRole("button", { name: "研究验证", exact: true })
    .click();
  await page
    .getByRole("button", { name: "创建多周期策略回测", exact: true })
    .click();
  await expect(page.locator(".period-comparison-panel")).toContainText(
    "已提交 1 项回测",
  );
  await page
    .locator(".portfolio-research-panel")
    .getByRole("button", { name: "多窗口验证", exact: true })
    .click();
  await page
    .getByRole("button", { name: "提交全部独立窗口", exact: true })
    .click();
  await expect.poll(() => writes.length).toBe(3);
  expect(writes[2].path).toBe("/api/trading/portfolio/991/rolling-validation");
  expect(writes[2].payload.windows).toBe(3);
  expect(writes.some((row) => row.path.endsWith("/command"))).toBe(false);
  expect(errors).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBe(true);
});

test("simulation strategy bridges to research without changing execution or placing orders", async ({
  page,
  request,
}) => {
  const seed = await (await request.get("/api/trading/status")).json();
  const strategy = {
    id: 987,
    name: "桥接规则测试",
    exchange: "bybit",
    symbol: "BTCUSDT",
    interval: "15",
    status: "paused",
    signal: "HOLD",
    quantity: "1",
    cash: "200",
    equity: "200",
    initial_equity: "200",
    max_notional: "200",
    total_return: "0",
    max_drawdown: "0",
    position: null,
    origin_job_id: null,
    rules: { ...seed.configuration.rules, fast: 21, stop_loss_pct: 2 },
  };
  await page.route("**/api/strategies", (route) =>
    route.fulfill({ json: [strategy] }),
  );
  const writes: { path: string; payload: Record<string, unknown> }[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/trading/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") {
      writes.push({ path, payload: route.request().postDataJSON() });
      return route.fulfill({
        json: { comparison_id: "bridge-test", jobs: [] },
      });
    }
    if (path.endsWith("/status"))
      return route.fulfill({
        json: { ...seed, available: true, enabled: false },
      });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "策略管理", exact: true }).click();
  const row = page.locator("tr").filter({ hasText: "桥接规则测试" });
  await row.getByRole("button", { name: "研究并用于交易助手" }).click();
  await expect(
    page.getByText("已选：桥接规则测试", { exact: false }),
  ).toBeVisible();
  await expect(
    page.getByText("助手当前执行参数不会因此改变", { exact: false }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "创建三个周期回测", exact: true })
    .click();
  await expect(
    page.getByText("已提交 3 项回测，后台处理", { exact: false }),
  ).toBeVisible();
  expect(writes).toHaveLength(1);
  expect(writes[0].path).toBe("/api/trading/comparisons");
  expect(writes[0].payload.source_strategy_id).toBe(strategy.id);
  expect(writes[0].payload.evaluation_phase).toBe("research");
  expect(errors).toEqual([]);
});

test("quantitative workflow saves opt-in policies, explains selection and shows research evidence without trading", async ({
  page,
  request,
}) => {
  const seed = await (await request.get("/api/trading/status")).json();
  let configuration = structuredClone(seed.configuration);
  const commands: string[] = [];
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const qualification = {
    eligible: false,
    enforced: true,
    failed: ["样本交易数"],
    checks: [
      { name: "样本交易数", passed: false, explanation: "至少20笔已结算交易" },
    ],
  };
  await page.route("**/api/trading/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith("/configuration")) {
      configuration = route.request().postDataJSON();
      return route.fulfill({ json: { ...seed, configuration } });
    }
    if (path.endsWith("/status"))
      return route.fulfill({
        json: {
          ...seed,
          configuration,
          available: true,
          environment: "testnet",
          enabled: false,
          research_qualification: qualification,
          strategy_monitor: {
            enabled: configuration.monitoring.enabled,
            pause_new_entries: false,
            reasons: [],
            completed_trades: 0,
            consecutive_losses: 0,
            observed_openings: 0,
            adverse_slippage_bps: null,
            net_pnl: 0,
            fees: 0,
            funding: 0,
            limitations: ["仅统计当前配置的本系统交易"],
          },
        },
      });
    if (path.endsWith("/scan"))
      return route.fulfill({
        json: {
          candidates: [
            {
              symbol: "BTCUSDT",
              signal: "HOLD",
              actionable: false,
              selected: true,
              selection_rank: 1,
              selection_reason: "已进入候选池，仍需入场信号与风控通过",
              budget_reason: "当前预算可满足最小订单",
              reason: "当前没有入场信号",
              factors: [],
            },
            {
              symbol: "ETHUSDT",
              signal: "HOLD",
              actionable: false,
              selected: false,
              selection_rank: 2,
              selection_reason: "最小订单超出风险预算",
              reason: "最小订单超出风险预算",
              factors: [],
            },
          ],
        },
      });
    if (path.endsWith("/portfolio") && route.request().method() === "GET")
      return route.fulfill({
        json: [
          {
            id: 997,
            status: "completed",
            parameters: {
              evaluation_phase: "research",
              start_at: "2025-01-01T00:00:00Z",
              end_at: "2025-04-01T00:00:00Z",
              portfolio_config: configuration,
            },
          },
        ],
      });
    if (route.request().method() !== "GET") commands.push(path);
    return route.fulfill({ json: [] });
  });
  await page.route("**/api/backtests/997", (route) =>
    route.fulfill({
      json: {
        result: {
          metrics: { total_return: 0.01, max_drawdown: -0.01, trades: 4 },
          curve: [],
          funding_total: 0,
          fees_total: 1,
          partial_fills: 0,
          expired_orders: 0,
          liquidations: 0,
          limitations: [],
          trades: [],
          coverage: {},
          events: [],
          qualification,
          cross_sectional: {
            symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
            horizon: 4,
            rows: [
              {
                key: "momentum",
                label: "价格动量",
                periods: 5,
                mean_rank_ic: null,
                positive_fraction: null,
                high_minus_low_gross: null,
              },
            ],
            limitations: ["不足30个观察期不显示汇总"],
          },
          strategy_review: {
            stress: [
              {
                multiplier: 2,
                metrics: { total_return: -0.01, max_drawdown: -0.02 },
              },
            ],
            neighbors: [],
          },
          selection: {
            history: [
              { timestamp: "2025-01-01T00:00:00Z", selected: ["BTCUSDT"] },
            ],
            rejected_signals: 1,
            history_truncated: false,
            semantics: "仅筛选新开仓，排名退出不平仓",
          },
        },
      },
    }),
  );
  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "交易助手" }).click();
  await expect(page.locator(".quantitative-workflow")).toBeVisible();
  await page
    .getByRole("navigation", { name: "交易助手功能" })
    .getByRole("button", { name: /^策略设置/ })
    .click();
  const policies = page.locator(".quant-policy-settings");
  await expect(policies).toHaveCount(3);
  for (let index = 0; index < 3; index++)
    await policies.nth(index).locator(".el-switch").click();
  await page.getByRole("button", { name: "保存扫描与风险配置" }).click();
  await expect
    .poll(
      () =>
        configuration.selection.enabled &&
        configuration.research_guard.enabled &&
        configuration.monitoring.enabled,
    )
    .toBe(true);
  await page.getByRole("button", { name: "扫描已保存的币种" }).click();
  await expect(
    page
      .locator("#trading-scan")
      .getByText("当前没有入场信号", { exact: true }),
  ).toBeVisible();
  await expect(
    page.locator("#trading-scan").getByText("最小订单超出风险预算").first(),
  ).toBeVisible();
  await page
    .getByRole("navigation", { name: "交易助手功能" })
    .getByRole("button", { name: "研究验证", exact: true })
    .click();
  await page
    .locator(".portfolio-research-panel")
    .getByRole("button", { name: "详情", exact: true })
    .click();
  const dialog = page.getByRole("dialog", { name: "组合回测详情" });
  await expect(
    dialog.getByRole("heading", { name: "多币横截面因子诊断" }),
  ).toBeVisible();
  await expect(dialog.getByText("样本不足").first()).toBeVisible();
  await expect(
    dialog.getByRole("heading", { name: "参数稳定性与成本压力" }),
  ).toBeVisible();
  await dialog.getByText("历史逐时合约筛选记录", { exact: true }).click();
  await expect(dialog.getByText(/排名退出不平仓/).first()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    )
    .toBe(true);
  expect(commands).toEqual([]);
  expect(errors).toEqual([]);
});

for (const scenario of [
  "success",
  "failure",
  "outage",
  "server-error",
  "unaccepted-error",
  "stale-failure",
]) {
  const failed = scenario === "failure" || scenario === "unaccepted-error";
  test(`automatic environment switch ${scenario === "stale-failure" ? "ignores a stale failure from a previous switch" : scenario === "unaccepted-error" ? "keeps the original environment when a 500 request was not accepted" : scenario === "server-error" ? "confirms the result after a 500 submission response" : scenario === "outage" ? "recovers promptly from an unresponsive status request" : failed ? "restores original environment on failure" : "updates account data and stays paused"}`, async ({
    page,
    request,
  }) => {
    const seed = await (await request.get("/api/trading/status")).json();
    let active = "testnet",
      selected = "testnet",
      phase = "idle";
    let finishAt = 0;
    let stalled = false;
    const commands: unknown[] = [];
    await page.route("**/api/bybit-environment", async (route) => {
      if (route.request().method() === "POST") {
        if (scenario === "unaccepted-error")
          return route.fulfill({
            status: 500,
            json: { detail: "request not accepted" },
          });
        selected = route.request().postDataJSON().environment;
        phase = "pending";
        finishAt = Date.now() + 1800;
        if (scenario === "server-error")
          return route.fulfill({
            status: 500,
            json: { detail: "temporary switch error" },
          });
      } else if (scenario === "outage" && phase === "pending" && !stalled) {
        stalled = true;
        await new Promise((resolve) => setTimeout(resolve, 12000));
        return route.abort().catch(() => {});
      } else if (
        scenario === "stale-failure" &&
        phase === "pending" &&
        !stalled
      ) {
        stalled = true;
        return route.fulfill({
          json: {
            active: "testnet",
            selected: "testnet",
            restart_required: false,
            switch_available: true,
            automatic_switch_available: true,
            switch_state: "failed",
            switch_id: "old-switch",
            switch_message: "上一轮切换失败",
          },
        });
      } else if (phase === "pending" && Date.now() >= finishAt) {
        phase = failed ? "failed" : "completed";
        active = failed ? "testnet" : selected;
        selected = active;
      }
      return route.fulfill({
        json: {
          active,
          selected,
          restart_required: active !== selected,
          switch_available: true,
          automatic_switch_available: true,
          switch_state: phase,
          switch_message: failed ? "切换失败，已恢复原环境" : "环境切换成功",
          switch_id: "fake-switch",
          switch_phase: phase === "pending" ? "account" : "",
          switch_elapsed_seconds: phase === "pending" ? 1 : 2,
        },
      });
    });
    await page.route("**/api/trading/**", (route) => {
      const path = new URL(route.request().url()).pathname;
      if (route.request().method() !== "GET")
        commands.push(route.request().postDataJSON());
      if (path.endsWith("/status"))
        return route.fulfill({
          json: {
            ...seed,
            environment: active,
            enabled: false,
            available: true,
            positions: [],
            orders: [],
            configuration: {
              ...seed.configuration,
              budget: active === "mainnet" ? 150 : 200,
            },
            snapshot: {
              available_balance: active === "mainnet" ? "150" : "1000",
              positions: [],
              ownership: {
                positions: [],
                orders: [],
                blocked_symbols: [],
                reserved_notional: "0",
                uncertain: false,
              },
            },
          },
        });
      return route.fulfill({ json: [] });
    });
    await page.goto("/");
    await page.locator("nav").getByRole("button", { name: "交易助手" }).click();
    const header = page.locator(".header-environment");
    await expect(
      header.getByText("当前：测试网", { exact: true }),
    ).toBeVisible();
    await header.locator(".el-switch").click();
    await expect(page.locator(".environment-switch-overlay")).toBeVisible();
    await expect(
      header.getByText("当前：测试网", { exact: true }),
    ).toBeVisible();
    await expect(page.locator(".environment-switch-overlay")).toHaveCount(0, {
      timeout: scenario === "outage" ? 9000 : 20000,
    });
    await expect(
      header.getByText(failed ? "当前：测试网" : "当前：主网", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "交易助手", exact: true }),
    ).toBeVisible();
    await expect(
      page
        .locator(".budget-summary")
        .getByText(
          failed ? /已保存预算：200.00 USDT/ : /已保存预算：150.00 USDT/,
        ),
    ).toBeVisible();
    await expect(
      page.getByText(
        "本系统当前没有运行自动交易。暂停只停止本系统新开仓，不会平仓或撤销你的外部手工订单。",
        { exact: true },
      ),
    ).toBeVisible();
    expect(commands).toEqual([]);
    if (scenario === "stale-failure")
      await expect(page.locator(".el-message--error")).toHaveCount(0);
  });
}

test("legacy exchange snapshots still display actual contract positions", async ({
  page,
  request,
}) => {
  const seed = await (await request.get("/api/trading/status")).json();
  let positions = [
    {
      symbol: "ZECUSDT",
      side: "Buy",
      size: ".07",
      avgPrice: "100",
      markPrice: "101",
      stopLoss: "98",
    },
    {
      symbol: "GOOGLUSDT",
      side: "Buy",
      size: ".14",
      avgPrice: "200",
      markPrice: "201",
      stopLoss: "0",
    },
  ];
  const posts: string[] = [];
  await page.route("**/api/trading/**", (route) => {
    if (route.request().method() !== "GET") {
      posts.push(route.request().url());
      return route.abort();
    }
    if (new URL(route.request().url()).pathname.endsWith("/status"))
      return route.fulfill({
        json: {
          ...seed,
          environment: "mainnet",
          enabled: false,
          available: false,
          positions: [],
          orders: [],
          snapshot: {
            positions,
            available_balance: "100",
            margin_mode: "ISOLATED_MARGIN",
          },
        },
      });
    return route.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "交易助手" }).click();
  const holdings = page.locator(".account-positions");
  await expect(holdings.getByText("ZECUSDT", { exact: true })).toBeVisible();
  await expect(holdings.getByText("GOOGLUSDT", { exact: true })).toBeVisible();
  await expect(holdings.getByText("归属待确认", { exact: true })).toHaveCount(
    2,
  );
  await expect(
    page.getByRole("button", { name: "暂停新开仓", exact: true }),
  ).toBeEnabled();
  await page.getByRole("button", { name: "修改预算", exact: true }).click();
  await expect(
    page.locator("#shared-budget").getByRole("spinbutton"),
  ).toBeDisabled();
  await page
    .getByRole("navigation", { name: "交易助手功能" })
    .getByRole("button", { name: "运行面板", exact: true })
    .click();
  positions = [];
  await page.getByRole("button", { name: "刷新持仓", exact: true }).click();
  await expect(
    holdings.getByText("当前账户没有持仓", { exact: true }),
  ).toBeVisible();
  expect(posts).toEqual([]);
});

test("mainnet assistant follows active environment and requires an explicit start", async ({
  page,
  request,
}) => {
  const seed = await (await request.get("/api/trading/status")).json();
  const commands: Record<string, unknown>[] = [];
  let configuration = structuredClone(seed.configuration);
  let enabled = false;
  let showPositions = false;
  await page.route("**/api/bybit-environment", (r) =>
    r.fulfill({
      json: {
        active: "mainnet",
        selected: "mainnet",
        restart_required: false,
        switch_available: true,
      },
    }),
  );
  await page.route("**/api/trading/**", (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith("/configuration")) {
      configuration = r.request().postDataJSON();
      return r.fulfill({ json: { enabled: false } });
    }
    if (path.endsWith("/command")) {
      const command = r.request().postDataJSON();
      commands.push(command);
      enabled = command.action === "start";
      return r.fulfill({ json: { enabled } });
    }
    if (path.endsWith("/status"))
      return r.fulfill({
        json: {
          ...seed,
          configuration,
          environment: "mainnet",
          available: true,
          enabled,
          snapshot: {
            available_balance: "100",
            order_margin: "100",
            ownership: {
              blocked_symbols: ["ETHUSDT"],
              reserved_notional: "20",
              uncertain: false,
              positions: showPositions
                ? [
                    {
                      symbol: "BTCUSDT",
                      side: "Buy",
                      size: ".01",
                      avgPrice: "100",
                      markPrice: "101",
                      stopLoss: "98",
                      unrealisedPnl: ".01",
                      source: "system",
                      managed: true,
                    },
                    {
                      symbol: "ETHUSDT",
                      side: "Sell",
                      size: ".02",
                      avgPrice: "200",
                      markPrice: "190",
                      stopLoss: "210",
                      unrealisedPnl: ".2",
                      source: "external",
                      managed: false,
                    },
                    {
                      symbol: "SOLUSDT",
                      side: "Buy",
                      size: "1",
                      avgPrice: "10",
                      markPrice: "11",
                      stopLoss: "0",
                      unrealisedPnl: "1",
                      source: "unknown",
                      managed: false,
                    },
                  ]
                : [],
              orders: [
                {
                  orderId: "manual-eth",
                  symbol: "ETHUSDT",
                  side: "Buy",
                  qty: ".1",
                  price: "200",
                  orderStatus: "New",
                  source: "external",
                  managed: false,
                },
              ],
            },
          },
          positions: [],
          orders: [],
          trades: [],
          executions: [],
          ledger: [],
          strategy_status: {
            ...seed.strategy_status,
            configuration_fingerprint: "a".repeat(64),
            acknowledged: false,
            source_job_id: null,
            unvalidated_symbols: [],
            last_scan: [],
          },
        },
      });
    return r.fulfill({ json: [] });
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "交易助手" }).click();
  await expect(
    page
      .locator(".header-environment")
      .getByText("当前：主网", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("当前：主网只读", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByText(/当前执行环境：Bybit 主网，使用真实资金/),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "启动测试网自动交易", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "平仓并暂停（真实资金）", exact: true }),
  ).toBeVisible();
  const external = page.locator(".external-activity");
  await expect(external.getByText("外部／手工", { exact: true })).toBeVisible();
  await expect(
    external.getByText("仅展示，不自动操作", { exact: true }),
  ).toBeVisible();
  await expect(external.getByText(/可用余额：100 USDT/)).toBeVisible();
  await expect(external.getByRole("button")).toHaveCount(0);
  expect(commands).toEqual([]);
  await page.getByRole("button", { name: "修改预算", exact: true }).click();
  await expect(page.getByText(/首次默认 200，可自行修改/)).toBeVisible();
  const budget = page.locator("#shared-budget").getByRole("spinbutton");
  await budget.fill("150");
  await budget.press("Tab");
  await page
    .getByRole("button", { name: "保存扫描与风险配置", exact: true })
    .click();
  await expect(
    page.locator(".current-strategy-panel").getByText(/预算 150 USDT/),
  ).toBeVisible();
  expect(commands).toEqual([]);
  await page
    .getByRole("button", { name: "启动主网自动交易", exact: true })
    .click();
  await expect.poll(() => commands.length).toBe(1);
  expect(commands[0]).toEqual({
    action: "start",
    environment: "mainnet",
    configuration_fingerprint: "a".repeat(64),
  });
  await page.getByRole("button", { name: "暂停新开仓", exact: true }).click();
  await expect.poll(() => commands.length).toBe(2);
  expect(commands[1]).toEqual({ action: "pause", environment: "mainnet" });
  showPositions = true;
  await page.getByRole("button", { name: "刷新持仓", exact: true }).click();
  const holdings = page.locator(".account-positions");
  await expect(
    holdings.getByRole("heading", { name: "账户全部持仓 · 主网" }),
  ).toBeVisible();
  await expect(holdings.getByText("本系统", { exact: true })).toBeVisible();
  await expect(holdings.getByText("外部／手工", { exact: true })).toBeVisible();
  await expect(
    holdings.getByText("归属未确认／混合", { exact: true }),
  ).toBeVisible();
  await expect(
    holdings.getByText("仅展示，不接管", { exact: true }),
  ).toHaveCount(2);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(
    page
      .locator(".budget-summary")
      .getByRole("button", { name: "修改预算", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
  mkdirSync("../artifacts/e2e", { recursive: true });
  await page.screenshot({
    path: "../artifacts/e2e/trading-budget-positions-mobile.png",
    fullPage: true,
  });
});

test("portfolio research, operational health and trade replay stay separate from execution", async ({
  page,
  request,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const seed = await (await request.get("/api/trading/status")).json();
  const configuration = {
    ...seed.configuration,
    symbols: ["BTCUSDT", "ETHUSDT"],
    interval: "60",
  };
  let created = false,
    validated = false,
    verified = false,
    replayed = false,
    saved = false;
  const parameters = {
    evaluation_phase: "research",
    start_at: "2025-01-01T00:00:00Z",
    end_at: "2025-04-01T00:00:00Z",
    portfolio_config: configuration,
  };
  await page.route("**/api/system/status", (r) =>
    r.fulfill({
      json: {
        workers: [{ name: "research", online: true }],
        alerts: [],
        supervisor: { services: [] },
        backup: {
          created_at: "2025-01-01T00:00:00Z",
          directory: "test-backup",
          verified: true,
          message: "数据库和研究文件已校验",
        },
      },
    }),
  );
  await page.route("**/api/system/backups/verify", (r) => {
    verified = true;
    return r.fulfill({ json: { verified: true } });
  });
  await page.route("**/api/trading/**", (r) => {
    const path = new URL(r.request().url()).pathname;
    if (path.endsWith("/status"))
      return r.fulfill({
        json: {
          ...seed,
          available: true,
          configuration,
          execution_state: "paused",
          enabled: false,
          positions: [],
          orders: [],
          snapshot: {},
          trades: [
            {
              symbol: "BTCUSDT",
              opening_link_id: "qp-" + "a".repeat(32),
              net_pnl: "1",
              complete: true,
            },
          ],
          executions: [],
          ledger: [],
        },
      });
    if (path.endsWith("/portfolio")) {
      if (r.request().method() === "POST") {
        const body = r.request().postDataJSON();
        expect(body.execution.include_funding).toBe(true);
        expect(body.execution.order_type).toBe("Market");
        created = true;
        return r.fulfill({ json: { id: 901, status: "pending" } });
      }
      return r.fulfill({
        json: [{ id: 900, status: "completed", parameters }],
      });
    }
    if (path.endsWith("/validation")) {
      validated = true;
      expect(r.request().postDataJSON().start_at).toBe(parameters.end_at);
      return r.fulfill({ json: { id: 902, status: "pending" } });
    }
    if (path.includes("/replay/")) {
      replayed = true;
      return r.fulfill({
        json: {
          context: null,
          orders: [],
          fills: [
            {
              symbol: "BTCUSDT",
              side: "Buy",
              quantity: "1",
              price: "100",
              fee: ".05",
              at_ms: 1735689600000,
            },
          ],
        },
      });
    }
    if (path.endsWith("/configuration")) {
      saved = true;
      expect(r.request().postDataJSON().rules.strategy_type).toBe(
        "rsi_reversion",
      );
      return r.fulfill({ json: { enabled: false } });
    }
    if (path.endsWith("/command"))
      throw new Error("Research must not start trading");
    return r.fulfill({ json: [] });
  });
  await page.route("**/api/backtests/900", (r) =>
    r.fulfill({
      json: {
        id: 900,
        parameters,
        result: {
          metrics: { total_return: 0.02, max_drawdown: -0.01, trades: 2 },
          curve: [
            { timestamp: "2025-01-01", equity: 200 },
            { timestamp: "2025-04-01", equity: 204 },
          ],
          funding_total: 0.1,
          data_quality: {
            BTCUSDT: {
              price: { bars: 269, fingerprint: "a".repeat(64) },
              max_mark_basis_pct: 2,
              funding_largest_gap_minutes: 600,
              warnings: ["资金费记录存在较长空档"],
            },
          },
          fees_total: 0.2,
          partial_fills: 1,
          expired_orders: 0,
          liquidations: 0,
          limitations: ["当前档位快照，强平为近似模型"],
          coverage: {},
          events: [],
          trades: [],
        },
      },
    }),
  );
  await page.goto("/");
  await page.getByText(/运行状态与备份 ·/).click();
  await page.getByRole("button", { name: "校验最近备份" }).click();
  await expect(page.getByText("备份文件和数据库完整性校验通过")).toBeVisible();
  expect(verified).toBe(true);
  await page.locator("nav").getByRole("button", { name: "交易助手" }).click();
  const assistantNav = page.getByRole("navigation", { name: "交易助手功能" });
  await expect(page.locator("#trading-config")).toBeHidden();
  await expect(page.locator(".portfolio-research-panel")).toBeHidden();
  await page
    .getByText("操作帮助：首次使用按什么顺序？", { exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "交易助手操作顺序" }),
  ).toBeVisible();
  expect(
    await page
      .locator('[id^="trading-"]')
      .evaluateAll((nodes) => nodes.map((node) => node.id)),
  ).toEqual([
    "trading-execution",
    "trading-config",
    "trading-research",
    "trading-apply",
    "trading-scan",
  ]);
  await assistantNav
    .getByRole("button", { name: "研究验证", exact: true })
    .click();
  await expect(
    page
      .locator(".research-preview-actions")
      .getByRole("button", { name: "预览研究参数" }),
  ).toBeDisabled();
  const panel = page.locator(".portfolio-research-panel");
  await expect(
    panel.getByRole("heading", { name: "多币种共享预算回测" }),
  ).toBeVisible();
  await panel.getByRole("button", { name: "创建组合研究回测" }).click();
  await expect(panel.getByText(/组合研究已排队/)).toBeVisible();
  expect(created).toBe(true);
  await panel.getByRole("button", { name: "详情", exact: true }).click();
  await expect(
    page.getByRole("dialog").getByText(/总收益 2.00%/),
  ).toBeVisible();
  await page
    .getByRole("dialog")
    .getByText("数据一致性报告", { exact: true })
    .click();
  await expect(
    page
      .getByRole("dialog")
      .getByText("资金费记录存在较长空档", { exact: true }),
  ).toBeVisible();
  await page.getByRole("dialog").locator(".el-dialog__headerbtn").click();
  await panel.getByRole("button", { name: "验证", exact: true }).click();
  await expect(panel.getByText(/验证回测已排队/)).toBeVisible();
  expect(validated).toBe(true);
  await assistantNav
    .getByRole("button", { name: "交易记录", exact: true })
    .click();
  await page.getByRole("button", { name: "查看回放" }).click();
  await expect(page.getByText(/旧订单未记录决策快照/)).toBeVisible();
  expect(replayed).toBe(true);
  await page.getByRole("dialog").locator(".el-dialog__headerbtn").click();
  await assistantNav.getByRole("button", { name: /^策略设置/ }).click();
  await page.locator(".rule-fields .el-select").first().click();
  await page.getByRole("option", { name: "RSI 均值回归" }).click();
  await page.getByRole("button", { name: "保存扫描与风险配置" }).click();
  await expect.poll(() => saved).toBe(true);
  await expect(
    assistantNav.getByRole("button", { name: "运行面板", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    )
    .toBe(true);
  expect(errors).toEqual([]);
});

test("header switch and Testnet assistant expose shared budget and explicit execution", async ({
  page,
}) => {
  let selected = "testnet";
  let enabled = false;
  let executionConfirmed = false;
  const commands: string[] = [];
  await page.route("**/api/bybit-environment", async (route) => {
    if (route.request().method() === "POST")
      selected = route.request().postDataJSON().environment;
    await route.fulfill({
      json: {
        active: "testnet",
        selected,
        restart_required: selected !== "testnet",
        switch_available: true,
      },
    });
  });
  const configuration = {
    budget: 200,
    risk_per_trade_pct: 0.5,
    portfolio_risk_pct: 1,
    daily_loss_pct: 2,
    max_drawdown_pct: 5,
    max_positions: 2,
    leverage: 2,
    fee_rate: 0.00055,
    slippage_bps: 5,
    interval: "60",
    symbols: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    rules: {
      strategy_type: "ema_cross",
      fast: 20,
      slow: 60,
      stop_loss_pct: 2,
      take_profit_pct: 0,
    },
  };
  let origin: Record<string, unknown> | null = null;
  let applied = false;
  let comparisonCreated = false;
  let validationCreated = false;
  let outdatedBackend = true;
  const validationResult = {
    id: 43,
    status: "completed",
    rejected_entries: 0,
    parameters: {
      comparison_id: "validation-test",
      evaluation_phase: "validation",
      interval: "60",
      symbol: "BTCUSDT",
      start_at: "2025-04-01T00:00:00Z",
      end_at: "2025-05-01T00:00:00Z",
    },
    metrics: {
      total_return: 0.01,
      max_drawdown: -0.01,
      trades: 10,
      average_trade_pnl: 0.2,
    },
  };
  await page.route("**/api/trading/**", async (route) => {
    const endpoint = new URL(route.request().url()).pathname.split("/").at(-1);
    if (endpoint === "portfolio") {
      await route.fulfill({ json: [] });
      return;
    }
    if (
      outdatedBackend &&
      ["comparisons", "research"].includes(endpoint ?? "")
    ) {
      await route.fulfill({ status: 404, json: { detail: "Not Found" } });
      return;
    }
    if (endpoint === "comparisons") {
      if (route.request().method() === "POST") {
        const payload = route.request().postDataJSON();
        expect(payload.symbol).toBe("BTCUSDT");
        expect(payload.evaluation_phase).toBe("research");
        expect(
          new Date(payload.end_at).getTime() -
            new Date(payload.start_at).getTime(),
        ).toBe(90 * 86400000);
        comparisonCreated = true;
        await route.fulfill({ json: { comparison_id: "period-test" } });
      } else {
        const rows = comparisonCreated
          ? ["15", "60", "240"].map((interval, id) => ({
              id,
              status: "completed",
              rejected_entries: 2,
              parameters: {
                comparison_id: "period-test",
                evaluation_phase: "research",
                interval,
                symbol: "BTCUSDT",
                start_at: "2025-01-01T00:00:00Z",
                end_at: "2025-04-01T00:00:00Z",
              },
              metrics: {
                total_return: 0.02,
                max_drawdown: -0.01,
                trades: 20,
                average_trade_pnl: 0.2,
              },
            }))
          : [];
        await route.fulfill({
          json: validationCreated ? [validationResult, ...rows] : rows,
        });
      }
      return;
    }
    if (endpoint === "validation") {
      expect(route.request().postDataJSON().start_at).toBe(
        "2025-04-01T00:00:00.000Z",
      );
      validationCreated = true;
      await route.fulfill({
        json: {
          comparison_id: "validation-test",
          jobs: [{ ...validationResult, status: "pending" }],
        },
      });
      return;
    }
    if (endpoint === "research") {
      await route.fulfill({
        json: [
          {
            id: validationCreated ? 43 : 42,
            symbol: "BTCUSDT",
            interval: "60",
            strategy_type: "ema_cross",
            market_environment: "testnet",
          },
        ],
      });
      return;
    }
    if (endpoint === "preview") {
      const mode =
        new URL(route.request().url()).searchParams.get("symbols_mode") ??
        "replace";
      const afterSymbols =
        mode === "keep" ? [...configuration.symbols] : ["BTCUSDT"];
      await route.fulfill({
        json: {
          job_id: validationCreated ? 43 : 42,
          fingerprint: (mode === "keep" ? "a" : "b").repeat(64),
          symbols_mode: mode,
          unvalidated_symbols: mode === "keep" ? ["ETHUSDT", "SOLUSDT"] : [],
          differences: [
            {
              field: "symbols",
              before: [...configuration.symbols],
              after: afterSymbols,
              research: ["BTCUSDT"],
              changed: mode !== "keep",
            },
            {
              field: "budget",
              before: 200,
              after: 200,
              research: 150,
              changed: false,
            },
            {
              field: "rules.fast",
              before: 20,
              after: 10,
              research: 10,
              changed: true,
            },
          ],
          configuration: {
            ...configuration,
            symbols: afterSymbols,
            rules: { ...configuration.rules, fast: 10, slow: 30 },
          },
          metrics: { total_return: 0.02, max_drawdown: -0.01, trades: 20 },
          market_environment: "testnet",
          warnings: ["应用后保持暂停，执行数量按共享风险预算重新计算"],
        },
      });
      return;
    }
    if (endpoint === "apply") {
      expect(route.request().postDataJSON().acknowledge_model_difference).toBe(
        true,
      );
      expect(route.request().postDataJSON().symbols_mode).toBe("keep");
      expect(
        route.request().postDataJSON().acknowledge_unvalidated_symbols,
      ).toBe(true);
      configuration.rules.fast = 10;
      configuration.rules.slow = 30;
      origin = {
        job_id: validationCreated ? 43 : 42,
        applied_at: new Date().toISOString(),
        modified: false,
      };
      applied = true;
      await route.fulfill({ json: { enabled: false } });
      return;
    }
    if (endpoint === "command") {
      const action = route.request().postDataJSON().action;
      commands.push(action);
      enabled = action === "start";
      executionConfirmed = false;
    }
    if (endpoint === "scan") {
      await route.fulfill({
        json: {
          candidates: [
            {
              symbol: "BTCUSDT",
              signal: "HOLD",
              actionable: false,
              reason: "当前已收盘 K 线没有入场信号",
              factors: [
                {
                  key: "volume",
                  label: "成交量倍数",
                  value: 0.8,
                  unit: "倍",
                  enabled: true,
                  passed: false,
                  threshold: 1,
                  comparison: "min",
                },
              ],
            },
          ],
        },
      });
      return;
    }
    await route.fulfill({
      json: {
        available: true,
        environment: "testnet",
        enabled,
        strategy_status: {
          configuration_fingerprint: "a".repeat(64),
          acknowledged: enabled && executionConfirmed,
          confirmed_at: executionConfirmed ? new Date().toISOString() : null,
          source: origin ? "validation" : "saved_configuration",
          source_job_id: origin?.job_id ?? null,
          source_modified: false,
          unvalidated_symbols: [],
          last_scan: [],
          last_scan_at: null,
        },
        research_origin: origin,
        risk_halted: false,
        flatten_requested: false,
        equity: "200",
        peak_equity: "200",
        configuration,
        last_error: null,
        updated_at: new Date().toISOString(),
        snapshot: {
          wallet_equity: "1000",
          available_balance: "1000",
          margin_mode: "ISOLATED_MARGIN",
          event_stream: { ready: false, reconciled: false, events: 3 },
          entry_check: {
            symbol: "BTCUSDT",
            reason: "报价超过 5 秒有效期，暂停开仓",
          },
        },
        positions: [],
        orders: [],
        trades: [
          {
            symbol: "XRPUSDT",
            entry_price: "1.5685",
            exit_price: "1.5683",
            closed_quantity: "3.7",
            fees: "0.0063834",
            funding: "0",
            net_pnl: "-0.0071234",
            complete: true,
            exit_reason: "请求平仓",
          },
        ],
        executions: [
          {
            symbol: "XRPUSDT",
            side: "Buy",
            quantity: "3.7",
            price: "1.5685",
            fee: "0.0031917",
            owned: true,
            at_ms: Date.now(),
          },
        ],
        ledger: [
          {
            kind: "TRANSFER_IN",
            symbol: "",
            change: "1000",
            fee: "0",
            funding: "0",
            at_ms: Date.now(),
          },
        ],
        net_flows: "1000",
      },
    });
  });
  await page.goto("/");
  const header = page.locator(".header-environment");
  await expect(header.getByText("当前：测试网")).toBeVisible();
  await header.locator(".el-switch").click();
  await expect(header.getByText("待重启")).toBeVisible();
  await expect(header.getByText("当前：测试网")).toBeVisible();
  await header.locator(".el-switch").click();
  await page.locator("nav").getByRole("button", { name: "交易助手" }).click();
  const assistantNav = page.getByRole("navigation", { name: "交易助手功能" });
  await page.getByText("账户余额、风险与同步详情", { exact: true }).click();
  await expect(
    page.getByText(/订单实时同步：断线恢复中，暂停新开仓/),
  ).toBeVisible();
  await expect(
    page.getByText("BTCUSDT 开仓检查：报价超过 5 秒有效期，暂停开仓"),
  ).toBeVisible();
  await assistantNav
    .getByRole("button", { name: "研究验证", exact: true })
    .click();
  await expect(
    page
      .locator(".period-comparison-panel")
      .getByText(/当前后端尚未加载新功能/),
  ).toBeVisible();
  await expect(
    page
      .locator(".research-apply-panel")
      .getByText(/当前后端尚未加载验证回测功能/),
  ).toBeVisible();
  outdatedBackend = false;
  await page
    .locator(".period-comparison-panel")
    .getByRole("button", { name: "刷新结果" })
    .click();
  await page
    .locator(".research-apply-panel")
    .getByRole("button", { name: "刷新回测列表" })
    .click();
  await assistantNav
    .getByRole("button", { name: "运行面板", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "共享资金与账户保护" }),
  ).toBeVisible();
  await expect(page.getByText("已暂停新开仓", { exact: true })).toBeVisible();
  await assistantNav
    .getByRole("button", { name: "交易记录", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "交易周期与净盈亏" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "交易所逐笔成交" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "资金流水（USDT）" }),
  ).toBeVisible();
  await expect(page.getByText("-0.0071", { exact: true })).toBeVisible();
  await assistantNav
    .getByRole("button", { name: "运行面板", exact: true })
    .click();
  await page.getByRole("button", { name: "扫描已保存的币种" }).click();
  await page.getByText("查看已收盘因子", { exact: true }).click();
  await expect(page.getByText(/成交量倍数：.*过滤未通过/)).toBeVisible();
  const budgetInput = page
    .locator("#trading-config .el-form-item")
    .filter({ hasText: "共享预算（USDT）" })
    .getByRole("spinbutton");
  await assistantNav.getByRole("button", { name: /^策略设置/ }).click();
  await expect(
    page.locator("#trading-config .advanced-rule-fields input").first(),
  ).toBeHidden();
  await budgetInput.fill("150");
  await budgetInput.press("Tab");
  await assistantNav
    .getByRole("button", { name: "运行面板", exact: true })
    .click();
  await assistantNav.getByRole("button", { name: /^策略设置/ }).click();
  await expect(budgetInput).toHaveValue("150");
  await assistantNav
    .getByRole("button", { name: "运行面板", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "启动测试网自动交易", exact: true }),
  ).toBeDisabled();
  await expect(
    page.locator(".current-strategy-panel").getByText(/表单与已保存配置不一致/),
  ).toBeVisible();
  await expect(
    page.locator(".current-strategy-panel").getByText(/预算 200 USDT/),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "加载已保存配置", exact: true })
    .click();
  await assistantNav.getByRole("button", { name: /^策略设置/ }).click();
  await expect(budgetInput).toHaveValue("200");
  await assistantNav
    .getByRole("button", { name: "运行面板", exact: true })
    .click();
  await page.getByRole("button", { name: "扫描已保存的币种" }).click();
  await expect(
    page.getByRole("button", { name: "启动测试网自动交易", exact: true }),
  ).toBeEnabled();
  await expect(
    page.getByText("当前已收盘 K 线没有入场信号", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "启动测试网自动交易", exact: true })
    .click();
  await expect(
    page
      .locator(".current-strategy-panel")
      .getByText(/已请求启动，等待执行进程确认当前参数/),
  ).toBeVisible();
  executionConfirmed = true;
  await page.getByRole("button", { name: "刷新执行确认" }).click();
  await expect(
    page
      .locator(".current-strategy-panel")
      .getByText(/执行进程已按当前已保存参数完成一轮检查/),
  ).toBeVisible();
  await page.getByRole("button", { name: "暂停新开仓", exact: true }).click();
  await expect(page.getByText("已暂停新开仓", { exact: true })).toBeVisible();
  expect(commands).toEqual(["start", "pause"]);
  await assistantNav
    .getByRole("button", { name: "研究验证", exact: true })
    .click();
  await page.getByRole("button", { name: "创建三个周期回测" }).click();
  await expect(page.getByText("最近一组：", { exact: false })).toBeVisible();
  expect(comparisonCreated).toBe(true);
  expect(commands).toEqual(["start", "pause"]);
  await page.route("**/api/backtests/0", (route) =>
    route.fulfill({
      json: {
        result: {
          factor_analysis: {
            split_at: "2025-03-01T00:00:00Z",
            split_kind: "internal_70_30",
            affects_execution: false,
            warnings: [],
            limitations: [],
            redundancy: [],
            rows: [
              {
                key: "trend",
                label: "均线趋势差",
                horizon: 4,
                train_samples: 120,
                test_samples: 50,
                train_ic: 0.12,
                test_ic: 0.02,
                direction: 1,
                lower: -0.1,
                upper: 0.1,
                tail_samples: 20,
                tail_gross_mean: 0.001,
                tail_net_mean: -0.001,
              },
            ],
            ablation: [
              {
                label: "当前完整规则",
                metrics: {
                  total_return: 0.01,
                  max_drawdown: -0.02,
                  trades: 15,
                },
              },
              {
                label: "仅关闭成交量过滤",
                metrics: {
                  total_return: -0.01,
                  max_drawdown: -0.03,
                  trades: 25,
                },
              },
            ],
          },
        },
      },
    }),
  );
  await page
    .locator(".period-comparison-panel")
    .getByRole("button", { name: "查看因子诊断" })
    .first()
    .click();
  const factorReport = page.locator(".period-factor-report");
  await factorReport
    .getByText("因子诊断：历史关联与过滤条件对照", { exact: true })
    .click();
  await expect(
    factorReport.getByText("均线趋势差", { exact: true }),
  ).toBeVisible();
  await expect(
    factorReport.getByText("仅关闭成交量过滤", { exact: true }),
  ).toBeVisible();
  expect(commands).toEqual(["start", "pause"]);
  await page.route("**/api/backtests/1", (route) =>
    route.fulfill({ json: { result: {} } }),
  );
  await page
    .locator(".period-comparison-panel")
    .getByRole("button", { name: "查看因子诊断" })
    .nth(1)
    .click();
  await expect(
    factorReport.getByText(/此历史结果尚未记录因子诊断/),
  ).toBeVisible();
  await page
    .locator(".period-comparison-panel")
    .getByRole("button", { name: "创建后续验证" })
    .nth(1)
    .click();
  await page.getByRole("button", { name: "提交独立验证回测" }).click();
  expect(validationCreated).toBe(true);
  await expect(
    page.locator(".period-comparison-panel").getByText(/已提交 1 项回测/),
  ).toBeVisible();
  const research = page.locator(".research-apply-panel");
  await research.locator(".el-select").click();
  await page.getByRole("option", { name: /#43 BTCUSDT/ }).click();
  await expect(
    research.getByRole("radio", { name: "仅使用回测币种", exact: true }),
  ).toBeChecked();
  await research.getByText("保留当前扫描币种", { exact: true }).click();
  await research
    .getByRole("button", { name: "预览研究参数", exact: true })
    .click();
  await expect(
    research.getByRole("button", { name: "应用参数并保持暂停" }),
  ).toBeDisabled();
  await expect(
    research.getByRole("heading", { name: "应用前后对比" }),
  ).toBeVisible();
  await research
    .getByText("我已核对应用前后差异和风险设置", { exact: true })
    .click();
  await expect(
    research.getByRole("button", { name: "应用参数并保持暂停" }),
  ).toBeDisabled();
  await research.getByText("仅使用回测币种", { exact: true }).click();
  await expect(
    research.getByRole("button", { name: "应用参数并保持暂停" }),
  ).toHaveCount(0);
  await research
    .getByRole("button", { name: "预览研究参数", exact: true })
    .click();
  await expect(
    research.getByText("我确认保留这些尚未验证的币种", { exact: true }),
  ).toHaveCount(0);
  await research.getByText("保留当前扫描币种", { exact: true }).click();
  await research
    .getByRole("button", { name: "预览研究参数", exact: true })
    .click();
  await research
    .getByText("我已核对应用前后差异和风险设置", { exact: true })
    .click();
  await research
    .getByText("我确认保留这些尚未验证的币种", { exact: true })
    .click();
  await expect(
    research.getByRole("button", { name: "应用参数并保持暂停" }),
  ).toBeEnabled();
  await research.getByRole("button", { name: "应用参数并保持暂停" }).click();
  await expect(
    assistantNav.getByRole("button", { name: "运行面板", exact: true }),
  ).toHaveAttribute("aria-pressed", "true");
  await expect(
    page.locator(".current-strategy-panel").getByText(/参数来源：.*#43/),
  ).toBeVisible();
  expect(configuration.symbols).toEqual(["BTCUSDT", "ETHUSDT", "SOLUSDT"]);
  expect(applied).toBe(true);
  await expect(
    page
      .locator(".current-strategy-panel")
      .getByText(/当前已暂停；尚无本轮执行确认/),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(header.locator(".el-switch")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await expect(page.locator(".el-message")).toHaveCount(0);
  mkdirSync("../artifacts/e2e", { recursive: true });
  await page.screenshot({
    path: "../artifacts/e2e/testnet-assistant-mobile.png",
    fullPage: true,
  });
});

test("large market: every coin is searchable, paginated and globally sorted", async ({
  page,
}) => {
  await page.route("**/api/tickers?exchange=bybit", async (route) => {
    const response = await route.fetch();
    const snapshot = await response.json();
    const seed = snapshot.tickers[0];
    snapshot.tickers = Array.from({ length: 782 }, (_, i) => ({
      ...seed,
      symbol: `COIN${String(i).padStart(3, "0")}USDT`,
      base_coin: `COIN${i}`,
      turnover_24h: i + 1,
    }));
    await route.fulfill({ response, json: snapshot });
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "行情中心" }).click();
  const board = page.locator(".market-board");
  await expect(board.getByText("匹配 782 / 全市场 782 个交易对")).toBeVisible();
  await expect(board.locator(".el-table__body-wrapper tbody tr")).toHaveCount(
    50,
  );
  await expect(board.locator(".symbol-link").first()).toContainText(
    "COIN781USDT",
  );
  await board.locator(".btn-next").click();
  await expect(board.locator(".symbol-link").first()).toContainText(
    "COIN731USDT",
  );
  await board.getByPlaceholder("搜索币种，例如 BTC、DOGE").fill("coin000");
  await expect(
    board.getByRole("button", { name: "查看 COIN000USDT" }),
  ).toBeVisible();
  await expect(board.getByText("匹配 1 / 全市场 782 个交易对")).toBeVisible();
  await expect(board.getByText(/请求耗时 \d+ ms/)).toBeVisible();
});

test("personal workspace: direct entry, strategy lifecycle, research and responsive pages", async ({
  page,
}) => {
  test.setTimeout(90000);
  // Keep UI assertions independent of the user's actual selected environment.
  await page.route("**/api/bybit-environment", (route) =>
    route.fulfill({
      json: {
        active: "testnet",
        selected: "testnet",
        restart_required: false,
        testnet_credentials_configured: true,
        mainnet_credentials_configured: true,
        switch_available: true,
      },
    }),
  );
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "总览", exact: true }),
  ).toBeVisible();
  await expect(page.getByPlaceholder("输入平台访问凭证")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "锁定工作台" })).toHaveCount(0);
  await expect(page.getByText("在线", { exact: true })).toHaveCount(2, {
    timeout: 20000,
  });
  await expect(page.locator("canvas")).toBeVisible();
  mkdirSync("../artifacts/screenshots", { recursive: true });
  await page.screenshot({
    path: "../artifacts/screenshots/overview.png",
    fullPage: true,
  });

  await page.getByRole("button", { name: "创建策略", exact: true }).click();
  await page
    .getByPlaceholder("例如：BTC 15 分钟趋势策略")
    .fill("BTC 私人趋势研究");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建策略", exact: true })
    .click();
  await expect(page.getByText("策略已创建，当前为暂停状态")).toBeVisible();
  await page.locator("nav").getByRole("button", { name: "策略管理" }).click();
  await expect(
    page.getByText("BTC 私人趋势研究", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "启动", exact: true }).click();
  await expect(page.getByText("运行中", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(page.getByText("已暂停", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "平仓并暂停", exact: true }).click();
  await page.getByRole("button", { name: "确认平仓", exact: true }).click();
  await expect(page.getByText("已暂停", { exact: true })).toBeVisible({
    timeout: 20000,
  });

  await page.locator("nav").getByRole("button", { name: "研究与回测" }).click();
  await page
    .locator(".research-form .el-form-item")
    .filter({ hasText: "快 EMA" })
    .getByRole("spinbutton")
    .fill("10");
  await page
    .locator(".research-form .el-form-item")
    .filter({ hasText: "慢 EMA" })
    .getByRole("spinbutton")
    .fill("40");
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(page.getByText("已完成", { exact: true })).toBeVisible({
    timeout: 25000,
  });
  await expect(
    page.getByRole("heading", { name: /回测权益（含浮动盈亏）/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "回测成交", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/screenshots/research.png",
    fullPage: true,
  });

  await page.getByRole("button", { name: "用这次回测创建模拟策略" }).click();
  await page.locator(".el-message-box input").fill("BTC 回测转模拟");
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "创建", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "BTC 回测转模拟 · 运行详情" }),
  ).toBeVisible();
  await expect(page.getByText("来自回测 #1", { exact: false })).toBeVisible();
  const promotedRow = page
    .locator(".el-table__body-wrapper tr")
    .filter({ hasText: "BTC 回测转模拟" });
  await promotedRow.getByRole("button", { name: "启动", exact: true }).click();
  await expect(page.locator("canvas")).toBeVisible({ timeout: 20000 });
  await promotedRow.getByRole("button", { name: "暂停", exact: true }).click();
  await expect(promotedRow.getByText("已暂停", { exact: true })).toBeVisible();
  await page.screenshot({
    path: "../artifacts/screenshots/strategy-detail.png",
    fullPage: true,
  });

  await page.locator("nav").getByRole("button", { name: "研究与回测" }).click();
  await page
    .locator(".research-form .el-form-item")
    .filter({ hasText: "数据范围" })
    .locator(".el-select")
    .click();
  await page.getByRole("option", { name: "历史起止日期" }).click();
  await page.getByPlaceholder("开始日期", { exact: true }).fill("2026-01-01");
  await page.getByPlaceholder("结束日期", { exact: true }).fill("2026-02-01");
  await page.getByPlaceholder("结束日期", { exact: true }).press("Enter");
  await page
    .getByText("多组 EMA 参数对比（最多 9 组）", { exact: true })
    .click();
  await page.getByPlaceholder("10,20,30").fill("10");
  await page.getByPlaceholder("40,60,90").fill("40,60");
  await page.getByRole("button", { name: "开始回测" }).click();
  await expect(
    page.locator(".job-row").getByText("已完成", { exact: true }),
  ).toHaveCount(3, { timeout: 30000 });
  await expect(
    page.getByRole("heading", { name: "回测结果对比" }),
  ).toBeVisible();
  await expect(page.getByText(/数据 .*指纹/)).toBeVisible();
  await page.screenshot({
    path: "../artifacts/screenshots/research-comparison.png",
    fullPage: true,
  });

  await page.locator("nav").getByRole("button", { name: "行情中心" }).click();
  const board = page.locator(".market-board");
  await expect(
    board.getByRole("heading", { name: "多币种行情" }),
  ).toBeVisible();
  await expect(board.getByText(/BYBIT .*16 个交易对/)).toBeVisible();
  await board.getByPlaceholder("搜索币种，例如 BTC、DOGE").fill("doge");
  await expect(
    board.getByRole("button", { name: "查看 DOGEUSDT" }),
  ).toBeVisible();
  await board.getByRole("button", { name: "加入自选 DOGEUSDT" }).click();
  await expect(board.getByText("我的自选（1）", { exact: true })).toBeVisible();
  await board.getByText("我的自选（1）", { exact: true }).click();
  await board.getByRole("button", { name: "查看 DOGEUSDT" }).click();
  await expect(
    page.getByRole("heading", { name: "DOGEUSDT · 单币详情" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "已收盘行情" })).toBeVisible();
  await page.screenshot({
    path: "../artifacts/screenshots/market.png",
    fullPage: true,
  });
  await page.reload();
  await page.locator("nav").getByRole("button", { name: "行情中心" }).click();
  await expect(
    board.getByRole("button", { name: "移除自选 DOGEUSDT" }),
  ).toBeVisible();
  await page.locator(".market-controls .el-select").nth(0).click();
  await page.getByRole("option", { name: "Binance" }).click();
  await expect(board.getByText("我的自选（0）", { exact: true })).toBeVisible();
  await board.getByPlaceholder("搜索币种，例如 BTC、DOGE").fill("DOGE");
  await board.getByRole("button", { name: "加入自选 DOGEUSDT" }).click();
  await expect(board.getByText("我的自选（1）", { exact: true })).toBeVisible();
  await page.locator(".market-controls .el-select").nth(0).click();
  await page.getByRole("option", { name: "Bybit" }).click();
  await expect(
    board.getByRole("button", { name: "移除自选 DOGEUSDT" }),
  ).toBeVisible();
  await board.getByRole("button", { name: "移除自选 DOGEUSDT" }).click();
  await expect(board.getByText("我的自选（0）", { exact: true })).toBeVisible();
  await expect(page.locator("canvas")).toBeVisible();
  await page.locator("nav").getByRole("button", { name: "策略管理" }).click();
  await page.getByRole("button", { name: "创建策略", exact: true }).click();
  await page
    .getByPlaceholder("例如：BTC 15 分钟趋势策略")
    .fill("DOGE 多币种研究");
  await page.getByRole("dialog").locator(".el-select").nth(1).click();
  await page.getByRole("option", { name: "DOGEUSDT", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "创建策略", exact: true })
    .click();
  await expect(
    page.getByText("DOGE 多币种研究", { exact: true }),
  ).toBeVisible();
  await page.locator("nav").getByRole("button", { name: "持仓与订单" }).click();
  await expect(
    page.getByRole("heading", { name: "模拟成交记录" }),
  ).toBeVisible();
  await page.locator("nav").getByRole("button", { name: "交易所账户" }).click();
  await page.getByRole("button", { name: "查询余额" }).click();
  await expect(page.getByText("演示模式不访问交易所账户")).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Bybit 主网 / 测试网" }),
  ).toBeVisible();
  await expect(
    page
      .locator(".bybit-environment-panel")
      .getByText("当前：测试网", { exact: true }),
  ).toBeVisible();
  // Exercise the switch UI without changing the user's local configuration.
  await page.route("**/api/bybit-environment", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    expect(route.request().postDataJSON()).toEqual({ environment: "mainnet" });
    return route.fulfill({
      json: {
        active: "testnet",
        selected: "mainnet",
        restart_required: true,
        testnet_credentials_configured: true,
        mainnet_credentials_configured: true,
        switch_available: true,
      },
    });
  });
  await page.locator(".bybit-environment-panel .el-select").click();
  await page.getByRole("option", { name: "主网", exact: true }).click();
  await page.getByRole("button", { name: "保存环境，重启后生效" }).click();
  await expect(page.getByText(/已保存主网，当前仍为测试网/)).toBeVisible();
  await expect(
    page
      .locator(".bybit-environment-panel")
      .getByText("当前：测试网", { exact: true }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("nav").getByRole("button", { name: "行情中心" }).click();
  await expect(
    board.getByRole("heading", { name: "多币种行情" }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.screenshot({
    path: "../artifacts/screenshots/market-mobile.png",
    fullPage: true,
  });
  await page
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "总览", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: "../artifacts/screenshots/mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth + 1,
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "总览", exact: true }),
  ).toBeVisible();
  await expect(page.getByPlaceholder("输入平台访问凭证")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("automatic parameter search: independent validation, candidate review and paper promotion", async ({
  page,
}) => {
  test.setTimeout(120000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "研究与回测" }).click();
  await page.locator(".research-form .rule-fields .el-select").first().click();
  await page.getByRole("option", { name: "EMA 双均线", exact: true }).click();
  const panel = page.locator(".optimization-panel");
  await panel.getByText("配置搜索范围与验证条件", { exact: true }).click();
  await panel
    .getByPlaceholder("优化开始日期", { exact: true })
    .fill("2026-01-01");
  await panel
    .getByPlaceholder("优化结束日期", { exact: true })
    .fill("2026-02-01");
  await panel.getByPlaceholder("优化结束日期", { exact: true }).press("Enter");
  await panel.getByPlaceholder("切分日期", { exact: true }).fill("2026-01-16");
  await panel.getByPlaceholder("切分日期", { exact: true }).press("Enter");
  await panel
    .locator(".el-form-item")
    .filter({ hasText: "搜索次数" })
    .getByRole("spinbutton")
    .fill("8");
  await panel
    .locator(".el-form-item")
    .filter({ hasText: "最低平仓次数" })
    .getByRole("spinbutton")
    .fill("1");
  await panel
    .locator(".el-form-item")
    .filter({ hasText: "最大允许回撤" })
    .getByRole("spinbutton")
    .fill("100");
  await panel.getByText("每个验证窗口收益必须为正", { exact: true }).click();
  await panel
    .getByRole("spinbutton", { name: "快 EMA搜索下限", exact: true })
    .fill("5");
  await panel
    .getByRole("spinbutton", { name: "快 EMA搜索上限", exact: true })
    .fill("10");
  await panel
    .getByRole("spinbutton", { name: "慢 EMA搜索下限", exact: true })
    .fill("30");
  await panel
    .getByRole("spinbutton", { name: "慢 EMA搜索上限", exact: true })
    .fill("40");
  await panel.getByRole("button", { name: "开始自动优化" }).click();
  await expect(
    panel.getByRole("heading", { name: "优化任务 #1 · 已完成" }),
  ).toBeVisible({ timeout: 45000 });
  await expect(panel.getByText("研究与验证通过", { exact: true })).toHaveCount(
    5,
  );
  const candidateRow = panel
    .locator(".optimization-result .el-table__body-wrapper tbody tr")
    .first();
  await candidateRow.locator(".el-table__expand-icon").click();
  await expect(panel.getByText(/窗口 1：/)).toBeVisible();
  await panel
    .locator(".candidate-details")
    .getByText("稳健性诊断：成本、邻近参数与滚动验证", { exact: true })
    .click();
  await expect(
    panel.getByRole("heading", { name: "成本压力 · 独立验证区间" }),
  ).toBeVisible();
  await expect(
    panel.getByRole("heading", { name: "滚动样本外验证", exact: true }),
  ).toBeVisible();
  await panel
    .getByRole("button", { name: "查看验证回测", exact: true })
    .first()
    .click();
  await expect(page.getByText(/EMA 双均线.*验证区间/)).toBeVisible();
  await page.screenshot({
    path: "../artifacts/screenshots/optimization.png",
    fullPage: true,
  });
  await panel
    .getByRole("button", { name: "创建模拟策略", exact: true })
    .first()
    .click();
  await page.locator(".el-message-box input").fill("自动选参候选");
  await page
    .locator(".el-message-box")
    .getByRole("button", { name: "创建", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "自动选参候选 · 运行详情" }),
  ).toBeVisible();
  await page.locator("nav").getByRole("button", { name: "研究与回测" }).click();
  await expect(
    panel.getByRole("heading", { name: "优化任务 #1 · 已完成" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect
    .poll(() =>
      page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth + 1,
      ),
    )
    .toBeTruthy();
  await page.screenshot({
    path: "../artifacts/screenshots/optimization-mobile.png",
    fullPage: true,
  });
  expect(errors).toEqual([]);
});

test("curves load only in open details and archive requires a reviewed preview", async ({
  page,
  request,
}) => {
  const created = await request.post("/api/strategies", {
    data: { name: "按需曲线测试" },
  });
  expect(created.ok()).toBeTruthy();
  const strategy = await created.json();
  await request.post(`/api/strategies/${strategy.id}/command`, {
    data: { action: "start" },
  });
  const curveRequests: string[] = [];
  page.on("request", (req) => {
    if (req.url().includes(`/strategies/${strategy.id}/curve`))
      curveRequests.push(req.url());
  });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "总览", exact: true }),
  ).toBeVisible();
  expect(curveRequests).toHaveLength(0);
  await page.locator("nav").getByRole("button", { name: "策略管理" }).click();
  const row = page
    .locator(".el-table__row")
    .filter({ hasText: "按需曲线测试" });
  await row.getByRole("button", { name: "详情", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "按需曲线测试 · 运行详情" }),
  ).toBeVisible();
  await expect.poll(() => curveRequests.length).toBeGreaterThan(0);
  await expect(
    page.getByText(/已有持仓仍执行止损、止盈及最大回撤保护/),
  ).toBeVisible();
  await expect
    .poll(() => curveRequests.some((url) => /since_id=[1-9]/.test(url)), {
      timeout: 15000,
    })
    .toBeTruthy();
  await page.getByRole("button", { name: "收起", exact: true }).click();
  const count = curveRequests.length;
  await page.waitForTimeout(5500);
  expect(curveRequests).toHaveLength(count);
  await request.post(`/api/strategies/${strategy.id}/command`, {
    data: { action: "pause" },
  });

  await page
    .locator("nav")
    .getByRole("button", { name: "总览", exact: true })
    .click();
  await expect(page.getByRole("button", { name: /确认归档/ })).toHaveCount(0);
  await page.getByRole("button", { name: "预览归档" }).click();
  await expect(page.getByText("当前没有需要归档的明细。")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "确认归档 0 条明细" }),
  ).toBeDisabled();
  const planId = "a".repeat(32);
  await page.route("**/api/maintenance/archive/preview", (route) =>
    route.fulfill({
      json: {
        id: planId,
        cutoff: "2026-01-01T00:00:00Z",
        counts: { equity: 0, events: 2, backtests: 0, optimizations: 0 },
        batch_limit: 10000,
      },
    }),
  );
  let confirmed = false;
  await page.route("**/api/maintenance/archive/execute", (route) => {
    expect(route.request().postDataJSON()).toEqual({ id: planId });
    confirmed = true;
    return route.fulfill({
      json: { manifest: "storage/archive/test/manifest.json" },
    });
  });
  await page.getByRole("button", { name: "预览归档" }).click();
  await expect(
    page.getByRole("button", { name: "确认归档 2 条明细" }),
  ).toBeEnabled();
  expect(confirmed).toBeFalsy();
  await page.getByRole("button", { name: "确认归档 2 条明细" }).click();
  await expect(page.getByText(/归档完成，校验清单保存在/)).toBeVisible();
  expect(confirmed).toBeTruthy();
});
