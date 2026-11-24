"""Money-lab dashboard.

Every strategy control is generated from component declarations, so a newly
registered indicator appears here without dashboard edits. All work goes through
``moneylab.service.Lab``; this module only presents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from moneylab.components.registry import ComponentDeclaration, get_component, list_components
from moneylab.data.snapshot import list_snapshots
from moneylab.engine.config import StrategyConfig, config_from_resolved
from moneylab.service import Lab

PAGES = ("Runs", "Strategy builder", "Compare", "Data", "Acceptance gates")


@st.cache_resource
def _lab() -> Lab:
    return Lab.open()


def _set_draft(cfg: dict[str, Any], parent_id: str | None, snapshot_id: str | None) -> None:
    st.session_state["draft"] = cfg
    st.session_state["draft_parent"] = parent_id
    st.session_state["draft_snapshot"] = snapshot_id
    st.session_state["draft_rev"] = st.session_state.get("draft_rev", 0) + 1


def _default_draft(symbols: list[str]) -> dict[str, Any]:
    return {
        "name": "SMA20 cross",
        "universe": [s for s in symbols if s != "SPY"],
        "benchmark": "SPY" if "SPY" in symbols else symbols[0],
        "start": None,
        "end": None,
        "entry": [
            {
                "kind": "price_cross",
                "indicator": {"id": "sma", "params": {"length": 20}},
                "params": {"direction": "above"},
            },
        ],
        "exit": {"kind": "holding_period", "params": {"sessions": 10}, "conditions": []},
        "portfolio": {},
    }


def _param_widget(decl: ComponentDeclaration, name: str, value: Any, key: str) -> Any:
    p = decl.param(name)
    label = p.label or p.name
    if p.type == "choice":
        return st.selectbox(label, p.choices, index=p.choices.index(value), key=key)
    if p.type == "int":
        return st.number_input(
            label,
            value=int(value),
            step=1,
            min_value=int(p.min) if p.min is not None else None,
            max_value=int(p.max) if p.max is not None else None,
            key=key,
        )
    return st.number_input(
        label,
        value=float(value),
        step=1.0,
        min_value=float(p.min) if p.min is not None else None,
        max_value=float(p.max) if p.max is not None else None,
        key=key,
    )


def _condition_editor(cond: dict[str, Any], use: str, key: str) -> dict[str, Any]:
    kinds = [d.id for d in list_components(use) if d.id in ("price_cross", "threshold")]  # type: ignore[arg-type]
    indicators = [d.id for d in list_components("indicator")]
    cols = st.columns([2, 2, 3])
    with cols[0]:
        kind = st.selectbox(
            "Condition",
            kinds,
            index=kinds.index(cond["kind"]),
            key=f"{key}.kind",
            format_func=lambda k: get_component(k).label,
        )
    with cols[1]:
        ind_id = st.selectbox(
            "Indicator",
            indicators,
            index=indicators.index(cond["indicator"]["id"]),
            key=f"{key}.ind",
            format_func=lambda k: get_component(k).label,
        )
    ind_decl = get_component(ind_id)
    cond_decl = get_component(kind)
    ind_params = ind_decl.resolve_params(
        {
            k: v
            for k, v in cond["indicator"]["params"].items()
            if k in {p.name for p in ind_decl.params}
        }
        if ind_id == cond["indicator"]["id"]
        else {}
    )
    cond_params = cond_decl.resolve_params(
        {k: v for k, v in cond["params"].items() if k in {p.name for p in cond_decl.params}}
        if kind == cond["kind"]
        else {}
    )
    with cols[2]:
        for p in ind_decl.params:
            ind_params[p.name] = _param_widget(
                ind_decl, p.name, ind_params[p.name], f"{key}.ind.{p.name}"
            )
        for p in cond_decl.params:
            cond_params[p.name] = _param_widget(
                cond_decl, p.name, cond_params[p.name], f"{key}.{p.name}"
            )
    return {"kind": kind, "indicator": {"id": ind_id, "params": ind_params}, "params": cond_params}


def page_builder(lab: Lab) -> None:
    snaps = list_snapshots(lab.settings.snapshot_dir)
    if not snaps:
        st.info("Import data first on the Data page.")
        return
    if "draft" not in st.session_state:
        _set_draft(_default_draft(snaps[0]["symbols"]), None, snaps[0]["id"])
    draft: dict[str, Any] = st.session_state["draft"]
    rev = st.session_state["draft_rev"]
    parent = st.session_state.get("draft_parent")
    if parent:
        st.caption(f"Duplicate of run `{parent}`. The original run is not changed.")

    snap_ids = [s["id"] for s in snaps]
    sid = st.session_state.get("draft_snapshot") or snap_ids[0]
    sid = st.selectbox(
        "Data snapshot",
        snap_ids,
        index=snap_ids.index(sid) if sid in snap_ids else 0,
        key=f"b{rev}.snapshot",
        format_func=lambda i: f"{i} ({next(s for s in snaps if s['id'] == i)['source']})",
    )
    symbols = next(s for s in snaps if s["id"] == sid)["symbols"]
    draft["name"] = st.text_input("Name", draft["name"], key=f"b{rev}.name")
    draft["universe"] = st.multiselect(
        "Universe (watchlist)",
        symbols,
        [s for s in draft["universe"] if s in symbols],
        key=f"b{rev}.universe",
    )
    draft["benchmark"] = st.selectbox(
        "Benchmark",
        symbols,
        index=symbols.index(draft["benchmark"]) if draft["benchmark"] in symbols else 0,
        key=f"b{rev}.benchmark",
    )

    st.subheader("Entry: all conditions must pass")
    new_entry = []
    for i, c in enumerate(draft["entry"]):
        with st.container(border=True):
            new_entry.append(_condition_editor(c, "entry", f"b{rev}.entry{i}"))
            if st.button("Remove condition", key=f"b{rev}.entry{i}.remove"):
                draft["entry"] = [
                    x for j, x in enumerate(new_entry + draft["entry"][i + 1 :]) if j != i
                ]
                st.session_state["draft_rev"] += 1
                st.rerun()
    draft["entry"] = new_entry
    if st.button("Add entry condition", key=f"b{rev}.entry.add"):
        draft["entry"].append(
            {"kind": "threshold", "indicator": {"id": "rsi", "params": {}}, "params": {}}
        )
        st.session_state["draft_rev"] += 1
        st.rerun()

    st.subheader("Exit")
    ex = draft["exit"]
    ex_kind = st.selectbox(
        "Exit rule",
        ["holding_period", "signal"],
        index=["holding_period", "signal"].index(ex["kind"]),
        key=f"b{rev}.exit.kind",
        format_func=lambda k: (
            "Exit after N sessions" if k == "holding_period" else "Exit on a signal"
        ),
    )
    if ex_kind == "holding_period":
        decl = get_component("holding_period")
        sessions = ex["params"].get("sessions", decl.param("sessions").default)
        draft["exit"] = {
            "kind": "holding_period",
            "conditions": [],
            "params": {
                "sessions": _param_widget(decl, "sessions", sessions, f"b{rev}.exit.sessions")
            },
        }
    else:
        conds = ex["conditions"] or [
            {
                "kind": "price_cross",
                "indicator": {"id": "sma", "params": {"length": 20}},
                "params": {"direction": "below"},
            }
        ]
        draft["exit"] = {
            "kind": "signal",
            "params": {},
            "conditions": [
                _condition_editor(c, "exit", f"b{rev}.exit{i}") for i, c in enumerate(conds)
            ],
        }

    st.subheader("Portfolio and costs")
    s = lab.settings
    pf = draft["portfolio"]
    cols = st.columns(4)
    pf["starting_cash"] = cols[0].number_input(
        "Starting cash",
        value=float(pf.get("starting_cash") or s.starting_cash),
        min_value=1.0,
        key=f"b{rev}.cash",
    )
    pf["target_allocation"] = cols[1].number_input(
        "Target allocation",
        value=float(pf.get("target_allocation") or s.target_allocation),
        min_value=0.01,
        max_value=1.0,
        step=0.01,
        key=f"b{rev}.alloc",
    )
    pf["max_positions"] = cols[2].number_input(
        "Max positions",
        value=int(pf.get("max_positions") or s.max_positions),
        min_value=1,
        max_value=300,
        step=1,
        key=f"b{rev}.maxpos",
    )
    pf["cost_bps_per_side"] = cols[3].number_input(
        "Cost (bps per side)",
        value=float(
            pf.get("cost_bps_per_side")
            if pf.get("cost_bps_per_side") is not None
            else s.cost_bps_per_side
        ),
        min_value=0.0,
        max_value=500.0,
        key=f"b{rev}.cost",
    )

    if st.button("Validate, run and save", type="primary", key="run"):
        try:
            cfg = StrategyConfig.model_validate(draft)
            run_id = lab.run(cfg, sid, parent_id=parent)
        except Exception as e:  # validation errors are shown, never executed
            st.error(f"Not run: {e}")
        else:
            st.session_state["selected_run"] = run_id
            st.session_state["last_run"] = run_id
            st.success(f"Saved run {run_id}")
            if parent:
                st.session_state["compare_pair"] = (parent, run_id)


def page_runs(lab: Lab) -> None:
    runs = lab.store.list_runs()
    if not runs:
        st.info("No runs yet. Build one in the Strategy builder.")
        return
    st.dataframe(pd.DataFrame(runs), hide_index=True, use_container_width=True)
    ids = [r["id"] for r in runs]
    cur = st.session_state.get("selected_run")
    run_id = st.selectbox(
        "Open run",
        ids,
        index=ids.index(cur) if cur in ids else 0,
        key="runs.open",
        format_func=lambda i: f"{i} - {next(r for r in runs if r['id'] == i)['name']}",
    )
    run = lab.store.get(run_id)
    led = lab.store.ledgers(run_id)
    c1, c2 = st.columns(2)
    if c1.button("Duplicate this run", key="runs.duplicate"):
        cfg = config_from_resolved(run["resolved"]).model_dump(mode="json")
        cfg["name"] = run["resolved"]["name"] + " (copy)"
        _set_draft(cfg, run_id, run["snapshot_id"])
        st.session_state["nav_to"] = "Strategy builder"
        st.rerun()
    if run["parent_id"] and c2.button("Compare with parent", key="runs.compare_parent"):
        st.session_state["compare_pair"] = (run["parent_id"], run_id)
        st.session_state["nav_to"] = "Compare"
        st.rerun()
    _metrics(run["metrics"])
    _equity_chart({run_id: led.equity}, benchmark=led.equity)
    t1, t2, t3, t4 = st.tabs(
        ["Resolved settings", "Fills", "Decisions", "Daily cash and positions"]
    )
    t1.json(run["resolved"])
    t2.dataframe(led.fills, hide_index=True, use_container_width=True)
    t3.dataframe(_decisions_view(led.decisions), hide_index=True, use_container_width=True)
    t4.dataframe(led.equity, hide_index=True, use_container_width=True)


def page_compare(lab: Lab) -> None:
    runs = lab.store.list_runs()
    if len(runs) < 2:
        st.info("Run at least two variants to compare.")
        return
    ids = [r["id"] for r in runs]
    pa, pb = st.session_state.get("compare_pair", (ids[1], ids[0]))
    c1, c2 = st.columns(2)
    a = c1.selectbox("Run A", ids, index=ids.index(pa) if pa in ids else 1, key="cmp.a")
    b = c2.selectbox("Run B", ids, index=ids.index(pb) if pb in ids else 0, key="cmp.b")
    cmp = lab.compare(a, b)
    st.subheader("Config differences")
    diff = pd.DataFrame(cmp["config_diff"])
    if diff.empty:
        st.write("No strategy settings differ.")
    else:
        st.dataframe(diff.astype(str), hide_index=True, use_container_width=True)
    st.subheader("First differing decision")
    fd = cmp["first_difference"]
    st.session_state["first_difference"] = fd
    if fd is None:
        st.success("No decision changed: both runs made identical trading decisions.")
    else:
        st.markdown(
            f"**{fd['symbol']} on {fd['session']}**: "
            f"A `{fd['a']['action']}` vs B `{fd['b']['action']}`"
        )
        rows = []
        for side in ("a", "b"):
            r = fd[side]
            for c in r.get("conditions") or []:
                rows.append(
                    {
                        "run": side.upper(),
                        "condition": c["condition"],
                        "observed": c.get("observed"),
                        "threshold": c.get("threshold"),
                        "passed": c.get("passed"),
                    }
                )
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        for side in ("a", "b"):
            r = fd[side]
            fill = r.get("fill")
            txt = f"Run {side.upper()}: **{r['action']}**, {r['reason']}"
            if fill:
                txt += (
                    f". Filled {fill['qty']} @ {fill['price']:.2f} on {fill['session']}, "
                    f"cost {fill['cost']:.2f}, cash after {fill['cash_after']:,.2f}"
                )
            st.markdown(txt)
        imp = fd["impact"]
        st.markdown(
            f"Portfolio impact: cash on {imp['next_session']} "
            f"A {imp['cash_a']:,.2f} vs B {imp['cash_b']:,.2f}; "
            f"final equity delta (B - A) {imp['final_equity_delta']:,.2f}"
        )
        with st.expander("Full decision records"):
            st.json(fd)
    la, lb = lab.store.ledgers(a), lab.store.ledgers(b)
    st.subheader("Performance")
    keys = ["total_return", "max_drawdown", "sharpe", "trades", "avg_exposure", "costs"]
    ma, mb = cmp["a"]["metrics"], cmp["b"]["metrics"]
    table = pd.DataFrame(
        {
            "Run A": [ma.get(k) for k in keys],
            "Run B": [mb.get(k) for k in keys],
            "Benchmark": [ma.get("benchmark", {}).get(k) for k in keys],
        },
        index=keys,
    )
    st.dataframe(table.astype(object).where(table.notna(), ""), use_container_width=True)
    st.caption(f"Sharpe: {ma.get('sharpe_assumptions', '')}")
    _equity_chart({f"A {a}": la.equity, f"B {b}": lb.equity}, benchmark=la.equity)
    f1, f2 = st.columns(2)
    f1.caption("Run A fills")
    f1.dataframe(la.fills, hide_index=True, use_container_width=True)
    f2.caption("Run B fills")
    f2.dataframe(lb.fills, hide_index=True, use_container_width=True)


def page_data(lab: Lab) -> None:
    path = st.text_input(
        "Import file (CSV: date,symbol,open,high,low,close,volume)",
        str(lab.settings.import_dir / "rsi_divergence.csv"),
        key="data.path",
    )
    if st.button("Import as new snapshot", key="data.import"):
        try:
            snap = lab.import_file(Path(path))
            st.success(
                f"Snapshot {snap.id}: {len(snap.meta['symbols'])} symbols, {snap.meta['rows']} rows"
            )
        except Exception as e:
            st.error(str(e))
    st.dataframe(
        pd.DataFrame(list_snapshots(lab.settings.snapshot_dir)),
        hide_index=True,
        use_container_width=True,
    )


def page_gates(lab: Lab) -> None:
    from moneylab.acceptance import GATES

    st.caption("Run `uv run moneylab acceptance` to re-check. Status today:")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "gate": n,
                    "title": t,
                    "status": "PENDING",
                    "evidence so far": ", ".join(ts) or "none",
                    "depends on": m,
                }
                for n, t, ts, m in GATES
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )


def _metrics(m: dict[str, Any]) -> None:
    cols = st.columns(6)
    cols[0].metric("Return", f"{m.get('total_return', 0):.2%}")
    cols[1].metric("Max drawdown", f"{m.get('max_drawdown', 0):.2%}")
    cols[2].metric("Sharpe", f"{m.get('sharpe', 0):.2f}", help=m.get("sharpe_assumptions"))
    cols[3].metric("Trades", m.get("trades", 0))
    cols[4].metric("Avg exposure", f"{m.get('avg_exposure', 0):.1%}")
    cols[5].metric("Costs", f"{m.get('costs', 0):,.2f}")


def _equity_chart(series: dict[str, pd.DataFrame], benchmark: pd.DataFrame | None = None) -> None:
    fig = go.Figure()
    for name, eq in series.items():
        fig.add_trace(go.Scatter(x=eq["session"], y=eq["equity"], name=name, mode="lines"))
    if benchmark is not None and "benchmark_equity" in benchmark:
        fig.add_trace(
            go.Scatter(
                x=benchmark["session"],
                y=benchmark["benchmark_equity"],
                name="Benchmark",
                mode="lines",
                line={"dash": "dot"},
            )
        )
    fig.update_layout(height=320, margin={"l": 10, "r": 10, "t": 10, "b": 10})
    st.plotly_chart(fig, use_container_width=True)


def _decisions_view(dec: pd.DataFrame) -> pd.DataFrame:
    out = dec.copy()
    for col in ("conditions", "values"):
        if col in out:
            out[col] = out[col].map(lambda v: "" if v is None else str(v))
    return out


def main() -> None:
    st.set_page_config(page_title="Money-lab", layout="wide")
    lab = _lab()
    st.sidebar.title("Money-lab")
    if "nav_to" in st.session_state:
        st.session_state["page"] = st.session_state.pop("nav_to")
    elif "page" not in st.session_state:
        st.session_state["page"] = "Runs"
    page = st.sidebar.radio("Page", PAGES, key="page")
    {
        "Runs": page_runs,
        "Strategy builder": page_builder,
        "Compare": page_compare,
        "Data": page_data,
        "Acceptance gates": page_gates,
    }[page](lab)


main()
