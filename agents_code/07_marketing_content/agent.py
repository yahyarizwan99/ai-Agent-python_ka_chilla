"""
07 - Marketing Content Agent
Pattern: generate -> critique -> revise loop with a round cap and best-of-history selection.
Spec: ../../guide/agents/07_marketing_content.md
"""
from __future__ import annotations

import argparse
import json
import operator
import os
import re
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

load_dotenv()
MODEL = os.getenv("MODEL", "claude-sonnet-5")
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PASS_SCORE = 8

# ---- Structured outputs ----------------------------------------------------


class Copy(BaseModel):
    headline: str = Field(description="email subject / social hook / landing headline")
    preheader: str = Field(default="", description="email preheader or subhead; empty for social")
    body: str
    cta: str


class Claims(BaseModel):
    claims: list[str] = Field(description="Every factual product claim in the copy (features, numbers, comparisons, guarantees)")


class Critique(BaseModel):
    score: int = Field(ge=1, le=10)
    notes: list[str] = Field(max_length=6)
    hard_fails: list[str] = Field(default_factory=list)
    best_line: str = ""


class PolicyCheck(BaseModel):
    allowed: bool
    reason: str


# ---- State -----------------------------------------------------------------


class State(TypedDict, total=False):
    brief: dict
    brand_guide: dict
    max_rounds: int
    refused: str | None
    draft: dict
    claim_violations: list[str]
    format_fails: list[str]
    critique: dict | None
    history: Annotated[list[dict], operator.add]
    round: Annotated[int, operator.add]
    final_copy: dict | None
    passed: bool


# ---- Tools -----------------------------------------------------------------

CHANNEL_LIMITS = {"email": {"headline": 60, "preheader": 90, "body": 900}, "social": {"headline": 280, "preheader": 0, "body": 0}, "landing": {"headline": 80, "preheader": 140, "body": 1200}}


def channel_limits(channel: str) -> dict:
    return CHANNEL_LIMITS.get(channel, {"headline": 60, "preheader": 90, "body": 600})


# ---- Model -----------------------------------------------------------------

llm = ChatAnthropic(model=MODEL, max_tokens=2048)
writer = llm.with_structured_output(Copy)
claim_extractor = llm.with_structured_output(Claims)
editor = llm.with_structured_output(Critique)
policy = llm.with_structured_output(PolicyCheck)

WRITE_PROMPT = """You are the WRITER. Produce marketing copy for the "{channel}" channel.
Brand voice: {voice}. Tagline may be used once: "{tagline}".
Never use these words: {banned}.
Only state product facts listed in key_points; do not invent features, prices, statistics, testimonials, or comparisons.
Brief: {brief}
Length limits (characters): {limits}. For social, put everything in headline and leave preheader/body empty.
{revision}"""

CRITIQUE_PROMPT = """You are the EDITOR. Do not rewrite. Score the draft 1-10 against this checklist and list concrete,
actionable notes (max 6):
1. Hits the goal and CTA from the brief
2. Covers every key point
3. Matches brand voice; no hype
4. Speaks to the stated audience
5. Clear, specific, no filler
Hard fails (cap the score at 5 and list each in hard_fails): {hard_fails}
Draft: {draft}
Brief: {brief}
Brand guide: {guide}"""


# ---- Nodes -----------------------------------------------------------------


def policy_gate(state: State) -> dict:
    r = policy.invoke([HumanMessage(f"Is this marketing brief acceptable to write? Refuse if it targets minors with age-restricted products, makes health/financial/medical guarantees, or disparages named competitors. Brief: {json.dumps(state['brief'])}")])
    return {"refused": None if r.allowed else r.reason}


def write(state: State) -> dict:
    b, g = state["brief"], state["brand_guide"]
    revision = ""
    if state.get("critique"):
        revision = f"""This is revision round {state['round'] + 1}. The editor's notes on your previous draft:
{json.dumps(state['critique']['notes'])}
Hard fails you MUST fix: {json.dumps(state['critique']['hard_fails'])}
Previous draft: {json.dumps(state['draft'])}
Address every note. Keep what the editor did not criticise."""
    prompt = WRITE_PROMPT.format(channel=b["channel"], voice=g["voice"], tagline=g.get("tagline", ""), banned=g.get("banned_words", []), brief=json.dumps(b), limits=channel_limits(b["channel"]), revision=revision)
    draft = writer.invoke([HumanMessage(prompt)])
    return {"draft": draft.model_dump(), "round": 1}


def check_claims(state: State) -> dict:
    text = " ".join(state["draft"].values())
    claims = claim_extractor.invoke([HumanMessage(f"List every factual product claim in this copy as short phrases.\nCopy: {text}")]).claims
    allowed = [k.lower() for k in state["brief"]["key_points"]] + [state["brief"]["product"].lower()]
    violations = []
    for c in claims:
        cl = c.lower()
        grounded = any(_overlap(cl, a) for a in allowed)
        numeric = re.search(r"\d", cl) and not any(re.search(r"\d", a) and _overlap(cl, a) for a in allowed)
        if not grounded or numeric:
            violations.append(c)
    return {"claim_violations": violations}


def _overlap(claim: str, point: str) -> bool:
    cw = set(re.findall(r"[a-z0-9]+", claim)) - {"the", "a", "an", "and", "with", "for", "of", "to", "your"}
    pw = set(re.findall(r"[a-z0-9]+", point)) - {"the", "a", "an", "and", "with", "for", "of", "to", "your"}
    return bool(cw and pw) and len(cw & pw) / len(pw) >= 0.5


def check_format(state: State) -> dict:
    d, g = state["draft"], state["brand_guide"]
    limits = channel_limits(state["brief"]["channel"])
    fails = []
    for field, lim in limits.items():
        if lim and len(d.get(field, "")) > lim:
            fails.append(f"{field} is {len(d[field])} chars, limit {lim}")
    text = " ".join(d.values()).lower()
    for w in g.get("banned_words", []):
        if re.search(rf"\b{re.escape(w.lower())}\w*", text):
            fails.append(f"banned word: {w}")
    return {"format_fails": fails}


def critique(state: State) -> dict:
    hard = state.get("format_fails", []) + [f"unsupported claim: {c}" for c in state.get("claim_violations", [])]
    c = editor.invoke([HumanMessage(CRITIQUE_PROMPT.format(hard_fails=json.dumps(hard) or "none", draft=json.dumps(state["draft"]), brief=json.dumps(state["brief"]), guide=json.dumps(state["brand_guide"])))])
    crit = c.model_dump()
    crit["hard_fails"] = sorted(set(crit["hard_fails"]) | set(hard))
    if crit["hard_fails"]:
        crit["score"] = min(crit["score"], 5)
    return {"critique": crit, "history": [{"round": state["round"], "draft": state["draft"], "critique": crit}]}


def finalise(state: State) -> dict:
    if state.get("refused"):
        return {"final_copy": None, "passed": False}
    best = max(state["history"], key=lambda h: (not h["critique"]["hard_fails"], h["critique"]["score"]))
    return {"final_copy": best["draft"], "passed": best["critique"]["score"] >= PASS_SCORE and not best["critique"]["hard_fails"]}


# ---- Routers ---------------------------------------------------------------


def after_gate(state: State) -> Literal["write", "finalise"]:
    return "finalise" if state.get("refused") else "write"


def after_critique(state: State) -> Literal["write", "finalise"]:
    c = state["critique"]
    if c["score"] >= PASS_SCORE and not c["hard_fails"]:
        return "finalise"
    return "write" if state["round"] < state.get("max_rounds", 3) else "finalise"


# ---- Graph -----------------------------------------------------------------


def build_graph():
    b = StateGraph(State)
    for name, fn in [("policy_gate", policy_gate), ("write", write), ("check_claims", check_claims), ("check_format", check_format), ("critique", critique), ("finalise", finalise)]:
        b.add_node(name, fn)
    b.add_edge(START, "policy_gate")
    b.add_conditional_edges("policy_gate", after_gate)
    b.add_edge("write", "check_claims")
    b.add_edge("check_claims", "check_format")
    b.add_edge("check_format", "critique")
    b.add_conditional_edges("critique", after_critique)
    b.add_edge("finalise", END)
    return b.compile()


graph = build_graph()

# ---- CLI -------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Marketing Content Agent")
    p.add_argument("--brief", default=os.path.join(DATA_DIR, "brief.json"))
    p.add_argument("--guide", default=os.path.join(DATA_DIR, "brand_guide.json"))
    p.add_argument("--rounds", type=int, default=3)
    p.add_argument("--graph", action="store_true")
    a = p.parse_args()
    if a.graph:
        print(graph.get_graph().draw_mermaid())
        return
    with open(a.brief, encoding="utf-8") as f:
        brief = json.load(f)
    with open(a.guide, encoding="utf-8") as f:
        guide = json.load(f)
    out = graph.invoke({"brief": brief, "brand_guide": guide, "max_rounds": a.rounds, "history": [], "round": 0})
    if out.get("refused"):
        print("REFUSED:", out["refused"])
        return
    for h in out["history"]:
        c = h["critique"]
        print(f"round {h['round']}: score {c['score']}  hard_fails={c['hard_fails']}  notes={c['notes'][:3]}")
    print(f"\npassed={out['passed']}\n")
    fc = out["final_copy"]
    print(f"HEADLINE : {fc['headline']}")
    if fc.get("preheader"):
        print(f"PREHEADER: {fc['preheader']}")
    if fc.get("body"):
        print(f"\n{fc['body']}\n")
    print(f"CTA      : {fc['cta']}")


if __name__ == "__main__":
    main()
