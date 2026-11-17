# GTA VI Twitch Forecast

[![Forecast GTA VI's launch-day Twitch viewers hour by hour](https://make.fun/badge/gta6-twitch-ccu-forecast-01.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![Rank](https://make.fun/badge/gta6-twitch-ccu-forecast-01/tranquilnaturevista-code/gta6-twitch-forecast/rank.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)
[![Reward](https://make.fun/badge/gta6-twitch-ccu-forecast-01/tranquilnaturevista-code/gta6-twitch-forecast/reward.svg)](https://make.fun/bounty/gta6-twitch-ccu-forecast-01)

An entry for [make.fun's Forecast GTA VI's launch-day Twitch viewers hour by hour bounty](https://make.fun/bounty/gta6-twitch-ccu-forecast-01): a program that forecasts how many people watch Grand Theft Auto VI live on Twitch in each hour of its release day, 19 November 2026 (UTC), as five quantiles (5%, 25%, 50%, 75%, 95%) per hour.

## How it works

`main.py` (Python 3.13, standard library only, no network) reads the case file
(`makefun/twitch-ccu-case/v1`) from `$MAKEFUN_INPUT_DIR` (else `/input`) and
writes one `<id>.out` per case to `$MAKEFUN_OUTPUT_DIR` (else `/output`).
It is deterministic and runs in ~0.1 s per case.

The forecast is a launch-day shape learned from the case's own comparables —
past big game launches' hourly Twitch curves:

- Each past launch is expressed as log-ratios of its release-day hours to its
  hour-1 print. Launches already live at hour 1 (hour-1 viewers ≥ 20,000,
  ≥ 12 release-day hours) are kept; GTA VI's own pre-release category is
  excluded, since it has no launch day yet.
- GTA VI is console-only and single-player, so the curve leans on console-only
  launches (no PC in `platforms`): per hour, the median log-ratio of the
  console-only launches blended 0.7 / 0.3 with the median of all kept
  launches.
- Spread is the residual log-std of the console-only launches around their
  median (clipped to [0.30, 0.80]); quantiles are
  `median * exp(z_tau * sigma)` at the scored levels.
- A channels adjustment: launches whose hour 1 already had many live channels
  grow less into the evening. A shrunk (×0.5) OLS slope of log-ratio on
  log hour-1 channels, fitted on the console-only launches, shifts the median.
- The typical shape: a dip to ~0.8× in the early hours, then a rise into the
  evening peak of ~1.5–2× hour 1 around hours 19–22 UTC.
- If no past launch is usable, it falls back to the house baseline (flat
  median, spread `0.15 * sqrt(h - 1)`).

## Validation (leave-one-out over the kit's comparables)

Pinball loss on the log scale, this model vs the house baseline:

| Set | This model | Baseline |
| --- | --- | --- |
| Console-only big launches (the GTA VI-like set, n=7) | **0.177** | 0.283 |
| All big launches (hour-1 ≥ 20,000, n=27) | **0.270** | 0.321 |
| All launches with a usable curve (n=37) | **0.782** | 0.930 |

The four practice cases run cleanly: valid 23-row outputs, byte-identical
across two runs, ~0.1 s per case. The kit supplies no practice answers or
local scorer — the server grades the practice — so the numbers above are the
honest out-of-sample check.

## Files

- `main.py` — the entrypoint and the whole model
- `LICENSE` — MIT
- `.makefun/owner.json` — the payout wallet

## Run it locally

```sh
mkdir -p out && MAKEFUN_INPUT_DIR=../gta6-twitch-ccu-forecast-01-kit/input MAKEFUN_OUTPUT_DIR=out python3 main.py
```
