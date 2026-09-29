# 运维说明

安全的只读检查命令：

```powershell
python -m quantpilot.cli connection --env-file .env
python -m quantpilot.cli signal --env-file .env
python -m quantpilot.cli balance --env-file .env
```

`connection` 和 `signal` 使用公开行情；`balance` 发送签名的私有只读请求。开发时保持：

```env
DRY_RUN=true
TRADING_ENABLED=false
```

连接超时或代理错误属于网络问题；交易所返回 JSON 错误或 HTTP 401，通常属于 key、权限、环境或本机时间问题。
