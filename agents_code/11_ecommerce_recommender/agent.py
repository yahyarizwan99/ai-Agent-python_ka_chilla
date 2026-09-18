"""
11 - E-commerce Product Recommender Agent
Pattern: two memories - a per-session thread checkpointer and a cross-session LangGraph Store keyed by shopper.
The Store is persisted to memory_store.json between runs so preferences survive restarts.
Spec: ../../guide/agents/11_ecommerce_recommender.md
"""
from __future__ import annotations

import argparse
import json
import os
import re
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.store.memory import InMemoryStore
from pydantic import BaseModel, Field

load_dotenv()
MODEL = os.getenv("MODEL", "claude-sonnet-5")
HERE = os.path.dirname(__file__)
CATALOGUE = os.path.join(HERE, "data", "catalogue.json")
STORE_FILE = os.getenv("STORE_FILE", os.path.join(HERE, "memory_store.json"))

Intent = Literal["recommend", "feedback", "memory_view", "memory_forget", "chat"]

# ---- Structured outputs ----------------------------------------------------


class Classification(BaseModel):
    intent: Intent
    stated_preferences: list[str] = Field(default_factory=list)


class ProductQuery(BaseModel):
    category: str
    attributes: list[str] = Field(default_factory=list)
    max_price: float | None = None
    size: str | None = None
    exclude_brands: list[str] = Field(default_factory=list)
    prefer_brands: list[str] = Field(default_factory=list)
    exclude_skus: list[str] = Field(default_factory=list)


class Rec(BaseModel):
    sku: str
    reason: str


class RecList(BaseModel):
    recommendations: list[Rec] = Field(max_length=5)
    reply: str


class MemoryUpdate(BaseModel):
    sizes: dict[str, str] | None = None
    budget_hint: float | None = None
    liked_brands: list[str] | None = None
    disliked_brands: list[str] | None = None
    style_notes: list[str] | None = None
    feedback: list[dict] | None = Field(default=None, description="[{sku, signal: liked|disliked}]")
    human_readable: list[str] = Field(default_factory=list)


class ForgetRequest(BaseModel):
    field: Literal["sizes", "budget_hint", "liked_brands", "disliked_brands", "style_notes", "feedback", "everything"]
    value: str | None = None


# ---- State -----------------------------------------------------------------


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    shopper_id: str
    profile: dict
    purchases: list[dict]
    feedback: list[dict]
    intent: Intent
    query: dict
    relaxed: bool
    candidates: list[dict]
    recommendations: list[dict]
    memory_updates: list[str]  # per-turn output, overwritten each invoke


# ---- Long-term memory (LangGraph Store, persisted to JSON) ----------------

store = InMemoryStore()
SENSITIVE = re.compile(r"\b(\d[ -]?){13,16}\b|pregnan|religio|diagnos|medical|health", re.I)


def ns(shopper_id: str) -> tuple[str, str]:
    return ("shopper", shopper_id)


def load_store() -> None:
    if not os.path.exists(STORE_FILE):
        return
    with open(STORE_FILE, encoding="utf-8") as f:
        for sid, items in json.load(f).items():
            for key, value in items.items():
                store.put(ns(sid), key, value)


def save_store(shopper_ids: set[str]) -> None:
    data = {}
    if os.path.exists(STORE_FILE):
        with open(STORE_FILE, encoding="utf-8") as f:
            data = json.load(f)
    for sid in shopper_ids:
        data[sid] = {item.key: item.value for item in store.search(ns(sid))}
    with open(STORE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---- Tools -----------------------------------------------------------------


def _catalogue() -> list[dict]:
    with open(CATALOGUE, encoding="utf-8") as f:
        return json.load(f)


def search_catalogue(q: dict, k: int = 20) -> list[dict]:
    words = set(re.findall(r"[a-z]+", (q["category"] + " " + " ".join(q.get("attributes", []))).lower()))
    scored = []
    for p in _catalogue():
        text = f"{p['name']} {p['category']} {' '.join(p['tags'])}".lower()
        score = len(words & set(re.findall(r"[a-z]+", text)))
        if p["category"].lower() in words or score:
            scored.append((score + (3 if p["category"].lower() in words else 0), p))
    scored.sort(key=lambda s: -s[0])
    return [p for _, p in scored[:k]]


def availability(skus: list[str]) -> dict:
    stock = {p["sku"]: {"in_stock": p["stock"] > 0, "price": p["price"]} for p in _catalogue()}
    return {s: stock[s] for s in skus if s in stock}


# ---- Model -----------------------------------------------------------------

llm = ChatAnthropic(model=MODEL, max_tokens=1500)
classifier = llm.with_structured_output(Classification)
query_builder = llm.with_structured_output(ProductQuery)
ranker = llm.with_structured_output(RecList)
memoriser = llm.with_structured_output(MemoryUpdate)
forgetter = llm.with_structured_output(ForgetRequest)


def transcript(msgs: list[AnyMessage], n: int = 4) -> str:
    return "\n".join(("shopper: " if isinstance(m, HumanMessage) else "assistant: ") + str(m.content) for m in msgs[-n:])


# ---- Nodes -----------------------------------------------------------------


def load_memory(state: State) -> dict:
    items = {i.key: i.value for i in store.search(ns(state["shopper_id"]))}
    return {"profile": items.get("profile", {}), "purchases": items.get("purchases", []), "feedback": items.get("feedback", [])}


def classify(state: State) -> dict:
    c = classifier.invoke([HumanMessage(f"Classify the shopper's latest message: recommend | feedback | memory_view | memory_forget | chat. 'feedback' = comments on a product/brand they saw. 'memory_view' = asks what you know about them. 'memory_forget' = asks you to forget something. 'chat' = anything else that is not a product request.\nMessages:\n{transcript(state['messages'])}")])
    return {"intent": c.intent}


def build_query(state: State) -> dict:
    relaxed = state.get("relaxed", False)
    recent_skus = [p["sku"] for p in state.get("purchases", [])]
    q = query_builder.invoke([HumanMessage(f"""Build a product search query. Merge the shopper's current request with their stored profile.
Hard constraints (never violate): sizes, disliked brands (exclude_brands), max budget if stated now or in profile.
Soft preferences: liked brands, style notes. Exclude recently purchased SKUs unless asked again: {recent_skus}.
{'Too few in-stock results last time: relax soft attributes only, never hard constraints.' if relaxed else ''}
Request: {state['messages'][-1].content}
Profile: {json.dumps(state.get('profile', {}))}
Feedback: {json.dumps(state.get('feedback', []))}""")])
    d = q.model_dump()
    # enforce hard constraints from memory in code, regardless of what the model produced
    prof = state.get("profile", {})
    d["exclude_brands"] = sorted(set(d["exclude_brands"]) | set(prof.get("disliked_brands", [])))
    if d["max_price"] is None and prof.get("budget_hint"):
        d["max_price"] = prof["budget_hint"]
    return {"query": d}


def search(state: State) -> dict:
    try:
        return {"candidates": search_catalogue(state["query"])}
    except Exception as e:  # noqa: BLE001
        return {"candidates": [], "messages": [AIMessage(f"Search is unavailable right now ({e}). Please try again in a moment.")]}


def check_stock(state: State) -> dict:
    q = state["query"]
    try:
        avail = availability([p["sku"] for p in state["candidates"]])
    except Exception:  # noqa: BLE001 - never risk recommending out-of-stock items
        return {"candidates": [], "recommendations": [], "messages": [AIMessage("I can't confirm stock right now, so I won't recommend anything I can't promise. Please try again shortly.")]}
    kept = []
    for p in state["candidates"]:
        a = avail.get(p["sku"])
        if not a or not a["in_stock"]:
            continue
        if q.get("max_price") and a["price"] > q["max_price"]:
            continue
        if p["brand"].lower() in {b.lower() for b in q.get("exclude_brands", [])}:
            continue
        if q.get("size") and p.get("sizes") and q["size"] not in p["sizes"]:
            continue
        if p["sku"] in q.get("exclude_skus", []):
            continue
        kept.append({**p, "price": a["price"]})
    return {"candidates": kept}


def rank_and_explain(state: State) -> dict:
    if not state["candidates"]:
        return {"recommendations": [], "messages": [AIMessage("I couldn't find anything in stock that fits all your constraints. Want me to relax the budget or try another category?")]}
    r = ranker.invoke([HumanMessage(f"""Pick 3-5 products for the shopper from the candidates ONLY. For each give a one-sentence reason referencing a real product attribute and, where relevant, a stored preference ("you said you prefer wide fit"). Order by fit to the request, then stored preferences. Then write a friendly 2-sentence reply introducing the picks and inviting feedback. No urgency language.
Request: {state['messages'][-1].content}
Candidates: {json.dumps(state['candidates'])}
Profile: {json.dumps(state.get('profile', {}))}
Feedback: {json.dumps(state.get('feedback', []))}""")])
    by_sku = {c["sku"]: c for c in state["candidates"]}
    recs = [{"sku": x.sku, "name": by_sku[x.sku]["name"], "price": by_sku[x.sku]["price"], "reason": x.reason, "in_stock": True} for x in r.recommendations if x.sku in by_sku]
    lines = "\n".join(f"  - {x['name']} (${x['price']:.2f}) - {x['reason']}" for x in recs)
    return {"recommendations": recs, "messages": [AIMessage(f"{r.reply}\n{lines}")]}


def update_memory(state: State) -> dict:
    turn = transcript(state["messages"], 2)
    if SENSITIVE.search(turn):
        turn = SENSITIVE.sub("[redacted]", turn)
    upd = memoriser.invoke([HumanMessage(f"""From this conversation turn, extract DURABLE preferences worth remembering across future visits: sizes (e.g. {{"shoes": "US 9"}}), budget hint, liked/disliked brands, style notes, product feedback (sku + liked/disliked). Ignore one-off context ("for my trip"). Never store payment details, addresses, or health/religious/family information. Return only fields to add or change, plus human_readable: one short line per change. If nothing durable, return nothing.
Turn:
{turn}
Query used: {json.dumps(state.get('query', {}))}
Current profile: {json.dumps(state.get('profile', {}))}""")])
    d = upd.model_dump()
    prof = dict(state.get("profile", {}))
    changed = []
    if d["sizes"]:
        prof["sizes"] = {**prof.get("sizes", {}), **d["sizes"]}
    if d["budget_hint"] is not None:
        prof["budget_hint"] = d["budget_hint"]
    for key in ["liked_brands", "disliked_brands", "style_notes"]:
        if d[key]:
            prof[key] = sorted(set(prof.get(key, [])) | set(d[key]))
    if d["feedback"]:
        fb = state.get("feedback", []) + d["feedback"]
        store.put(ns(state["shopper_id"]), "feedback", fb)
        changed.append("feedback")
    if prof != state.get("profile", {}):
        store.put(ns(state["shopper_id"]), "profile", prof)
        changed.append("profile")
    notes = d["human_readable"] if changed else []
    return {"memory_updates": notes, "profile": prof, "messages": ([AIMessage("Noted - " + "; ".join(notes))] if state["intent"] == "feedback" and notes else [])}


def memory_view(state: State) -> dict:
    prof, fb, pur = state.get("profile", {}), state.get("feedback", []), state.get("purchases", [])
    if not (prof or fb or pur):
        return {"messages": [AIMessage("I don't have anything saved about you yet. As we chat I'll remember sizes, budget hints and brands you like or dislike - and you can ask me to forget any of it.")]}
    text = "Here's what I remember:\n" + json.dumps({"profile": prof, "feedback": fb, "purchases": pur}, indent=1) + "\nSay 'forget my budget' (or any item) to remove it."
    return {"messages": [AIMessage(text)]}


def memory_forget(state: State) -> dict:
    req = forgetter.invoke([HumanMessage(f"What does the shopper want forgotten? Fields: sizes, budget_hint, liked_brands, disliked_brands, style_notes, feedback, everything. value = the specific brand/size/sku if they named one.\nMessage: {state['messages'][-1].content}")])
    sid = state["shopper_id"]
    prof = dict(state.get("profile", {}))
    if req.field == "everything":
        for key in ["profile", "feedback"]:
            store.delete(ns(sid), key)
        return {"profile": {}, "feedback": [], "memory_updates": ["Forgot everything"], "messages": [AIMessage("Done - I've forgotten everything I had saved about you.")]}
    if req.field == "feedback":
        store.delete(ns(sid), "feedback")
        return {"feedback": [], "memory_updates": ["Forgot product feedback"], "messages": [AIMessage("Done - product feedback removed.")]}
    if req.field in prof:
        if req.value and isinstance(prof[req.field], list):
            prof[req.field] = [v for v in prof[req.field] if v.lower() != req.value.lower()]
        else:
            prof.pop(req.field)
        store.put(ns(sid), "profile", prof)
        return {"profile": prof, "memory_updates": [f"Forgot {req.field}{' ' + req.value if req.value else ''}"], "messages": [AIMessage(f"Done - I no longer remember your {req.field.replace('_', ' ')}{' for ' + req.value if req.value else ''}.")]}
    return {"messages": [AIMessage(f"I didn't have a {req.field.replace('_', ' ')} saved, so nothing to forget.")]}


def chat(state: State) -> dict:
    r = llm.invoke([HumanMessage(f"You are a friendly shopping assistant for an outdoor and running store. Reply briefly (max 2 sentences) and steer back to how you can help with products. No urgency language.\n{transcript(state['messages'])}")])
    return {"messages": [AIMessage(r.content)]}


# ---- Routers ---------------------------------------------------------------


def route_intent(state: State) -> Literal["build_query", "update_memory", "memory_view", "memory_forget", "chat"]:
    return {"recommend": "build_query", "feedback": "update_memory", "memory_view": "memory_view", "memory_forget": "memory_forget"}.get(state["intent"], "chat")


def after_stock(state: State) -> Literal["build_query", "rank_and_explain"]:
    if len(state["candidates"]) < 3 and not state.get("relaxed"):
        return "build_query"
    return "rank_and_explain"


def mark_relaxed(state: State) -> dict:
    return {"relaxed": True}


# ---- Graph -----------------------------------------------------------------


def build_graph(checkpointer=None):
    b = StateGraph(State)
    for name, fn in [("load_memory", load_memory), ("classify", classify), ("build_query", build_query), ("search", search), ("check_stock", check_stock), ("mark_relaxed", mark_relaxed), ("rank_and_explain", rank_and_explain), ("update_memory", update_memory), ("memory_view", memory_view), ("memory_forget", memory_forget), ("chat", chat)]:
        b.add_node(name, fn)
    b.add_edge(START, "load_memory")
    b.add_edge("load_memory", "classify")
    b.add_conditional_edges("classify", route_intent)
    b.add_edge("build_query", "search")
    b.add_edge("search", "check_stock")
    b.add_conditional_edges("check_stock", after_stock, {"build_query": "mark_relaxed", "rank_and_explain": "rank_and_explain"})
    b.add_edge("mark_relaxed", "build_query")
    b.add_edge("rank_and_explain", "update_memory")
    for n in ["update_memory", "memory_view", "memory_forget", "chat"]:
        b.add_edge(n, END)
    return b.compile(checkpointer=checkpointer or InMemorySaver(), store=store)


graph = build_graph()

# ---- CLI -------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="E-commerce Product Recommender Agent")
    p.add_argument("--shopper", default="S-90213")
    p.add_argument("--thread", default="session-1", help="Use a new value each visit to simulate a new session")
    p.add_argument("--graph", action="store_true")
    a = p.parse_args()
    if a.graph:
        print(graph.get_graph().draw_mermaid())
        return
    load_store()
    cfg = {"configurable": {"thread_id": f"{a.shopper}:{a.thread}"}}
    print(f"Shopping assistant (shopper={a.shopper}, session={a.thread}). Type 'quit' to exit.")
    print("Try: Need trail running shoes under $120, I'm a US 9 and I don't like Brand X  |  What do you know about me?  |  Forget my budget\n")
    try:
        while True:
            msg = input("you> ").strip()
            if msg.lower() in {"quit", "exit", "q"}:
                break
            if not msg:
                continue
            out = graph.invoke({"messages": [HumanMessage(msg)], "shopper_id": a.shopper, "relaxed": False, "memory_updates": []}, cfg)
            print(f"\nagent> {out['messages'][-1].content}")
            if out.get("memory_updates"):
                print("   [memory] " + "; ".join(out["memory_updates"]))
            print(f"   [intent={out.get('intent')}]\n")
    finally:
        save_store({a.shopper})
        print(f"(memory saved to {STORE_FILE})")


if __name__ == "__main__":
    main()
