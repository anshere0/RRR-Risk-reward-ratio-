# Plain-English Overview

## The problem
On Delta Exchange you trade **options** (for example a put). You watch the **BTC chart** (left screen) but you actually
make or lose money on the **option's price** (right screen). The option price does not move 1:1 with BTC.
So "where should my stop and target be, in option price?" is not obvious.

## What the bot does
1. Reads the live BTC price and the live option price **from Delta's data feed** (more exact and faster than "looking" at the screen).
2. Looks at the last few hours of 5-minute BTC candles to find a sensible **stop** (where your idea is proven wrong)
   and **target** (where price will probably meet resistance/support).
3. Asks: "If BTC reaches the stop, what will this option be worth? If BTC reaches the target?" using an options pricing formula
   that is first **calibrated to the live option price**, so it matches what you see on screen right now.
4. Takes off the real costs: you buy at the higher price (ask) and sell at the lower price (bid), exchange fees, and the option
   losing value as time passes.
5. Shows you the real **risk : reward** after costs. If it is not good enough, or the market is dangerous, it says **NO TRADE** and why.

## Why not read the screen with AI vision?
Pixels can be misread (a 6 looks like a 5), the chart rescales, it is slow, and a screen does not show Greeks or IV.
Delta already sends the exact numbers. Screen reading is kept only as an optional "double check" that the symbol on your
screen matches the symbol the bot is analysing.

## Words you will see
| Word | Meaning |
|------|---------|
| Premium | The option's price |
| Bid / Ask | Price buyers pay (ask) / sellers get (bid). Ask is higher. The gap is the **spread** — a hidden cost |
| ATM | At the money: strike is close to the current BTC price |
| Greeks (delta, gamma, theta, vega) | How the option price reacts to BTC moves, to time, and to volatility |
| Theta / time decay | Option loses value as expiry gets closer, even if BTC does not move |
| IV | Implied volatility: how big a move the market is pricing in |
| ATR | Average True Range: how far a 5-minute candle typically moves |
| ADX | Trend strength (low = choppy/sideways) |
| Net RR | Reward divided by risk **after** spread, fees and decay |
| Break-even win rate | How often you must win for this setup to make money. RR 1:2 needs 33%+, RR 1:1 needs 50%+ |

## The honest part
* A formula can make the **numbers accurate**; it cannot make **BTC go your way**. The bot's value is: no bad-shaped trades,
  no hidden costs, and a record to learn from.
* Short-dated ATM options lose value fast and the spread + fees are big for small trades. In the example in
  `02_RR_ENGINE_SPEC.md` a trade that looks like 1.8 : 1 on the chart is only **0.59 : 1** on the option after costs.
  That is exactly the mistake this tool exists to catch.
* Until you have collected real paper-trade results, every output is labelled **UNVALIDATED**.
