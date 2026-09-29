# 可视化仪表盘

安装项目并启动本地只读研究页面：

```powershell
python -m pip install -e .
streamlit run quantpilot/dashboard.py
```

页面读取最近行情，展示最新已收盘价格、当前策略信号和基础回测摘要。页面没有下单控件，也不会修改 `DRY_RUN` 或 `TRADING_ENABLED`。
