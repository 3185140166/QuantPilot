# QuantPilot

行情列表支持全部可用 USDT 永续交易对，按 20 / 50 / 100 条分页。搜索、排序针对全部结果，自选独立保存。页面显示请求耗时与快照获取时间；行情每 15 秒刷新，服务端快照缓存 10 秒，因此请求耗时不代表行情实时性或订单执行延迟。

个人使用的量化研究、模拟交易与 Bybit 测试网交易平台：Vue 3 + TypeScript + Element Plus + ECharts 前端，FastAPI 后端，独立 Python 交易引擎和回测进程，SQLAlchemy / Alembic 数据层。

本地开发使用 SQLite，Docker 部署使用 PostgreSQL。默认个人模式直接进入页面，无需凭证；交易所凭据只保存在服务端 `.env`。原 Streamlit 研究页面继续保留。

## 当前功能

- 多币种行情：Bybit / Binance 当前可用的 USDT 永续合约列表、最新价格、24 小时涨跌与成交额，支持搜索和排序。
- 我的自选：点击星标添加或移除，按交易所分别保存到数据库，刷新页面和重启后保留。
- 单币详情：从行情列表点击交易对或“查看”，切换已收盘 K 线和策略信号。
- 策略管理：EMA / RSI、双均线、RSI 均值回归、区间突破、布林带回归五种模板，可配置参数、成交量／ATR／ADX 过滤、止损止盈和回撤暂停。
- 异步回测：独立进程计算并保存结果、权益曲线、成交明细与 Parquet 行情样本。
- 账户只读查询、模拟持仓订单、操作记录和 CSV 导出。

真实行情模式读取交易所批量行情；演示模式提供 16 个模拟币种并明确标识合成数据。多币种列表每 15 秒刷新，服务器价格快照缓存 10 秒，交易对目录缓存 5 分钟。单币 K 线手动刷新。“策略管理”执行本地模拟；“交易助手”可单独启用 Bybit 测试网实际订单，使用测试币。主网真实资金自动下单未开放。

## 快速启动（已安装依赖）

在项目根目录的 PowerShell 中执行：

```powershell
.\.venv\Scripts\python.exe -m quantpilot.platform.launch
```

打开 **http://127.0.0.1:5173**。一个终端管理前端、API、本地模拟引擎、独立测试网执行与风控、回测进程共五个服务，并自动执行最新数据库迁移。已有 SQLite 数据库升级前自动备份到 `storage/backups/`。按 `Ctrl+C` 停止；修改配置后需停止并重新启动。日志在 `storage/logs/`。

## 首次安装

需要 Python 3.10+ 和 Node.js 22.12+（建议 Node.js 24）。以下命令无需激活虚拟环境，避免 PowerShell 脚本执行策略问题：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[platform,dev]"
npm.cmd --prefix frontend ci
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m quantpilot.platform.bootstrap
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m quantpilot.platform.launch
```

`bootstrap` 只创建缺失的 `.env.platform`，不会覆盖已有配置或 `.env`。不要提交 `.env`、`.env.platform`、`pat.env` 或密钥文件。

## 演示行情与真实账户

平台配置在 `.env.platform`：

```env
PLATFORM_AUTH_ENABLED=false
PLATFORM_MARKET_SOURCE=demo
DATABASE_URL=sqlite:///storage/platform.db
PLATFORM_POLL_SECONDS=10
```

- `demo`：合成行情与本地模拟账户；“交易所账户”不会请求真实余额。
- `exchange`：真实交易所行情；点击“查询余额”时读取真实账户。策略成交仍是模拟订单。

要连接真实行情，把 `.env.platform` 的 `PLATFORM_MARKET_SOURCE` 改为 `exchange` 并重启。Bybit 主网密钥对应的 `.env` 配置为：

```env
EXCHANGE=bybit
BYBIT_API_KEY=填写你的密钥
BYBIT_API_SECRET=填写你的密钥对应的Secret
BYBIT_TESTNET=false
LIVE_TRADING_CONFIRM=I_UNDERSTAND_LIVE_TRADING
BYBIT_CATEGORY=linear
BYBIT_SYMBOL=BTCUSDT
BYBIT_INTERVAL=15
DRY_RUN=true
TRADING_ENABLED=false
```

测试网密钥使用 `BYBIT_TESTNET=true`；Binance 由 `BINANCE_API_KEY`、`BINANCE_API_SECRET`、`BINANCE_TESTNET` 配置。交易所环境与密钥必须匹配。

策略和回测任务绑定创建时的行情来源，切换 `demo` / `exchange` 后请在新来源下创建实例。自选是按交易所保存的个人偏好，不受行情来源切换影响；当前无报价的自选会显示提示并允许移除。

## 常用命令

以下 Python 命令均在项目根目录运行。

### 交易所只读检查

```powershell
# 公开行情连接（不验证密钥）
.\.venv\Scripts\python.exe -m quantpilot.cli connection --env-file .env
# 私有余额接口（验证密钥与读取权限，不下单）
.\.venv\Scripts\python.exe -m quantpilot.cli balance --env-file .env
# 已收盘 K 线策略信号
.\.venv\Scripts\python.exe -m quantpilot.cli signal --env-file .env
```

这些 CLI 命令按 `.env` 选择交易所与交易对，不使用 `.env.platform` 的演示行情设置。

### 分别启动服务

每个长期运行命令使用一个独立终端。已用统一启动命令时无需再运行：

```powershell
# 后端 API（端口 8000）
.\.venv\Scripts\python.exe -m uvicorn quantpilot.platform.api:create_app --factory --host 127.0.0.1 --port 8000
# 模拟交易引擎
.\.venv\Scripts\python.exe -m quantpilot.platform.engine
# 测试网执行与风控（独立进程，日志 testnet.log）
.\.venv\Scripts\python.exe -m quantpilot.platform.testnet_worker
# 回测进程
.\.venv\Scripts\python.exe -m quantpilot.platform.research_worker
# 前端（端口 5173）
npm.cmd --prefix frontend run dev
```

### 本机平台接口检查

在服务启动且个人模式未启用凭证验证时：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
Invoke-RestMethod http://127.0.0.1:8000/api/overview
# 列出交易对
Invoke-RestMethod 'http://127.0.0.1:8000/api/instruments?exchange=bybit'
# 查看成交额排序的前 10 个币种
Invoke-RestMethod 'http://127.0.0.1:8000/api/tickers?exchange=bybit' |
    Select-Object -ExpandProperty tickers |
    Select-Object -First 10 symbol, last_price, change_24h_pct, turnover_24h
# 查询自选
Invoke-RestMethod 'http://127.0.0.1:8000/api/watchlist?exchange=bybit'
```

### 数据库、测试与构建

```powershell
# 升级数据库：自动优化功能需要迁移到 0004_optimization
.\.venv\Scripts\python.exe -m alembic upgrade head
# 查看当前迁移 / 检查模型与数据库一致性
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
# Python 离线测试和代码检查
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check quantpilot tests migrations scripts
# 前端构建、浏览器流程测试、格式化
npm.cmd --prefix frontend run build
npm.cmd --prefix frontend run test:e2e
npm.cmd --prefix frontend run format
```

浏览器测试使用隔离数据库、演示行情与独立端口（8011、5174），不会读取交易所账户。Windows 使用本机 Edge，其他系统先安装 Playwright Chromium。截图位于 `artifacts/screenshots/`。

### 原 Streamlit 研究页面

```powershell
.\.venv\Scripts\python.exe -m streamlit run quantpilot/dashboard.py
```

### Docker / PostgreSQL

```powershell
docker compose --env-file .env.platform up --build -d
docker compose --env-file .env.platform ps
docker compose --env-file .env.platform logs --tail 100 api engine research
docker compose --env-file .env.platform down
```

打开 **http://127.0.0.1:8080**。数据库与研究数据保存在 Docker 卷中，`down` 不删除卷。`POSTGRES_PASSWORD` 由 bootstrap 生成。当前工作区未进行 Docker 实机验证。

## 实现边界与文档

平台已接入 Bybit 测试网下单、持仓和订单对账，尚未开放主网自动执行。新回测采用固定数量、每根收盘盯市权益，包含手续费、滑点和收盘触发的止损止盈／最大回撤限制，不包含资金费率、杠杆保证金、强平或盘中触价模型。旧回测保留原有权益口径。

## Bybit 测试网自动交易

右上角滑动开关选择测试网／主网，保存后需重启平台；主网为只读模式。测试网专用密钥填写在 `.env` 的 `BYBIT_TESTNET_API_KEY`、`BYBIT_TESTNET_API_SECRET`，并设置 `BYBIT_TESTNET=true`、`EXCHANGE=bybit`、`BYBIT_CATEGORY=linear`。`.env.platform` 使用 `PLATFORM_MARKET_SOURCE=exchange` 与 `PLATFORM_TESTNET_EXECUTION_ENABLED=true`。

重启后打开“交易助手”，等待账户同步，在空仓且没有挂单时设置逐仓／单向模式。先领取足够测试 USDT，再保存预算与扫描币种、扫描信号，最后点击“启动测试网自动交易”。默认暂停，默认共用 200 USDT 预算，单笔计划风险 0.5%、组合风险 1%、日亏损暂停 2%、高点回撤暂停 5%，最多 2 个持仓、杠杆最多 2 倍，总名义金额不超过预算。费用和滑点计入仓位计划；最小订单超过预算则跳过，不强行提高数量。

开仓同时提交标记价格触发的交易所止损；暂停只停止新开仓，已提交订单可能继续成交，持仓保护与对账继续。平仓按钮仅退出本系统确认的持仓。检测到手工持仓或未知挂单会阻止新开仓。提交前持久化唯一订单编号，超时先查询原订单与逐笔成交，不自动重发。网页可请求撤单并暂停，交易所确认后释放通道。

交易助手展示资金账户余额、逐笔成交、资金流水与每个交易周期的净盈亏。已核实的净划转从钱包收益中扣除；流水延迟或不一致时暂停新开仓，仍处理已有持仓保护。请使用专门测试账户，手工交易仍影响账户统计。多币种优先最近成交额较高的有效信号，并默认用 0.85 的收益率相关性上限限制增加持仓。风险阈值是计划值，跳空、滑点及网络异常可能造成超出。

私有请求使用交易所时间与单调时钟签名，缓解本机时间偏差；同步失败时阻止请求。普通 `.env` 的 `DRY_RUN=true`、`TRADING_ENABLED=false` 保留，由专用测试网执行器在通过环境检查后提交测试订单。`PLATFORM_TESTNET_EXECUTION_ENABLED=false` 会停止执行进程的测试网下单与对账，已有交易所止损仍保留。

## 策略研究到模拟运行

交易助手支持从已完成的 Bybit 交易所行情验证回测预览并应用规则：先选择回测，核对币种、周期、完整规则、指标和数据环境，再确认仓位模型差异。应用只复制信号与成本配置，保留共享预算、按风险重新计算数量，并保持暂停。演示回测和未通过约束的优化候选不可应用；验证标签不等于盈利证明。来源与数据指纹保留在账户配置记录中，后续修改会标记来源已变更。

暂停后恢复持仓管理按账户权益、现有止损与组合风险核查，不要求已占用保证金后仍有完整预算可用。仓位归属要求可核对的逐笔成交时间线与剩余成本，已结束开仓退出认领范围；无法确认归属时不接管。交错的开平仓成交按时间和交易所序列更新成本，缺少可靠顺序时显示待核查。流水延迟时仍对已确认浮盈亏执行账户保护，未知现金变化待补账核实。

1. 在“研究与回测”选择策略模板，调整 EMA、RSI、数量、费用与风控。
2. 数据范围可以用最近 100–1000 根，或选择历史起止日期（本地时区，结束日期不包含）。日期区间自动补充指标预热，单次最多 100000 根、730 天；缺失 K 线或上市前数据会报错，不会悄悄缩短区间。
3. 趋势模板支持快／慢 EMA 候选值的组合对比，最多 9 组。每批固定同一行情区间和预热长度，历史数据在回测进程内缓存复用，文件保存到 `storage/market/` 并记录 SHA-256。
4. 比较收益、最大回撤和成交次数，在没有参与选参的新区间再跑一次，标记为“验证”。该标签仅记录用途，不会自动检测区间重叠或保证样本外验证。
5. 点击“用这次回测创建模拟策略”。币种、周期、策略参数、数量、资金、费用和风控原样保存，记录来源回测编号，实例默认暂停；研究与运行共用信号规则。
6. 到“策略管理”启动，在“详情”观察含浮动盈亏的权益曲线、全程最大回撤、持仓和异常。曲线展示最近 1000 个 K 线周期的记录；升级前的历史浮亏不能追溯补录。

暂停后停止新开仓和信号反手，保留持仓并继续执行止损、止盈及最大回撤保护；平仓并暂停退出持仓。模拟引擎按轮询价格触发止损止盈；触发最大回撤后平仓、暂停并阻止重新启动，应研究新配置后创建新实例。曲线按 K 线周期保存最新权益，最大回撤按每次轮询计算，因此同一根 K 线内的最深回撤可能不显示在曲线上。回测按收盘检查风险、下一根开盘成交，模拟轮询可能出现不同成交价。模板是可研究的规则，不是已验证盈利策略。

策略列表只加载摘要；展开“详情”后才加载最近 1000 个权益点，并每 5 秒增量刷新，收起详情或切换页面后停止曲线请求。演示模式的近期与历史已收盘 K 线共用确定性生成逻辑，包含一致的成交量；未收盘演示 K 线按时间进度变化。

在“总览”底部的“历史数据归档”中选择保留天数（默认 90 天，最少 30 天），先预览，再确认归档。旧权益／事件导出为 Parquet，已结束回测／优化明细保存为 JSON；回读校验及 SHA-256 校验通过后才移除数据库旧明细。任务编号、配置、汇总、持仓、订单、全程最大回撤与共享行情文件保留，归档结果仍可打开。备份时同时保存数据库、`storage/archive/` 和 `storage/market/`，详见[平台说明](docs/platform/README.md)。

更新项目后统一启动会自动执行迁移 `0007_testnet_recovery`；单独运行服务时先执行 `.\.venv\Scripts\python.exe -m alembic upgrade head`。安装或更新依赖：`.\.venv\Scripts\python.exe -m pip install -e ".[platform,dev]"`（包含 Optuna）。

## 自动选参和独立验证

在“研究与回测”的“自动选参 · 独立验证”中展开配置。沿用下方模板、币种、周期、数量、资金、手续费和固定因子设置，然后：

1. 选择完整历史区间及研究／验证切分日期。切分自动对齐到下一个 K 线边界；研究在切分前，验证在切分后，禁止重叠。每个区间至少 100 根。
2. 选择搜索参数、上下限与步长，最多 10 个维度、5–200 次搜索，默认 40 次。EMA、RSI、突破窗口或布林带参数按模板提供；止损止盈、成交量倍数、ATR 比例和 ADX 门槛可选。未勾选的参数沿用固定配置。
3. 选择目标“收益减回撤惩罚”或“收益”，设置最大允许回撤和最低平仓次数。Optuna TPE 的前 10 次为随机探索，后续根据研究结果采样；不足 10 次只执行随机探索。目标与约束都只计算研究区间。
4. 按研究评分保留最多 5 个不同配置，再运行独立验证。可把验证期分成 1–3 个连续、不重叠窗口；参数不重新选择，各窗口独立初始化资金和持仓。这是固定参数分窗验证，不是每窗重新训练的 walk-forward。
5. 查看研究／验证收益、含浮盈亏回撤、成交次数及未通过原因。所有研究与验证约束通过后，才允许从该候选直接创建暂停的模拟策略；验证明细通过“查看验证回测”打开。候选按研究评分排序，不用验证收益重新排序。

完整验证期的回测连续持仓，各分窗验证独立重置持仓，因此两种结果可能不同。默认要求各验证窗收益为正，可取消该要求，但回撤／成交约束仍生效。没有通过的候选会说明原因，不会强行推荐。因子为价格、成交量与技术指标，未接入资金费率、持仓量或链上数据。

任务保存在数据库中，显示搜索进度，可取消；运行中的取消会在下一次任务检查生效。每次试验检查点保存参数与研究统计，重启后恢复未完成任务，沿用有 SHA-256 指纹的原始 Parquet 行情。库版本变化或文件损坏时拒绝恢复。相同数据、依赖、种子和不中断的任务可复现；中途恢复保留已有试验，但后续采样不保证与不中断运行完全相同。

反复根据验证结果改参数，会让验证区间参与选参。应保留另一段从未用来选参的数据，并在模拟运行中观察；内置模板与自动优化都不保证盈利。

- [平台完整说明](docs/platform/README.md)：配置、恢复、服务运行与部署。
- [项目文档入口](docs/README.md)：架构、策略、回测、交易所和开发路线。
