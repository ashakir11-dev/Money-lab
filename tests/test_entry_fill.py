from fixtures_util import bars, day, fills, level_strategy, run
from moneylab.engine.config import EntryFill


def test_dip_fills_at_limit_when_the_low_prints_through() -> None:
    cfg = level_strategy(["AAA", "SPY"], above=10, hold=99)
    cfg.entry_fill = EntryFill(kind="dip", dip_pct=0.1)
    df = bars(
        {
            "AAA": [(20, 20), (20, 20, 17.5, 19), (19, 19)],
            "SPY": [(10, 10), (10, 10), (10, 10)],
        }
    )
    buys = [f for f in fills(run(df, cfg)) if f[2] == "buy"]
    assert buys, "dip should fill"
    assert buys[0][4] == 18.0


def test_dip_waits_until_the_low_reaches_the_limit() -> None:
    cfg = level_strategy(["AAA", "SPY"], above=10, hold=99)
    cfg.entry_fill = EntryFill(kind="dip", dip_pct=0.1)
    df = bars(
        {
            "AAA": [(20, 20), (20, 20, 19.5, 20), (20, 20, 17.0, 18)],
            "SPY": [(10, 10), (10, 10), (10, 10)],
        }
    )
    buys = [f for f in fills(run(df, cfg)) if f[2] == "buy"]
    assert len(buys) == 1
    assert buys[0][0] == day(2)
    assert buys[0][4] == 18.0


def test_next_range_skips_a_gap_outside_the_band() -> None:
    cfg = level_strategy(["AAA", "SPY"], above=10, hold=99)
    cfg.entry_fill = EntryFill(kind="next_range", below_pct=0.02, above_pct=0.02)
    df = bars(
        {
            "AAA": [(20, 20), (25, 26, 24, 5), (5, 5)],
            "SPY": [(10, 10), (10, 10), (10, 10)],
        }
    )
    buys = [f for f in fills(run(df, cfg)) if f[2] == "buy"]
    assert buys == []


def test_next_range_fills_at_open_when_the_open_is_in_band() -> None:
    cfg = level_strategy(["AAA", "SPY"], above=10, hold=99)
    cfg.entry_fill = EntryFill(kind="next_range", below_pct=0.05, above_pct=0.05)
    df = bars(
        {
            "AAA": [(20, 20), (20.4, 20.4), (20, 20)],
            "SPY": [(10, 10), (10, 10), (10, 10)],
        }
    )
    buys = [f for f in fills(run(df, cfg)) if f[2] == "buy"]
    assert buys
    assert buys[0][4] == 20.4
