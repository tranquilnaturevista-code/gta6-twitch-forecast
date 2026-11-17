"""gta6-twitch-ccu-forecast-01 entry in Python 3.13 (standard library only).

Forecasts GTA VI's launch-day Twitch viewers (Grand Theft Auto VI category),
hours 2..24 of 2026-11-19 UTC, as five quantiles each, from the case's hour-1
print and the release-day curves of past big launches in the case file.

Model (calibrated offline on the kit's comparables, leave-one-out):
  - For every past launch in the case's `comparables`, take its release-day
    hourly curve (SullyGnome hourly averages) and express each hour as a
    log-ratio to that launch's hour-1 print.
  - Keep launches that were already live at hour 1 (hour-1 viewers >= 20,000,
    at least 12 release-day hours present). GTA VI's own pre-release category
    is excluded: it has no launch day yet.
  - GTA VI is console-only, single-player, so the curve is learned mostly from
    console-only launches (no "PC" in `platforms`): per hour, the median
    log-ratio of the console-only launches blended 0.7 / 0.3 with the median
    of all kept launches, falling back to all launches when fewer than 5
    console-only launches cover that hour.
  - Spread: the residual log-std of the console-only launches around their
    median, clipped to [0.30, 0.80]. Quantiles are median * exp(z_tau * sigma)
    with the standard normal quantiles at the scored levels.
  - Channels adjustment: launches whose hour 1 already had many live channels
    grow less into the evening (corr about -0.5). A shrunk (x0.5) OLS slope of
    log-ratio on log hour-1 channels, fitted on the console-only launches and
    clipped to [-0.5, 0], shifts the median by that slope times the case's
    log hour-1 channels minus the launches' median. Needs at least 5 launches
    with hour-1 channels, else no adjustment.
  - Fallbacks keep the output valid: no usable comparables -> the house
    baseline (flat median, spread 0.15 * sqrt(h - 1)); a missing hour-1 print
    -> the last history point, else 1.

Leave-one-out pinball loss (log scale) on the kit's comparables:
  console-only launches (the GTA VI-like set): this model 0.177, house
  baseline 0.283. All big launches: 0.271 vs 0.321.

Deterministic: no randomness, no wall-clock, no network; sorted iteration
everywhere. Runs in well under a second per case on 1 CPU.
"""

import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# The grader's -P leaves /bot off sys.path: put it back first, so a module
# shipped beside this file imports.
sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]

# A launch counts as "already live at hour 1" when its first hour is at
# least this many viewers; below it the hour-1 print is a sparse-sampling
# artefact (or a pre-unlock hour), not a level to ratio against.
MIN_HOUR1 = 20000
# A launch's release-day curve needs at least this many hours to be usable.
MIN_DAY_HOURS = 12
# Blend weight of the console-only median curve vs the all-launch curve.
W_CONSOLE = 0.7
# The channels adjustment is shrunk by this much (OLS slope, then x SHRINK).
SHRINK = 0.5
# Residual log-std is clipped to this range.
SIGMA_MIN = 0.30
SIGMA_MAX = 0.80
# House baseline spread, used as the fallback.
BASE_SIGMA = 0.15


def round2(x: float):
    """x rounded to 2 decimals, half up, as JavaScript's Math.round(x*100)/100."""
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value == int(value) else value


def parse_time(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def median(values):
    vals = sorted(values)
    n = len(vals)
    if n == 0:
        return None
    mid = n // 2
    return vals[mid] if n % 2 == 1 else (vals[mid - 1] + vals[mid]) / 2


def pstdev(values, mean):
    if len(values) < 2:
        return None
    return math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))


def ols_slope(xs, ys):
    """OLS slope of ys on xs, clipped to [-0.5, 0]; 0 when too few points."""
    if len(xs) < 5:
        return 0.0
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    return max(-0.5, min(0.0, b))


def day_curve(comp):
    """Release-day hourly viewers (hour 1..24) and channels of one comparable."""
    try:
        ds = parse_time(comp["day_start"])
    except Exception:
        return {}, {}
    viewers = {}
    channels = {}
    for p in comp.get("hourly") or []:
        try:
            h = int((parse_time(p["from"]) - ds).total_seconds() // 3600) + 1
        except Exception:
            continue
        if 1 <= h <= 24:
            v = p.get("viewers")
            if isinstance(v, (int, float)) and v > 0:
                viewers[h] = float(v)
            c = p.get("channels")
            if isinstance(c, (int, float)) and c > 0:
                channels[h] = float(c)
    return viewers, channels


def launch_pool(case):
    """Usable past launches from the case's comparables.

    Returns a list of dicts {viewers, channels, console_only}. The case's own
    game and the GTA VI pre-release category are left out.
    """
    game_name = ""
    try:
        game_name = (case.get("game") or {}).get("name") or ""
    except Exception:
        game_name = ""
    pool = []
    for comp in case.get("comparables") or []:
        name = comp.get("name") or ""
        if name == game_name or "Grand Theft Auto" in name:
            continue
        viewers, channels = day_curve(comp)
        if viewers.get(1, 0) < MIN_HOUR1:
            continue
        if len(viewers) < MIN_DAY_HOURS:
            continue
        platforms = comp.get("platforms") or ""
        pool.append({
            "viewers": viewers,
            "channels": channels,
            "console_only": "PC" not in platforms,
        })
    return pool


def hour_stats(pool, h):
    """Median log-ratio and residual log-std of hour h across a launch pool."""
    vals = [math.log(L["viewers"][h] / L["viewers"][1]) for L in pool if h in L["viewers"]]
    if not vals:
        return None, None
    m = median(vals)
    s = pstdev(vals, m)
    if s is None:
        s = 0.5
    return m, max(SIGMA_MIN, min(SIGMA_MAX, s))


def forecast_case(case, name):
    if case.get("levels") != LEVELS:
        raise ValueError(f"{name} asks for levels {case.get('levels')}, not {LEVELS}")
    first = next((k for k in case.get("known") or [] if k.get("hour") == 1), None)
    if first is not None and first.get("viewers"):
        p1 = max(1.0, float(first["viewers"]))
        ch1 = first.get("channels")
    else:
        hist = case.get("history") or []
        p1 = max(1.0, float(hist[-1]["viewers"])) if hist else 1.0
        ch1 = None
    ch1 = float(ch1) if isinstance(ch1, (int, float)) and ch1 > 0 else None

    pool = launch_pool(case)
    console = [L for L in pool if L["console_only"]]

    hours = []
    for row in case["forecast"]:
        h = row["hour"]
        mc, sc = hour_stats(console, h)
        ma, sa = hour_stats(pool, h)
        if mc is None and ma is None:
            # No usable past launch: the house baseline.
            q = [round2(p1 * math.exp(z * BASE_SIGMA * math.sqrt(h - 1))) for z in Z]
        else:
            if mc is None or len([L for L in console if h in L["viewers"]]) < 5:
                m, sig = ma, sa
            else:
                m = W_CONSOLE * mc + (1 - W_CONSOLE) * (ma if ma is not None else mc)
                sig = sc if sc is not None else sa
            if sig is None:
                sig = 0.5
            # Channels adjustment, fitted on the console-only launches.
            if ch1 is not None:
                pairs = [
                    (math.log(L["channels"][1]), math.log(L["viewers"][h] / L["viewers"][1]))
                    for L in console
                    if h in L["viewers"] and 1 in L["channels"]
                ]
                if len(pairs) >= 5:
                    beta = ols_slope([p[0] for p in pairs], [p[1] for p in pairs])
                    med_log_ch = median([p[0] for p in pairs])
                    m += SHRINK * beta * (math.log(ch1) - med_log_ch)
            q = [round2(p1 * math.exp(m + z * sig)) for z in Z]
        # Keep the row valid: positive, non-decreasing, within the cap.
        fixed = []
        prev = 0.0
        for v in q:
            v = max(1.0, min(1e9, float(v)))
            if v < prev:
                v = prev
            fixed.append(v)
            prev = v
        # round2 can return ints; keep numbers as-is (fractions are fine).
        hours.append({"hour": h, "q": fixed})
    hours.sort(key=lambda r: r["hour"])
    return {"hours": hours}


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
