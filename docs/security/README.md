# Security

- Keep Bybit and Binance keys in local `.env` only.
- Keep the GitHub PAT in `pat.env`; never use it as an exchange credential.
- Do not commit `.env`, `pat.env`, `api.md`, PEM files, logs, or CSV trade exports.
- Exchange keys must have the minimum required permissions and no withdrawal permission.
- Testnet and mainnet keys are different.
- Keep `DRY_RUN=true` and `TRADING_ENABLED=false` until Testnet execution, order reconciliation, and recovery tests are complete.
- If a key was exposed, revoke it at the exchange and create a replacement.\n