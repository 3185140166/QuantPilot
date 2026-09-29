# 安全规则

- Bybit 和 Binance key 只保存在本地 `.env`。
- GitHub PAT 只放在 `pat.env`，不能当作交易所凭据。
- 不要提交 `.env`、`pat.env`、`api.md`、PEM 私钥、日志或交易 CSV。
- 交易所 key 只授予必要权限，禁止提现。
- Testnet key 和主网 key 完全不同。
- 在 Testnet 执行、订单对账和故障恢复完成前，保持 `DRY_RUN=true` 和 `TRADING_ENABLED=false`。
- 如果 key 曾经暴露，应立即在交易所撤销并重新生成。
