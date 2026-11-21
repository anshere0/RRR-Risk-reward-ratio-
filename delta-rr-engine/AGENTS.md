# AGENTS.md — rules for every task in this repository

This project handles **money-related decisions**. Correctness beats speed and cleverness.

## Absolute rules
1. Read `docs/00`–`docs/07` and `prompts/MASTER_PROMPT.md` before writing code. Specs in `docs/` are the source of truth.
2. **Never invent Delta API fields, channel names, units or fee numbers.** Use `docs/probe_output.json` and `docs/03_DELTA_API_NOTES.md`.
   If a fact is not verified, write a TODO tagged `VERIFY:` and make the code fail safe (NO_TRADE) rather than guess.
3. **No order placement, no trading keys, no auto-click.** Read-only public data only.
4. **Fail closed**: uncertain/stale/invalid -> `NO_TRADE`. Never print a number built on bad data.
5. `reference/rr_reference/` is the oracle for pricing, fees, indicators, stop/target and RR. Do not edit it. Production code must match
   `reference/tests/golden_vectors.json`. If you believe the reference is wrong, stop and explain with evidence.
6. Closed candles only; confirmed pivots only; no look-ahead anywhere (backtests included).
7. Every gate in `docs/04_NO_TRADE_RULES.md` needs a unit test. Every threshold comes from `config/settings.yaml` (no magic numbers).
8. Round conservatively to tick size (entry up, targets down). Never round in the user's favour.
9. Keep it simple: no new dependencies without a reason written in the PR/commit message.
10. After every change run the `verify` workflow (`.agent/workflows/verify.md`). Do not claim "done" without showing test output.

## Style
Typed Python 3.11, small pure functions, dataclasses/pydantic models, docstrings stating units (USD per BTC, years, decimals vs percent).
Plain-English `message` strings for every reason code (the user is not a quant).
