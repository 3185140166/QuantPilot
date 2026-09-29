# QuantPilot

项目文档入口：[docs/README.md](docs/README.md)

QuantPilot 是一个以测试网优先的多交易所量化研究项目。当前第一阶段包括已收盘 K 线策略、基础回测、风险计算、交易所只读连接和本地研究仪表盘。实盘交易默认关闭。

## 安装

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

请在 `.env` 中填写专用的 Bybit 或 Binance 测试网密钥。开发期间保持 `DRY_RUN=true` 和 `TRADING_ENABLED=false`。不要提交 `.env`、`pat.env` 或任何包含密钥的文件。

## 常用检查

```powershell
python -m pytest
python -m quantpilot.cli connection
python -m quantpilot.cli signal
python -m quantpilot.cli balance
streamlit run quantpilot/dashboard.py
```

`connection` 和 `signal` 读取公开行情；`balance` 验证私有只读 API，不会下单。仪表盘是只读研究页面。
