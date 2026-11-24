"""Money-lab dashboard.

Every strategy control is generated from component declarations, so a newly
registered indicator appears here without dashboard edits. All work goes through
``moneylab.service.Lab``; this module only presents.
"""

from __future__ import annotations

import html
import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from moneylab.components.registry import ComponentDeclaration, get_component, list_components
from moneylab.dashboard import ui
from moneylab.dashboard.paper_page import page_paper
from moneylab.data.snapshot import list_snapshots
from moneylab.engine.config import StrategyConfig, config_from_resolved
from moneylab.service import Lab

PAGES = ("Runs", "Strategy builder", "Compare", "Paper trading", "Data", "Acceptance gates")


@st.cache_resource
def _cached_lab(home: str) -> Lab:
    return Lab.open(Path(home)) if home else Lab.open()


def _lab() -> Lab:
    # Keyed by MONEYLAB_HOME so a process that switches homes never reuses another's Lab.
    return _cached_lab(os.environ.get("MONEYLAB_HOME", ""))


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
    cols = st.columns([2, 2, 3])
    with cols[0]:
        kind = st.selectbox(
            "Condition",
            kinds,
            index=kinds.index(cond["kind"]),
            key=f"{key}.kind",
            format_func=lambda k: get_component(k).label,
        )
    # "close crosses X" only makes sense for indicators in price units (the SMA).
    indicators = [
        d.id for d in list_components("indicator") if kind != "price_cross" or d.price_scale
    ]
    current = cond["indicator"]["id"]
    with cols[1]:
        ind_id = st.selectbox(
            "Indicator",
            indicators,
            index=indicators.index(current) if current in indicators else 0,
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
        else {},
        cross_check=False,  # inconsistent values are reported by "Validate, run and save"
    )
    cond_params = cond_decl.resolve_params(
        {k: v for k, v in cond["params"].items() if k in {p.name for p in cond_decl.params}}
        if kind == cond["kind"]
        else {},
        cross_check=False,
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


def _shape(conds: list[dict[str, Any]]) -> list[tuple[str, str]]:
    return [(c["kind"], c["indicator"]["id"]) for c in conds]


def _reset_changed(new: list[dict[str, Any]], old: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Clear parameters of conditions whose kind or indicator was just switched, so the
    redrawn controls start from the new component's declared defaults."""
    out = []
    for i, c in enumerate(new):
        prev = old[i] if i < len(old) else None
        c = {**c, "indicator": dict(c["indicator"])}
        if prev is None or prev["indicator"]["id"] != c["indicator"]["id"]:
            c["indicator"]["params"] = {}
        if prev is None or prev["kind"] != c["kind"]:
            c["params"] = {}
        out.append(c)
    return out


def _snapshots(lab: Lab) -> list[dict[str, Any]]:
    return list_snapshots(lab.settings.snapshot_dir)


def _universe_default(draft: dict[str, Any], symbols: list[str], benchmark: str) -> list[str]:
    """Keep the draft's universe where the snapshot has it; on a snapshot that shares
    none of it (e.g. switching from the demo to real data) start from every symbol
    except the benchmark."""
    kept = [s for s in draft["universe"] if s in symbols]
    return kept or [s for s in symbols if s != benchmark]


def page_builder(lab: Lab) -> None:
    ui.page_header(
        "Strategy builder",
        "Pick data and symbols, set entry and exit rules, then run. Every run is saved "
        "with its exact settings and data.",
    )
    snaps = _snapshots(lab)
    if not snaps:
        st.info("No market data yet. Load demo data or fetch real prices on the **Data** page.")
        return
    if "draft" not in st.session_state:
        _set_draft(_default_draft(snaps[0]["symbols"]), None, snaps[0]["id"])
    draft: dict[str, Any] = st.session_state["draft"]
    rev = st.session_state["draft_rev"]
    parent = st.session_state.get("draft_parent")
    if parent:
        pname = _run_name(lab, parent)
        st.info(
            f"Editing a copy of **{pname}** (`{parent}`). The original run is not changed.",
            icon=":material/content_copy:",
        )

    snap_ids = [s["id"] for s in snaps]
    by_id = {s["id"]: s for s in snaps}
    with st.container(border=True):
        ui.eyebrow("Step 1 · Data and symbols")
        sid = st.session_state.get("draft_snapshot") or snap_ids[0]
        sid = st.selectbox(
            "Data snapshot",
            snap_ids,
            index=snap_ids.index(sid) if sid in snap_ids else 0,
            key=f"b{rev}.snapshot",
            format_func=lambda i: ui.snapshot_option(by_id[i]),
        )
        meta = by_id[sid]
        ds = ui.data_source(meta.get("source"))
        ui.html_block(f'{ui.badge(ds.badge, ds.kind)} <span class="ml-meta">{ds.detail}</span>')
        symbols = meta["symbols"]
        c1, c2 = st.columns([3, 1])
        draft["name"] = c1.text_input("Name", draft["name"], key=f"b{rev}.name")
        bench_default = (
            draft["benchmark"]
            if draft["benchmark"] in symbols
            else ("SPY" if "SPY" in symbols else symbols[0])
        )
        draft["benchmark"] = c2.selectbox(
            "Benchmark",
            symbols,
            index=symbols.index(bench_default),
            key=f"b{rev}.benchmark",
        )
        draft["universe"] = st.multiselect(
            "Universe (watchlist)",
            symbols,
            _universe_default(draft, symbols, draft["benchmark"]),
            key=f"b{rev}.universe",
        )

    with st.container(border=True):
        ui.eyebrow("Step 2 · Entry: buy when all of these are true")
        new_entry = []
        for i, c in enumerate(draft["entry"]):
            with st.container(border=True):
                new_entry.append(_condition_editor(c, "entry", f"b{rev}.entry{i}"))
                if st.button(
                    "Remove condition",
                    key=f"b{rev}.entry{i}.remove",
                    icon=":material/close:",
                    type="tertiary",
                ):
                    draft["entry"] = [
                        x for j, x in enumerate(new_entry + draft["entry"][i + 1 :]) if j != i
                    ]
                    st.session_state["draft_rev"] += 1
                    st.rerun()
        if _shape(new_entry) != _shape(draft["entry"]):
            # A different condition or indicator was picked: redraw its parameter controls
            # from that component's declared defaults instead of the previous widget values.
            draft["entry"] = _reset_changed(new_entry, draft["entry"])
            st.session_state["draft_rev"] += 1
            st.rerun()
        draft["entry"] = new_entry
        if st.button("Add entry condition", key=f"b{rev}.entry.add", icon=":material/add:"):
            draft["entry"].append(
                {"kind": "threshold", "indicator": {"id": "rsi", "params": {}}, "params": {}}
            )
            st.session_state["draft_rev"] += 1
            st.rerun()

    with st.container(border=True):
        ui.eyebrow("Step 3 · Exit")
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
            new_exit = [
                _condition_editor(c, "exit", f"b{rev}.exit{i}") for i, c in enumerate(conds)
            ]
            draft["exit"] = {"kind": "signal", "params": {}, "conditions": new_exit}
            if ex["conditions"] and _shape(new_exit) != _shape(ex["conditions"]):
                draft["exit"]["conditions"] = _reset_changed(new_exit, ex["conditions"])
                st.session_state["draft_rev"] += 1
                st.rerun()

    with st.container(border=True):
        ui.eyebrow("Step 4 · Portfolio and costs")
        s = lab.settings
        pf = draft["portfolio"]
        cols = st.columns(4)
        pf["starting_cash"] = cols[0].number_input(
            "Starting cash ($)",
            value=float(pf.get("starting_cash") or s.starting_cash),
            min_value=1.0,
            step=1000.0,
            key=f"b{rev}.cash",
        )
        pf["target_allocation"] = cols[1].number_input(
            "Target allocation (fraction of equity)",
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

    ui.html_block(
        f'<p class="ml-summary"><b>Summary.</b> {html.escape(ui.strategy_summary(draft))}</p>'
    )
    if st.button(
        "Validate, run and save",
        type="primary",
        key="run",
        icon=":material/play_arrow:",
        width="stretch",
    ):
        try:
            cfg = StrategyConfig.model_validate(draft)
            run_id = lab.run(cfg, sid, parent_id=parent)
        except Exception as e:  # validation errors are shown, never executed
            st.error(f"Not run: {e}")
        else:
            st.session_state["selected_run"] = run_id
            st.session_state["last_run"] = run_id
            st.success(f"Saved run `{run_id}`. Open it on the **Runs** page.")
            if parent:
                st.session_state["compare_pair"] = (parent, run_id)
    # After running a duplicate, one click opens the comparison with its parent, and
    # the Compare page leads with the first differing decision.
    pair = st.session_state.get("compare_pair")
    last = st.session_state.get("last_run")
    if (
        parent
        and pair
        and last
        and tuple(pair) == (parent, last)
        and st.button(
            "Compare with the original run",
            key="run.compare",
            type="primary",
            icon=":material/compare_arrows:",
            width="stretch",
        )
    ):
        st.session_state["nav_to"] = "Compare"
        st.rerun()


def _run_name(lab: Lab, run_id: str) -> str:
    try:
        return str(lab.store.get(run_id)["resolved"]["name"])
    except KeyError:
        return run_id


def _runs_table(lab: Lab, runs: list[dict[str, Any]]) -> pd.DataFrame:
    names = {r["id"]: r["name"] for r in runs}
    rows = []
    for r in runs[:RUNS_TABLE_LIMIT]:
        full = lab.store.get(r["id"])
        m = full["metrics"]
        bench = (m.get("benchmark") or {}).get("total_return")
        rows.append(
            {
                "Run": r["name"],
                "Data": ui.data_source(full["resolved"].get("data", {}).get("source")).badge,
                "Return %": _pct100(m.get("total_return")),
                "vs benchmark (pp)": _pct100(m.get("total_return"), bench),
                "Max drawdown %": _pct100(m.get("max_drawdown")),
                "Sharpe": m.get("sharpe"),
                "Trades": m.get("trades"),
                "Copy of": names.get(r["parent_id"], r["parent_id"] or ""),
                "Created": ui.fmt_time(r["created_at"]),
                "Status": r["status"],
                "ID": r["id"],
            }
        )
    return pd.DataFrame(rows)


def _pct100(v: Any, minus: Any = None) -> float | None:
    if not isinstance(v, int | float):
        return None
    if minus is not None:
        if not isinstance(minus, int | float):
            return None
        v = v - minus
    return round(float(v) * 100, 2)


RUNS_TABLE_LIMIT = 200


def page_runs(lab: Lab) -> None:
    ui.page_header(
        "Runs",
        "Every backtest you have saved. Open one to see its results, duplicate it to try "
        "a change, or compare it with the run it was copied from.",
    )
    runs = lab.store.list_runs()
    if not runs:
        st.info(
            "No runs yet. Load data on the **Data** page, then build a strategy in the "
            "**Strategy builder**."
        )
        return
    ids = [r["id"] for r in runs]
    names = {r["id"]: r["name"] for r in runs}
    cur = st.session_state.get("selected_run")
    run_id = st.selectbox(
        "Open run",
        ids,
        index=ids.index(cur) if cur in ids else 0,
        key="runs.open",
        format_func=lambda i: f"{names[i]}  ·  {i}",
    )
    st.session_state["selected_run"] = run_id
    run = lab.store.get(run_id)
    led = lab.store.ledgers(run_id)
    res = run["resolved"]
    data = res.get("data", {})

    with st.container(border=True):
        head, actions = st.columns([3, 1.3], vertical_alignment="top")
        with head:
            st.markdown(f"### {res['name']}")
            meta = (
                f"{ui.source_badge(data.get('source'))} "
                f'<span class="ml-meta">{data.get("first_session", "?")} to '
                f"{data.get('last_session', '?')} · benchmark {html.escape(res['benchmark'])}"
                f" · run <code>{run_id}</code></span>"
            )
            if run["parent_id"]:
                meta += (
                    f'<br><span class="ml-meta">Copy of '
                    f"<b>{html.escape(names.get(run['parent_id'], run['parent_id']))}</b></span>"
                )
            ui.html_block(meta)
            ui.html_block(f'<p class="ml-summary">{html.escape(ui.strategy_summary(res))}</p>')
        with actions:
            if st.button(
                "Duplicate this run",
                key="runs.duplicate",
                icon=":material/content_copy:",
                type="primary",
                width="stretch",
                help="Opens the Strategy builder with a copy. The original is not changed.",
            ):
                cfg = config_from_resolved(res).model_dump(mode="json")
                cfg["name"] = res["name"] + " (copy)"
                _set_draft(cfg, run_id, run["snapshot_id"])
                st.session_state["nav_to"] = "Strategy builder"
                st.rerun()
            if run["parent_id"] and st.button(
                "Compare with parent",
                key="runs.compare_parent",
                icon=":material/compare_arrows:",
                width="stretch",
            ):
                st.session_state["compare_pair"] = (run["parent_id"], run_id)
                st.session_state["nav_to"] = "Compare"
                st.rerun()

    _metrics(run["metrics"], res["benchmark"])
    _run_context(res, run["metrics"])
    ui.eyebrow("Equity vs benchmark")
    ui.equity_chart(
        {res["name"]: led.equity}, benchmark=led.equity, benchmark_name=res["benchmark"]
    )

    t1, t2, t3, t4 = st.tabs(["Trades", "Decisions", "Daily cash and positions", "Settings"])
    with t1:
        _fills_table(led.fills, "No trades in this run.")
    with t2:
        _decisions_panel(led.decisions, key=f"runs.{run_id}")
    with t3:
        st.dataframe(_equity_view(led.equity), hide_index=True, width="stretch")
    with t4:
        st.caption("Exactly what ran, including defaults and engine rules.")
        st.json(res, expanded=1)

    with st.expander(f"All runs ({len(runs)})", expanded=False):
        st.dataframe(
            _runs_table(lab, runs),
            hide_index=True,
            width="stretch",
            column_config={
                "Return %": st.column_config.NumberColumn(format="%.2f%%"),
                "vs benchmark (pp)": st.column_config.NumberColumn(format="%+.2f"),
                "Max drawdown %": st.column_config.NumberColumn(format="%.2f%%"),
                "Sharpe": st.column_config.NumberColumn(format="%.2f"),
            },
        )


def _fills_table(fills: pd.DataFrame, empty: str) -> None:
    if fills.empty:
        st.caption(empty)
        return
    cols = [c for c in ("session", "symbol", "side", "qty", "price", "cost", "cash_after", "note")]
    st.dataframe(
        fills[[c for c in cols if c in fills]],
        hide_index=True,
        width="stretch",
        column_config={
            "session": "Date",
            "symbol": "Symbol",
            "side": "Side",
            "qty": "Shares",
            "price": st.column_config.NumberColumn("Price", format="$%.2f"),
            "cost": st.column_config.NumberColumn("Cost", format="$%.2f"),
            "cash_after": st.column_config.NumberColumn("Cash after", format="$%,.2f"),
            "note": "Note",
        },
    )


def _decisions_panel(dec: pd.DataFrame, key: str) -> None:
    if dec.empty:
        st.caption("No decisions recorded.")
        return
    show_all = st.toggle(
        "Show every decision (including no signal and warm-up)", key=f"{key}.alldec"
    )
    view = dec if show_all else dec[~dec["action"].isin(QUIET_ACTIONS)]
    st.caption(f"{len(view):,} of {len(dec):,} decisions")
    out = pd.DataFrame(
        {
            "Date": view["session"],
            "Symbol": view["symbol"],
            "Action": view["action"],
            "Reason": view["reason"],
            "Checks": view["conditions"].map(_checks_text) if "conditions" in view else "",
        }
    )
    st.dataframe(out, hide_index=True, width="stretch")


QUIET_ACTIONS = ("no_signal", "ineligible")


def _checks_text(conds: Any) -> str:
    if not isinstance(conds, list):
        return ""
    marks = {True: "✓", False: "✗", None: "·"}
    parts = []
    for c in conds:
        obs = c.get("observed")
        val = f" ({obs:.2f})" if isinstance(obs, int | float) else ""
        parts.append(f"{marks.get(c.get('passed'), '·')} {c.get('condition')}{val}")
    return "   ".join(parts)


def _equity_view(eq: pd.DataFrame) -> pd.DataFrame:
    out = eq.copy()
    if "positions" in out:
        out["positions"] = out["positions"].map(lambda v: "" if v is None else str(v))
    return out


def page_compare(lab: Lab) -> None:
    ui.page_header(
        "Compare",
        "Two runs side by side: the first trade where they differ and why, the settings "
        "that differ, and performance.",
    )
    runs = lab.store.list_runs()
    if len(runs) < 2:
        st.info("Run at least two variants to compare. Duplicate a run on the **Runs** page.")
        return
    ids = [r["id"] for r in runs]
    names = {r["id"]: r["name"] for r in runs}
    # Default: the newest run against its parent (the experimental control); without a
    # parent, against the run before it.
    parents = {r["id"]: r["parent_id"] for r in runs}
    newest_parent = parents.get(ids[0])
    default = (newest_parent, ids[0]) if newest_parent in names else (ids[1], ids[0])
    pa, pb = st.session_state.get("compare_pair", default)
    c1, c2 = st.columns(2)
    a = c1.selectbox(
        "Run A",
        ids,
        index=ids.index(pa) if pa in ids else 1,
        key="cmp.a",
        format_func=lambda i: f"{names[i]}  ·  {i}",
    )
    b = c2.selectbox(
        "Run B",
        ids,
        index=ids.index(pb) if pb in ids else 0,
        key="cmp.b",
        format_func=lambda i: f"{names[i]}  ·  {i}",
    )
    cmp = lab.compare(a, b)
    src_a = cmp["a"]["resolved"].get("data", {}).get("source")
    src_b = cmp["b"]["resolved"].get("data", {}).get("source")
    c1.markdown(ui.source_badge(src_a), unsafe_allow_html=True)
    c2.markdown(ui.source_badge(src_b), unsafe_allow_html=True)
    if cmp["a"]["snapshot_id"] != cmp["b"]["snapshot_id"]:
        st.warning("These runs used different data snapshots, so prices may differ too.")

    fd = cmp["first_difference"]
    st.session_state["first_difference"] = fd
    with st.container(border=True):
        ui.eyebrow("First differing decision")
        if fd is None:
            st.success("No decision changed: both runs made identical trading decisions.")
        else:
            st.markdown(
                f"#### {fd['symbol']} on {fd['session']}: "
                f"A {_action_label(fd['a']['action'])}, B {_action_label(fd['b']['action'])}"
            )
            ca, cb = st.columns(2)
            for col, side in ((ca, "a"), (cb, "b")):
                r = fd[side]
                fill = r.get("fill")
                txt = f"**Run {side.upper()}: {r['action']}**, {r['reason']}"
                if fill:
                    txt += (
                        f". Filled {fill['qty']} @ {fill['price']:.2f} on {fill['session']}, "
                        f"cost {fill['cost']:.2f}, cash after {fill['cash_after']:,.2f}"
                    )
                with col:
                    st.markdown(txt)
            rows = []
            for side in ("a", "b"):
                for c in fd[side].get("conditions") or []:
                    rows.append({"run": side.upper(), **c})
            if rows:
                ui.html_block(ui.conditions_table(rows))
            imp = fd["impact"]
            st.markdown(
                f"**Portfolio impact.** Cash on {imp['next_session']}: "
                f"A {imp['cash_a']:,.2f} vs B {imp['cash_b']:,.2f}. "
                f"Final equity difference (B - A): {imp['final_equity_delta']:+,.2f}"
            )
            with st.expander("Full decision records"):
                st.json(fd)

    ui.eyebrow("Settings that differ")
    diff = pd.DataFrame(cmp["config_diff"])
    if diff.empty:
        st.write("No strategy settings differ.")
    else:
        st.dataframe(
            diff.astype(str),
            hide_index=True,
            width="stretch",
            column_config={"setting": "Setting", "a": "Run A", "b": "Run B"},
        )

    la, lb = lab.store.ledgers(a), lab.store.ledgers(b)
    ui.eyebrow("Performance")
    ma, mb = cmp["a"]["metrics"], cmp["b"]["metrics"]
    bench = ma.get("benchmark", {})
    rows = []
    for key, label, kind in PERF_ROWS:
        va, vb, vx = ma.get(key), mb.get(key), bench.get(key)
        delta = vb - va if isinstance(va, int | float) and isinstance(vb, int | float) else None
        rows.append(
            {
                "Metric": label,
                "Run A": _fmt(va, kind),
                "Run B": _fmt(vb, kind),
                "B \u2212 A": _fmt(delta, kind, signed=True),
                "Benchmark": _fmt(vx, kind),
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    st.caption(f"Sharpe: {ma.get('sharpe_assumptions', '')}")
    ui.equity_chart(
        {f"A · {names[a]}": la.equity, f"B · {names[b]}": lb.equity},
        benchmark=la.equity,
        benchmark_name=cmp["a"]["resolved"]["benchmark"],
    )
    f1, f2 = st.columns(2)
    with f1:
        ui.eyebrow("Run A trades")
        _fills_table(la.fills, "No trades.")
    with f2:
        ui.eyebrow("Run B trades")
        _fills_table(lb.fills, "No trades.")


PERF_ROWS = [
    ("total_return", "Total return", "pct"),
    ("max_drawdown", "Max drawdown", "pct"),
    ("sharpe", "Sharpe", "sharpe"),
    ("trades", "Round trips", "int"),
    ("open_positions", "Open at end", "int"),
    ("avg_exposure", "Avg exposure", "pct"),
    ("costs", "Costs ($)", "num"),
]


def _fmt(v: Any, kind: str, signed: bool = False) -> str:
    if kind == "sharpe" and v is None and not signed:
        return "undefined"
    if not isinstance(v, int | float) or pd.isna(v):
        return "\u2013"
    sign = "+" if signed else ""
    if kind == "pct":
        return f"{v:{sign}.2%}"
    if kind == "int":
        return f"{v:{sign},.0f}"
    return f"{v:{sign},.2f}"


def _action_label(action: str) -> str:
    return {
        "enter": "bought",
        "exit": "sold",
        "no_signal": "no signal",
        "none": "no decision",
        "ineligible": "not eligible",
    }.get(action, action.replace("_", " "))


def page_data(lab: Lab) -> None:
    ui.page_header(
        "Data",
        "Market data snapshots that runs use. Fetch real prices from Tiingo, import your "
        "own CSV, or load the demo. A snapshot never changes once saved.",
    )
    has_key = lab.settings.tiingo_api_key is not None
    t_real, t_file = st.tabs(["Real prices (Tiingo)", "Import a CSV file"])
    with t_real:
        if has_key:
            st.caption(
                "Daily end-of-day prices, split and dividend adjusted. Include your benchmark "
                "(e.g. SPY). One request per symbol; the free Tiingo plan allows about 50 "
                "symbols an hour."
            )
        else:
            st.warning(
                "No Tiingo API key found, so real data can't be fetched. Add "
                "`TIINGO_API_KEY=...` to a `.env` file in the Money-lab folder (or export it "
                "in your shell) and restart the dashboard.",
                icon=":material/key_off:",
            )
        c1, c2, c3 = st.columns([3, 1, 1])
        syms = c1.text_input(
            "Symbols (comma or space separated)",
            "AAPL, MSFT, NVDA, AMZN, SPY",
            key="data.tiingo.symbols",
        )
        today = date.today()
        start = c2.date_input("From", today - timedelta(days=3 * 365), key="data.tiingo.start")
        end = c3.date_input("To", today, key="data.tiingo.end")
        label = st.text_input("Label (optional)", "", key="data.tiingo.label")
        if st.button(
            "Fetch from Tiingo",
            key="data.tiingo.fetch",
            type="primary",
            disabled=not has_key,
            icon=":material/cloud_download:",
        ):
            symbols = [s for s in syms.replace(",", " ").split() if s]
            try:
                with st.spinner(f"Fetching {len(symbols)} symbols from Tiingo..."):
                    snap = lab.import_provider(symbols, start, end, label)
            except Exception as e:  # provider errors never contain the key
                st.error(f"Not fetched: {e}")
            else:
                st.session_state["data.new_snapshot"] = snap.id
        _new_snapshot_cta(lab)
    with t_file:
        path = st.text_input(
            "File path (CSV: date,symbol,open,high,low,close,volume)",
            str(lab.settings.import_dir / "rsi_divergence.csv"),
            key="data.path",
            help="The default path is the demo fixture: synthetic prices for AAA to DDD and SPY.",
        )
        if st.button("Import as new snapshot", key="data.import", icon=":material/upload_file:"):
            try:
                snap = lab.import_file(Path(path))
            except Exception as e:
                st.error(str(e))
            else:
                st.success(
                    f"Snapshot {snap.id}: {len(snap.meta['symbols'])} symbols, "
                    f"{snap.meta['rows']} rows"
                )

    ui.eyebrow("Saved snapshots")
    snaps = _snapshots(lab)
    if not snaps:
        st.caption("None yet.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Data": ui.data_source(s.get("source")).badge,
                    "Symbols": ", ".join(s["symbols"]),
                    "From": s.get("first_date"),
                    "To": s.get("last_date"),
                    "Rows": s.get("rows"),
                    "Label": s.get("label", ""),
                    "Saved": ui.fmt_time(s.get("created_at")),
                    "ID": s["id"],
                }
                for s in snaps
            ]
        ),
        hide_index=True,
        width="stretch",
    )


def _new_snapshot_cta(lab: Lab) -> None:
    sid = st.session_state.get("data.new_snapshot")
    if not sid:
        return
    try:
        snap = lab.snapshot(sid)
    except Exception:
        return
    m = snap.meta
    st.success(
        f"Saved real-data snapshot `{sid}`: {len(m['symbols'])} symbols, {m['rows']:,} rows, "
        f"{m['first_date']} to {m['last_date']}."
    )
    if st.button(
        "Build a strategy on this data",
        key="data.tiingo.build",
        icon=":material/arrow_forward:",
    ):
        _set_draft(_default_draft(m["symbols"]), None, sid)
        st.session_state["nav_to"] = "Strategy builder"
        st.rerun()


def page_gates(lab: Lab) -> None:
    from moneylab.acceptance import GATES

    passed = sum(1 for *_, m in GATES if not m)
    ui.page_header(
        "Acceptance gates",
        f"{passed} of {len(GATES)} MVP gates pass. Re-check from a terminal: "
        "uv run moneylab acceptance",
    )
    st.progress(passed / len(GATES))
    cols = st.columns(2)
    for i, (n, title, tests, missing) in enumerate(GATES):
        with cols[i % 2], st.container(border=True):
            pill = ui.badge("Pass", "pass") if not missing else ui.badge("Pending", "pending")
            ui.html_block(
                f'<div class="ml-eyebrow">Gate {n}</div>'
                f"<div style='display:flex;justify-content:space-between;gap:.5rem;"
                f"align-items:center'><b>{html.escape(title)}</b>{pill}</div>"
            )
            if missing:
                st.markdown(f"**Waiting on:** {missing}")
            st.caption("Evidence: " + (", ".join(f"`{t}`" for t in tests) or "none"))


def _metrics(m: dict[str, Any], benchmark: str = "benchmark") -> None:
    bench = m.get("benchmark") or {}
    cols = st.columns(6)
    br = bench.get("total_return")
    cols[0].metric(
        "Return",
        ui.fmt_pct(m.get("total_return")),
        delta=(
            f"{(m.get('total_return', 0) - br) * 100:+.1f} pp vs {benchmark}"
            if isinstance(br, int | float)
            else None
        ),
    )
    cols[1].metric("Max drawdown", ui.fmt_pct(m.get("max_drawdown")))
    cols[2].metric("Sharpe", _sharpe(m.get("sharpe")), help=m.get("sharpe_assumptions"))
    cols[3].metric(
        "Round trips",
        m.get("trades", 0),
        help=f"Completed buy-then-sell trades. Still open at the end: "
        f"{m.get('open_positions', 0)}.",
    )
    cols[4].metric("Avg exposure", ui.fmt_pct(m.get("avg_exposure"), 1))
    cols[5].metric("Costs", ui.fmt_money(m.get("costs")))


def _sharpe(v: Any) -> str:
    """Sharpe is undefined when daily returns never vary; say so instead of showing 0."""
    return "undefined" if v is None else ui.fmt_num(v)


def _run_context(res: dict[str, Any], m: dict[str, Any]) -> None:
    """Open positions, how the benchmark was bought, and when the watchlist was chosen."""
    parts = [f"Open positions at the end: {m.get('open_positions', 0)}"]
    b = m.get("benchmark") or {}
    if b.get("invested_session"):
        parts.append(
            f"{res['benchmark']} bought {b['shares']:,} shares at "
            f"{ui.fmt_money(b['price'])} on {b['invested_session']} "
            f"(cost {ui.fmt_money(b['cost'])}, cash left {ui.fmt_money(b['cash_left'])})"
        )
    wl = res.get("watchlist")
    if wl:
        parts.append(
            f"Watchlist {wl['name']} v{wl['version']} selected {str(wl['created_at'])[:10]}: "
            "a list chosen today describes past performance of today's picks"
        )
    st.caption(" · ".join(parts))


PAGE_ICONS = {
    "Runs": ":material/list_alt:",
    "Strategy builder": ":material/tune:",
    "Compare": ":material/compare_arrows:",
    "Paper trading": ":material/receipt_long:",
    "Data": ":material/database:",
    "Acceptance gates": ":material/verified:",
}


def _sidebar(lab: Lab) -> str:
    with st.sidebar:
        ui.html_block(
            '<div class="ml-brand">Money-lab</div>'
            '<div class="ml-brand-sub">Trading research lab</div>'
        )
        page = st.radio(
            "Page",
            PAGES,
            key="page",
            label_visibility="collapsed",
            format_func=lambda p: f"{PAGE_ICONS[p]} {p}",
        )
        st.divider()
        ui.eyebrow("Data")
        snaps = _snapshots(lab)
        kinds = [ui.data_source(s.get("source")).kind for s in snaps]
        n_real, n_demo = kinds.count("real"), kinds.count("demo")
        n_file = len(kinds) - n_real - n_demo
        parts = [f"{n_real} real", f"{n_demo} demo"] + ([f"{n_file} file"] if n_file else [])
        st.caption(f"Snapshots: {', '.join(parts)}")
        if lab.settings.tiingo_api_key is not None:
            ui.html_block(ui.badge("Tiingo key found", "real"))
        else:
            ui.html_block(ui.badge("No Tiingo key: demo and files only", "neutral"))
    return str(page)


def main() -> None:
    st.set_page_config(page_title="Money-lab", page_icon=":material/monitoring:", layout="wide")
    ui.inject_css()
    lab = _lab()
    if "nav_to" in st.session_state:
        st.session_state["page"] = st.session_state.pop("nav_to")
    elif "page" not in st.session_state:
        st.session_state["page"] = "Runs"
    page = _sidebar(lab)
    {
        "Runs": page_runs,
        "Strategy builder": page_builder,
        "Compare": page_compare,
        "Paper trading": page_paper,
        "Data": page_data,
        "Acceptance gates": page_gates,
    }[page](lab)


main()
