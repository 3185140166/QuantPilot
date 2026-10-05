# 私人量化平台第一版

Vue 3 / TypeScript / Element Plus / ECharts 前端，FastAPI 接口，SQLAlchemy 数据层，Alembic 迁移，独立 Python 模拟交易引擎与独立回测进程。Docker 部署使用 PostgreSQL；Windows 本地启动默认使用 SQLite，方便先联调。

## Windows 本地启动

当前工作区已安装依赖并生成本地平台配置，可直接在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m quantpilot.platform.launch
```

打开 http://127.0.0.1:5173 即可直接进入工作台，默认个人模式无需访问凭证。一个终端会同时管理 API、模拟交易引擎、回测进程和前端；Ctrl+C 停止全部子进程，日志保存到 `storage/logs/`。关闭启动终端会终止本地服务；长期运行请使用下方 Docker 方式。

在新机器首次安装，或希望分别启动服务时，使用以下步骤。

在项目根目录安装后端并生成独立平台配置（不会修改交易所 `.env`）：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[platform,dev]"
.\.venv\Scripts\python.exe -m quantpilot.platform.bootstrap
.\.venv\Scripts\python.exe -m alembic upgrade head
```

打开四个 PowerShell 终端。前三个保持项目根目录，分别启动：

```powershell
# 终端 1：API
.\.venv\Scripts\python.exe -m uvicorn quantpilot.platform.api:create_app --factory --host 127.0.0.1 --port 8000

# 终端 2：独立模拟交易引擎
.\.venv\Scripts\python.exe -m quantpilot.platform.engine

# 终端 3：独立回测进程
.\.venv\Scripts\python.exe -m quantpilot.platform.research_worker
```

第四个终端启动前端：

```powershell
Set-Location frontend
npm.cmd ci
npm.cmd run dev
```

打开 http://127.0.0.1:5173 即可直接进入。`.env.platform` 默认 `PLATFORM_AUTH_ENABLED=false`，页面和 API 都不要求平台凭证；本地启动仍只绑定 `127.0.0.1`。

若以后需要访问验证，可将 `PLATFORM_AUTH_ENABLED=true`，填写至少 32 字符的 `PLATFORM_ACCESS_TOKEN` 并重启。开启后页面才显示凭证输入与锁定按钮，API 强制校验凭证。平台凭证与交易所 API Key 不同。

## 行情与账户环境

行情中心新增多币种列表、搜索、排序和数据库自选。真实行情模式动态读取交易所当前可用的 USDT 永续合约，演示模式提供 16 个模拟交易对。点击行内交易对或“查看”进入单币 K 线，策略与回测的交易对选择同样来自动态目录。

星标自选按 Bybit / Binance 分别保存，刷新页面和重启服务后保留。行情快照每 15 秒更新，服务端快照缓存 10 秒，交易对目录缓存 5 分钟；已下架或缺少行情的自选会提示并允许移除。升级旧工作区时运行 `python -m alembic upgrade head`，统一启动器会自动执行迁移。

默认 `PLATFORM_MARKET_SOURCE=demo`，使用合成价格，不访问交易所账户。界面明确显示演示标记，没有默认策略或虚构成交。

要读取交易所真实行情，将 `.env.platform` 改为：

```env
PLATFORM_MARKET_SOURCE=exchange
```

然后重启 API、引擎与回测进程。`.env` 决定交易所环境与凭据。如果使用 Bybit 主网密钥：

```env
EXCHANGE=bybit
BYBIT_TESTNET=false
LIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING
DRY_RUN=true
TRADING_ENABLED=false
```

账户查询只在页面点击“查询余额”时请求私有只读接口。平台不返回 API Key 或 Secret。

策略和回测任务绑定创建时的行情来源。切换来源后，旧来源策略不会混用价格；请在新来源下创建策略与回测。所有策略仍然只执行本地模拟订单。

## 操作语义与恢复

- 创建策略：独立模拟资金账户，默认暂停；可选择 EMA / RSI、EMA 双均线、RSI 均值回归、区间突破、布林带回归，保存配置 v1 参数快照，支持成交量、ATR、ADX 过滤。
- 启动：引擎轮询行情，仅对最新已收盘 K 线计算一次信号。成交价采用本次轮询观察到的最新行情价格并施加滑点；不假设过去开盘价仍可成交。
- 暂停：停止新信号与交易，保留持仓；已有持仓仍更新标记价格。
- 平仓并暂停：保存平仓意图，引擎取得行情后完成模拟平仓。行情不可用时保留意图并重试，不伪造平仓成功。
- 费用：开仓费即时扣除；平仓现金变化为毛盈亏减平仓费。两笔现金变化合计为完整交易净盈亏。
- 风控：不允许超过实例最大名义金额与模拟可用资金的开仓；支持本地模拟止损止盈及最大回撤平仓暂停，未模拟杠杆保证金、资金费率、强平及交易所止损。
- 恢复：数据库保存状态、持仓、最后处理的信号 K 线；重启不补做错过的历史信号，不重复已提交的模拟成交。
- 单实例：引擎与回测进程分别使用数据库租约，租约有效期 120 秒。强制终止后，替代进程最多等待 120 秒接管；所有成交与游标在同一事务中落库。
- 回测：API 仅排队，独立回测进程计算；价格样本保存到 `storage/market/*.parquet`。进程中断后可重算未完成任务。
- 心跳：页面显示数据库租约状态，不表示已验证交易所连通性。市场处理异常会显示在策略与事件记录中。

新回测使用每根收盘的盯市权益，包含持仓浮动盈亏、手续费与滑点，下一根开盘成交、样本末尾平仓。旧回测记录仍按原有口径显示。历史日期区间支持分页拉取、预热与严格完整性检查；参数批次固定相同数据区间，最多 9 组。每个结果记录配置、样本指纹和风控拒绝次数，可以沿用配置创建暂停的模拟策略。验证标签仅记录用途，未自动执行样本外拆分。

自动优化独立于上述手动标签：`POST /api/optimizations` 自动按切分日期分开研究与验证，只把研究评分传给 Optuna TPE。最多 200 次试验，保留 5 个不同候选；验证支持 1–3 个固定参数、独立资金的连续窗口。结果、进度、试验检查点落库，研究进程共享租约串行处理普通回测与优化任务。取消、恢复和行情指纹校验均由服务端执行。`GET /api/optimization-space` 返回当前模板允许的搜索范围。升级至 `0004_optimization` 并更新 platform 依赖后使用。

## PostgreSQL / Docker 部署

安装 Docker 后，在根目录执行：

```powershell
docker compose --env-file .env.platform up --build -d
```

打开 http://127.0.0.1:8080 。Compose 包含 PostgreSQL、迁移、API、引擎、回测进程与 Nginx 前端，数据库与行情使用持久化卷。`POSTGRES_PASSWORD` 由 bootstrap 生成；没有默认数据库密码。

前端端口仅绑定本机。远程私人访问可使用 SSH 隧道转发本机端口；如公开到网络，需单独配置 HTTPS 和访问控制。本次没有部署到公网。

若已有 `.env.platform` 而缺少 `POSTGRES_PASSWORD`，请填写随机长密码。密码用于 Compose URL，建议使用字母数字或十六进制，避免未编码特殊字符。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check quantpilot tests migrations
Set-Location frontend
npm.cmd run build
npm.cmd run test:e2e
```

测试完全离线，覆盖 API 鉴权、参数校验、策略指令、模拟成交幂等、反手、风控拒绝、暂停和平仓恢复、回测任务恢复及下单门控。Docker/PostgreSQL 需要单独进行实际环境验证。

端到端测试会自动启动隔离测试服务（端口 8011、5174），使用独立临时数据库和演示行情；不修改平台数据或连接交易所。Windows 使用已安装的 Edge 无头浏览器，其他系统先运行 `npx playwright install chromium`。截图位于 `artifacts/screenshots/`。

## 后续实盘阶段

这是能运行和扩展的平台第一版，不是完成实盘验收的交易系统。下一阶段需要实现交易所 WebSocket 事件流、真实订单状态机、成交与仓位对账、合约过滤规则、交易所止损、限频、断线恢复、幂等订单重试、账户资金与多策略总风险约束，以及 Testnet 故障演练。当前 API 没有真实下单入口。
