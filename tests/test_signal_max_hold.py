from fixtures_util import bars, decisions, fills, level_strategy, run
from moneylab.engine.config import Condition, ExitRule, IndicatorRef


def test_signal_exit_respects_optional_max_sessions() -> None:
    """Stay in when the exit signal never fires; max_sessions still gets you out."""
    cfg = level_strategy(["AAA", "SPY"], above=10, hold=99)
    cfg.exit = ExitRule(
        kind="signal",
        params={"max_sessions": 3},
        conditions=[
            Condition(
                kind="threshold",
                indicator=IndicatorRef(id="sma", params={"length": 1}),
                params={"op": "below", "value": 1},
            )
        ],
    )
    df = bars(
        {
            "AAA": [(10, 20)] * 8,
            "SPY": [(10, 10)] * 8,
        }
    )
    r = run(df, cfg)
    sells = [f for f in fills(r) if f[2] == "sell"]
    assert sells, "max hold should force an exit"
    reasons = [d[3] for d in decisions(r, "exit")]
    assert any("held" in x for x in reasons)
