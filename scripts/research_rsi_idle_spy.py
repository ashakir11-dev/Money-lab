"""Research 1 again with idle cash held in SPY (engine/0.4.0 ``idle_cash = "benchmark"``).

Same universe, dates and variants as research_rsi_spy20.py, so the only change is that cash
not in a position buys whole SPY shares. The question becomes: do the signal's stock picks
beat simply staying in SPY? Needs TIINGO_API_KEY.
Write-up: docs/research/rsi30-idle-spy.md. Run: uv run python scripts/research_rsi_idle_spy.py
"""

from __future__ import annotations

import json
from datetime import date

from research_rsi_spy20 import UNIVERSE, VARIANTS, config

from moneylab.service import Lab


def main() -> None:
    lab = Lab.open()
    snap = lab.import_provider(
        [*UNIVERSE, "SPY"], date(2017, 9, 1), date(2024, 12, 31),
        label="SPY top 20 (end-2017) + SPY, 2017-09 to 2024-12, Tiingo", source="tiingo", timeframe="1d",
    )  # fmt: skip
    out = {}
    for th, hold in VARIANTS:
        cfg = config(th, hold)
        cfg.portfolio.idle_cash = "benchmark"
        cfg.name += ", idle cash in SPY"
        rid = lab.run(cfg, snap.id)
        run = lab.store.get(rid)
        m, a = run["metrics"], run["metrics"]["analysis"]
        out[f"rsi<{th} hold {hold}"] = {
            "run": rid,
            "total_return": m["total_return"],
            "spy_total_return": m["benchmark"]["total_return"],
            "cagr": a["returns"]["cagr"],
            "spy_cagr": a["benchmark"]["cagr"],
            "sharpe": a["returns"]["sharpe"],
            "spy_sharpe": a["benchmark"]["sharpe"],
            "max_drawdown": m["max_drawdown"],
            "beta": a["market"]["beta"],
            "alpha": a["market"]["alpha"],
            "trades": a["trades"]["count"],
            "win_rate": a["trades"]["win_rate"],
            "profit_factor": a["trades"]["profit_factor"],
            "edge": a["edge"]["mean"],
            "edge_t": a["edge"]["t_stat"],
            "beat_rate": a["edge"]["beat_rate"],
            "costs": m["costs"],
            "by_year": {y["year"]: [y["ret"], y["bench_ret"]] for y in a["by_year"]},
        }
    print(f"snapshot {snap.id}")
    print(json.dumps(out, indent=1, default=float))


if __name__ == "__main__":
    main()
