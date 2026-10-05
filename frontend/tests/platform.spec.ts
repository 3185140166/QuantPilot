import { expect, test } from "@playwright/test";
import { mkdirSync } from "node:fs";

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
