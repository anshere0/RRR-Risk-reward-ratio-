# 06 — Safety and Risk Limits

1. **No order placement code. No trading API keys.** The engine only reads public market data. Reject any request to add order placement in v1.
2. **Fail closed.** Missing/stale/invalid data -> NO_TRADE. Exceptions inside the engine -> NO_TRADE with `ENGINE_ERROR`, logged. Never show a half-computed number.
3. **Always show provenance**: symbol analysed, index price, data age (s), computed-at time, region, config hash. A wrong-symbol or stale card must be visible at a glance.
4. **UNVALIDATED banner** is shown until the go-live criteria are met. It cannot be dismissed.
5. **Long options only.** Maximum loss is the premium paid. Refuse sell/short evaluations.
6. **Sizing**: if the user sets `max_risk_usd`, the HUD shows `max_lots`; if risk_usd (as entered lots) exceeds it -> `RISK_OVER_BUDGET`.
   Suggested personal rule (yours to set): risk <= 1-2 % of trading capital per trade, daily loss limit 3-5 %.
7. **Secrets**: none required. `.env` is git-ignored. Logs must never contain keys.
8. **Rounding**: round entry up / targets down to tick (conservative); never round in the user's favour.
9. **Config changes** require restart and are hashed into every journal entry.
10. **Not advice**: the HUD footer says "Decision support, not financial advice. Options can lose 100% of premium."
11. **Short-dated options warning**: if expiry < 24 h show a permanent amber note: "Time decay and gamma are extreme."
12. **Local only**: bind to 127.0.0.1; CORS allows only the extension origin and localhost.
