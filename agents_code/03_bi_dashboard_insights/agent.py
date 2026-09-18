"""
03 - BI Dashboard Insights Agent
Pattern: multi-step reasoning (rank -> hypothesise -> test -> synthesise) + summariser node.
Spec: ../../guide/agents/03_bi_dashboard_insights.md
"""
from __future__ import annotations

import argparse
import json
import operator
import os
import statistics
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

load_dotenv()
MODEL = os.getenv("MODEL", "claude-sonnet-5")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TOP_N = 5
EXPLAINED_TARGET = 70.0
MAX_ITER = 2

# ---- Structured output ----------------------------------------------------


class Hypothesis(BaseModel):
    metric: str
    dimension: str
    rationale: str


class HypothesisList(BaseModel):
    hypotheses: list[Hypothesis] = Field(description="Up to 3 per metric movement")


# ---- State -----------------------------------------------------------------


class State(TypedDict, total=False):
    metrics: list[dict]
    dimensions: list[str]
    period_label: str
    audience: str
    ranked_movements: list[dict]
    hypotheses: Annotated[list[dict], operator.add]
    driver_evidence: Annotated[list[dict], operator.add]
    insights: list[dict]
    narrative: str
    iteration: Annotated[int, operator.add]


# ---- Tools (local JSON fixture; swap for your warehouse) ------------------


def _load_breakdowns() -> dict:
    with open(os.path.join(DATA_DIR, "breakdowns.json"), encoding="utf-8") as f:
        return json.load(f)


def dimension_breakdown(metric: str, dimension: str) -> list[dict]:
    """[{value, current, prior}] for one metric split by one dimension."""
    data = _load_breakdowns()
    try:
        return data[metric][dimension]
    except KeyError:
        raise LookupError(f"No breakdown for {metric} by {dimension}") from None


def contribution(breakdown: list[dict]) -> list[dict]:
    """Share of the total delta attributable to each dimension value."""
    total_delta = sum(b["current"] - b["prior"] for b in breakdown)
    if total_delta == 0:
        return [{"value": b["value"], "contribution_pct": 0.0} for b in breakdown]
    return [
        {"value": b["value"], "contribution_pct": round(100 * (b["current"] - b["prior"]) / total_delta, 1)}
        for b in breakdown
    ]


# ---- Model -----------------------------------------------------------------

llm = ChatAnthropic(model=MODEL, max_tokens=2048)
hypothesiser = llm.with_structured_output(HypothesisList)

HYPOTHESISE_PROMPT = """You are a BI analyst forming hypotheses about WHY metrics moved.
Period: {period_label}
Top movements (metric, delta, pct, z): {movements}
Dimensions available for breakdown: {dimensions}

For each movement, propose up to 3 dimensions most likely to explain it.
{prior}"""

SUMMARISE_PROMPT = """Write an executive summary for {audience} covering {period_label}.
Max 180 words. Lead with the single biggest movement. One sentence per insight: metric, direction, size,
main driver, confidence. Flag low-confidence items with "likely". End with one recommended follow-up question.
Plain English; no bullet points; no metrics beyond those listed. If the insights list is empty, say there was
no material movement this period in two sentences.
Insights: {insights}"""


# ---- Nodes -----------------------------------------------------------------


def rank_movements(state: State) -> dict:
    rows = []
    for m in state["metrics"]:
        delta = m["current"] - m["prior"]
        pct = 100 * delta / m["prior"] if m["prior"] else 0.0
        rows.append({"metric": m["name"], "unit": m.get("unit", ""), "current": m["current"], "prior": m["prior"], "delta": round(delta, 2), "pct": round(pct, 1)})
    pcts = [abs(r["pct"]) for r in rows]
    mean = statistics.mean(pcts) if pcts else 0
    sd = statistics.pstdev(pcts) if len(pcts) > 1 else 1
    for r in rows:
        r["z"] = round((abs(r["pct"]) - mean) / sd, 2) if sd else 0.0
    significant = [r for r in rows if abs(r["pct"]) >= 1.0]
    significant.sort(key=lambda r: abs(r["pct"]), reverse=True)
    return {"ranked_movements": significant[:TOP_N]}


def hypothesise(state: State) -> dict:
    if not state["ranked_movements"]:
        return {"hypotheses": []}
    tested = {(e["metric"], e["dimension"]) for e in state.get("driver_evidence", [])}
    explained = _explained_by_metric(state)
    targets = [m for m in state["ranked_movements"] if explained.get(m["metric"], 0) < EXPLAINED_TARGET]
    prior = ""
    if tested:
        prior = f"Dimensions already tested (metric, dimension): {sorted(tested)}. Do NOT repeat a tested pair. Focus only on these metrics: {[t['metric'] for t in targets]}."
    prompt = HYPOTHESISE_PROMPT.format(period_label=state["period_label"], movements=targets, dimensions=state["dimensions"], prior=prior)
    out = hypothesiser.invoke([HumanMessage(prompt)])
    valid_metrics = {m["metric"] for m in targets}
    new = [h.model_dump() for h in out.hypotheses if h.dimension in state["dimensions"] and h.metric in valid_metrics and (h.metric, h.dimension) not in tested]
    return {"hypotheses": new}


def test_hypotheses(state: State) -> dict:
    tested = {(e["metric"], e["dimension"]) for e in state.get("driver_evidence", [])}
    evidence = []
    for h in state.get("hypotheses", []):
        key = (h["metric"], h["dimension"])
        if key in tested:
            continue
        tested.add(key)
        try:
            bd = dimension_breakdown(h["metric"], h["dimension"])
            for c in contribution(bd):
                evidence.append({"metric": h["metric"], "dimension": h["dimension"], "value": c["value"], "contribution_pct": c["contribution_pct"], "testable": True})
        except LookupError as e:
            evidence.append({"metric": h["metric"], "dimension": h["dimension"], "value": None, "contribution_pct": 0.0, "testable": False, "note": str(e)})
    return {"driver_evidence": evidence, "iteration": 1}


def _explained_by_metric(state: State) -> dict[str, float]:
    out: dict[str, float] = {}
    for m in state.get("ranked_movements", []):
        drivers = [e for e in state.get("driver_evidence", []) if e["metric"] == m["metric"] and e.get("testable") and e["contribution_pct"] >= 10]
        # one dimension can explain at most 100 %; take the best dimension rather than summing across dimensions
        by_dim: dict[str, float] = {}
        for d in drivers:
            by_dim[d["dimension"]] = by_dim.get(d["dimension"], 0) + d["contribution_pct"]
        out[m["metric"]] = min(100.0, max(by_dim.values())) if by_dim else 0.0
    return out


def synthesise(state: State) -> dict:
    insights = []
    explained = _explained_by_metric(state)
    for m in state["ranked_movements"]:
        drivers = [e for e in state.get("driver_evidence", []) if e["metric"] == m["metric"] and e.get("testable") and e["contribution_pct"] >= 10]
        drivers.sort(key=lambda e: e["contribution_pct"], reverse=True)
        untestable = any(e["metric"] == m["metric"] and not e.get("testable") for e in state.get("driver_evidence", []))
        exp = explained.get(m["metric"], 0.0)
        conf = "high" if exp >= 80 else "medium" if exp >= 60 else "low"
        if untestable and conf == "high":
            conf = "medium"
        insights.append({
            "metric": m["metric"], "delta": m["delta"], "pct": m["pct"], "unit": m["unit"],
            "drivers": [{"dimension": d["dimension"], "value": d["value"], "contribution_pct": d["contribution_pct"]} for d in drivers[:3]],
            "explained_pct": round(exp, 1), "residual_pct": round(100 - exp, 1), "confidence": conf,
        })
    return {"insights": insights}


def summarise(state: State) -> dict:
    prompt = SUMMARISE_PROMPT.format(audience=state.get("audience", "the leadership team"), period_label=state["period_label"], insights=json.dumps(state["insights"]))
    reply = llm.invoke([HumanMessage(prompt)])
    return {"narrative": reply.content if isinstance(reply.content, str) else str(reply.content)}


# ---- Routers ---------------------------------------------------------------


def after_synthesise(state: State) -> Literal["hypothesise", "summarise"]:
    unexplained = [i for i in state["insights"] if i["explained_pct"] < EXPLAINED_TARGET]
    if unexplained and state.get("iteration", 0) < MAX_ITER:
        return "hypothesise"
    return "summarise"


def after_rank(state: State) -> Literal["hypothesise", "synthesise"]:
    return "hypothesise" if state["ranked_movements"] else "synthesise"


# ---- Graph -----------------------------------------------------------------


def build_graph():
    b = StateGraph(State)
    for name, fn in [("rank_movements", rank_movements), ("hypothesise", hypothesise), ("test_hypotheses", test_hypotheses), ("synthesise", synthesise), ("summarise", summarise)]:
        b.add_node(name, fn)
    b.add_edge(START, "rank_movements")
    b.add_conditional_edges("rank_movements", after_rank)
    b.add_edge("hypothesise", "test_hypotheses")
    b.add_edge("test_hypotheses", "synthesise")
    b.add_conditional_edges("synthesise", after_synthesise)
    b.add_edge("summarise", END)
    return b.compile()


graph = build_graph()

# ---- CLI -------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="BI Dashboard Insights Agent")
    p.add_argument("--metrics", default=os.path.join(DATA_DIR, "metrics.json"))
    p.add_argument("--audience", default="VP Sales")
    p.add_argument("--graph", action="store_true")
    a = p.parse_args()
    if a.graph:
        print(graph.get_graph().draw_mermaid())
        return
    with open(a.metrics, encoding="utf-8") as f:
        payload = json.load(f)
    out = graph.invoke({**payload, "audience": a.audience, "hypotheses": [], "driver_evidence": [], "iteration": 0})
    print("=== Insights ===")
    for i in out["insights"]:
        drivers = ", ".join(f"{d['dimension']}={d['value']} ({d['contribution_pct']}%)" for d in i["drivers"]) or "none found"
        print(f"- {i['metric']}: {i['pct']:+.1f}% ({i['delta']:+} {i['unit']}) | explained {i['explained_pct']}% | {i['confidence']} | {drivers}")
    print(f"\n=== Narrative ({out.get('iteration')} hypothesis round(s)) ===\n{out['narrative']}")


if __name__ == "__main__":
    main()
