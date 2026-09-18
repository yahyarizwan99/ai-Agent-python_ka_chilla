"""
09 - Healthcare Intake Agent
Pattern: fail-closed safety screen on every turn, PII scrubbing before the model, output check on every reply,
escalation to humans. Collects a structured intake record; never diagnoses or recommends treatment.
Spec: ../../guide/agents/09_healthcare_intake.md
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
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

load_dotenv()
MODEL = os.getenv("MODEL", "claude-sonnet-5")
MAX_TURNS = 12
MAX_REPHRASE = 2
REQUIRED_FIELDS = ["reason", "symptoms", "onset", "severity_self_rated", "medications", "allergies", "history"]

RED_FLAG_CATEGORIES = ["chest_pain", "breathing_difficulty", "stroke_signs", "severe_bleeding", "anaphylaxis", "suicidal_ideation", "self_harm", "overdose", "loss_of_consciousness", "severe_abdominal_pain_pregnancy", "infant_under_3mo_fever"]

EMERGENCY_SCRIPT = (
    "What you're describing may need urgent care. Please call your local emergency number (1122 in Pakistan, 911 in the US, 112 in the EU) now, "
    "or go to the nearest emergency department. I have alerted the clinic staff. This chat cannot provide emergency help."
)
STAFF_SCRIPT = "Thanks for your patience. A member of the clinic team will continue this with you directly."
CONSENT = "Hi, I'm the clinic's intake assistant. I'll ask a few questions so the nurse is prepared for your visit. Your answers are reviewed by a human and I can't give medical advice. You can type 'stop' at any time to talk to a person. What brings you in today?"

# ---- Structured outputs ----------------------------------------------------


class SafetyResult(BaseModel):
    red_flags: list[str] = Field(default_factory=list, description=f"Subset of {RED_FLAG_CATEGORIES} or empty")


class FieldUpdate(BaseModel):
    reason: str | None = None
    symptoms: list[str] | None = None
    onset: str | None = None
    severity_self_rated: int | None = Field(default=None, ge=1, le=10)
    medications: list[str] | None = None
    allergies: list[str] | None = None
    history: list[str] | None = None
    notes: str | None = None


class OutputCheck(BaseModel):
    unsafe: bool = Field(description="True if the text names a condition/diagnosis, recommends a medication, dose or treatment, or judges severity")
    reason: str = ""


# ---- State -----------------------------------------------------------------


def merge_record(old: dict | None, new: dict | None) -> dict:
    out = dict(old or {})
    for k, v in (new or {}).items():
        if v is None:
            continue
        if isinstance(v, list) and isinstance(out.get(k), list):
            out[k] = list(dict.fromkeys(out[k] + v))
        else:
            out[k] = v
    return out


class State(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    patient_token: str
    appointment_type: str
    red_flags: Annotated[list[str], operator.add]
    record: Annotated[dict, merge_record]
    missing_fields: list[str]
    escalation: dict | None
    turns: Annotated[int, operator.add]
    complete: bool
    rephrase_count: int
    stop_requested: bool


# ---- Tools -----------------------------------------------------------------

SCRUB_PATTERNS = [
    (re.compile(r"\b(my name is|i am|i'm|this is)\s+([A-Z][a-z]+(?:\s[A-Z][a-z]+)?)", re.I), r"\1 [NAME]"),
    (re.compile(r"\+?\d[\d\s\-()]{8,}\d"), "[PHONE]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[EMAIL]"),
    (re.compile(r"\b(mrn|patient id|id)\s*[:#]?\s*[A-Z0-9\-]{5,}\b", re.I), "[MRN]"),
    (re.compile(r"\b\d{1,5}\s+[A-Z][a-z]+\s+(street|st|road|rd|avenue|ave|lane|ln|block|phase)\b.*", re.I), "[ADDRESS]"),
]
KEYWORD_FLAGS = {
    "chest_pain": r"chest (pain|tight|pressure)|crushing",
    "breathing_difficulty": r"(can'?t|cannot|hard to|trouble|short(ness)? of) breath|gasping|wheez",
    "stroke_signs": r"face droop|slurred|one side (numb|weak)|sudden (numb|confusion)",
    "severe_bleeding": r"(won'?t|will not) stop bleeding|bleeding heavily|blood everywhere",
    "anaphylaxis": r"throat (closing|swelling)|swollen (tongue|lips)|anaphyla",
    "suicidal_ideation": r"kill myself|end (it all|my life)|suicid|don'?t want to (be here|live|wake up)",
    "self_harm": r"hurt(ing)? myself|cut(ting)? myself",
    "overdose": r"overdos|took (too many|a whole bottle|all the) pills",
    "loss_of_consciousness": r"passed out|fainted|blacked out|unconscious",
}


def scrub(text: str) -> dict:
    entities = []
    for pat, repl in SCRUB_PATTERNS:
        if pat.search(text):
            entities.append(repl)
            text = pat.sub(repl, text)
    return {"text": text, "entities": entities}


def notify_staff(patient_token: str, level: str, summary: str) -> bool:
    print(f"\n   [notify_staff] level={level} patient={patient_token} summary={summary!r}")
    return True


def submit_record(patient_token: str, record: dict) -> dict:
    out = f"intake_{patient_token}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return {"id": out}


# ---- Model -----------------------------------------------------------------

llm = ChatAnthropic(model=MODEL, max_tokens=1024)
safety_llm = llm.with_structured_output(SafetyResult)
extractor = llm.with_structured_output(FieldUpdate)
output_checker = llm.with_structured_output(OutputCheck)


def recent(msgs: list[AnyMessage], n: int = 3) -> str:
    return "\n".join(("patient: " if isinstance(m, HumanMessage) else "assistant: ") + str(m.content) for m in msgs[-n:])


# ---- Nodes -----------------------------------------------------------------


def scrub_phi(state: State) -> dict:
    last = state["messages"][-1]
    if not isinstance(last, HumanMessage):
        return {}
    try:
        cleaned = scrub(str(last.content))["text"]
    except Exception:  # noqa: BLE001 - fail closed: do not process an unscrubbed message
        return {"messages": [HumanMessage(content="[message could not be processed]", id=last.id)]}
    stop = bool(re.search(r"\b(stop|human|person|nurse)\b", cleaned, re.I)) and len(cleaned.split()) <= 6
    return {"messages": [HumanMessage(content=cleaned, id=last.id)], "stop_requested": stop}


def safety_screen(state: State) -> dict:
    text = recent(state["messages"]).lower()
    flags = {cat for cat, pat in KEYWORD_FLAGS.items() if re.search(pat, text)}
    try:
        r = safety_llm.invoke([HumanMessage(f"You are a triage safety classifier, not a clinician. Read the patient's messages and list matched emergency categories from {RED_FLAG_CATEGORIES}, or an empty list. Match on described symptoms, not just keywords. When unsure between a category and none, choose the category.\nMessages:\n{recent(state['messages'])}")])
        flags |= {f for f in r.red_flags if f in RED_FLAG_CATEGORIES}
    except Exception as e:  # noqa: BLE001 - fail closed
        flags.add(f"classifier_error:{e.__class__.__name__}")
    return {"red_flags": sorted(flags), "turns": 1}


def escalate(state: State) -> dict:
    flags = state.get("red_flags", [])
    level = "emergency" if any(f in RED_FLAG_CATEGORIES for f in flags) or any(f.startswith("classifier_error") for f in flags) else "staff"
    summary = f"flags={flags}; record={json.dumps(state.get('record', {}))}"
    notify_staff(state["patient_token"], level, summary)
    return {"escalation": {"level": level, "reason": flags or ["turn cap / stop requested"], "instructions_given": True}, "messages": [AIMessage(EMERGENCY_SCRIPT if level == "emergency" else STAFF_SCRIPT)], "complete": True}


def extract_fields(state: State) -> dict:
    last = state["messages"][-1].content
    try:
        upd = extractor.invoke([HumanMessage(f"Extract intake fields from the patient's latest message. Only fill fields the patient actually stated; never infer a condition. Record medications and allergies exactly as spelled. Leave everything else null.\nCurrent record: {json.dumps(state.get('record', {}))}\nLatest message: {last}")])
        return {"record": {k: v for k, v in upd.model_dump().items() if v is not None}}
    except Exception:  # noqa: BLE001
        return {}


def plan_next_question(state: State) -> dict:
    rec = state.get("record", {})
    missing = [f for f in REQUIRED_FIELDS if not rec.get(f)]
    if not missing:
        return {"missing_fields": [], "complete": True}
    nxt = missing[0]
    friendly = {"reason": "the main reason for the visit", "symptoms": "the symptoms they are experiencing", "onset": "when the symptoms started", "severity_self_rated": "how much it affects them on a scale of 1-10", "medications": "any medications or supplements they currently take (or none)", "allergies": "any allergies to medicines or foods (or none)", "history": "any ongoing health conditions or past surgeries (or none)"}[nxt]
    r = llm.invoke([HumanMessage(f"You are a friendly clinic intake assistant. Ask the patient ONE clear question to collect: {friendly}. One sentence, everyday language, optionally a short example of the kind of answer that helps. Do not comment on what they have said so far, do not reassure, do not suggest causes or remedies.")])
    return {"missing_fields": missing, "complete": False, "messages": [AIMessage(r.content)], "rephrase_count": state.get("rephrase_count", 0)}


SAFE_FALLBACKS = {"reason": "Could you tell me the main reason for your visit today?", "symptoms": "What symptoms are you experiencing?", "onset": "When did this start?", "severity_self_rated": "On a scale of 1 to 10, how much is this affecting your day?", "medications": "Are you currently taking any medications or supplements?", "allergies": "Do you have any allergies to medicines or foods?", "history": "Do you have any ongoing health conditions or past surgeries?"}


def output_check(state: State) -> dict:
    last = state["messages"][-1]
    try:
        chk = output_checker.invoke([HumanMessage(f"Does this message from a clinic intake assistant contain a diagnosis, a named condition, a medication/dose/treatment recommendation, or a judgement of how serious the situation is? Text: {last.content}")])
        unsafe = chk.unsafe
    except Exception:  # noqa: BLE001 - fail closed
        unsafe = True
    if not unsafe:
        return {"rephrase_count": 0}
    n = state.get("rephrase_count", 0) + 1
    if n >= MAX_REPHRASE:
        nxt = (state.get("missing_fields") or ["reason"])[0]
        return {"messages": [AIMessage(content=SAFE_FALLBACKS[nxt], id=last.id)], "rephrase_count": 0}
    return {"messages": [AIMessage(content="[unsafe wording removed]", id=last.id)], "rephrase_count": n}


def finalise(state: State) -> dict:
    res = submit_record(state["patient_token"], state.get("record", {}))
    return {"messages": [AIMessage(f"Thank you - that's everything the nurse needs for now (record {res['id']}). A clinician will review it before your appointment. If anything gets worse before then, please contact the clinic or emergency services.")]}


# ---- Routers ---------------------------------------------------------------


def after_screen(state: State) -> Literal["escalate", "extract_fields"]:
    if state.get("red_flags") or state.get("turns", 0) > MAX_TURNS or state.get("stop_requested"):
        return "escalate"
    return "extract_fields"


def after_plan(state: State) -> Literal["finalise", "output_check"]:
    return "finalise" if state.get("complete") else "output_check"


def after_output_check(state: State) -> Literal["plan_next_question", "__end__"]:
    return "plan_next_question" if state["messages"][-1].content == "[unsafe wording removed]" else END


# ---- Graph -----------------------------------------------------------------


def build_graph(checkpointer=None):
    b = StateGraph(State)
    for name, fn in [("scrub_phi", scrub_phi), ("safety_screen", safety_screen), ("escalate", escalate), ("extract_fields", extract_fields), ("plan_next_question", plan_next_question), ("output_check", output_check), ("finalise", finalise)]:
        b.add_node(name, fn)
    b.add_edge(START, "scrub_phi")
    b.add_edge("scrub_phi", "safety_screen")
    b.add_conditional_edges("safety_screen", after_screen)
    b.add_edge("escalate", END)
    b.add_edge("extract_fields", "plan_next_question")
    b.add_conditional_edges("plan_next_question", after_plan)
    b.add_conditional_edges("output_check", after_output_check)
    b.add_edge("finalise", END)
    return b.compile(checkpointer=checkpointer or InMemorySaver())


graph = build_graph()

# ---- CLI -------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(description="Healthcare Intake Agent")
    p.add_argument("--patient", default="PT-7f3a")
    p.add_argument("--thread", default=None)
    p.add_argument("--graph", action="store_true")
    a = p.parse_args()
    if a.graph:
        print(graph.get_graph().draw_mermaid())
        return
    thread = a.thread or f"intake-{a.patient}"
    cfg = {"configurable": {"thread_id": thread}}
    print(f"assistant> {CONSENT}\n")
    while True:
        msg = input("patient> ").strip()
        if not msg:
            continue
        if msg.lower() in {"quit", "exit"}:
            break
        out = graph.invoke({"messages": [HumanMessage(msg)], "patient_token": a.patient, "appointment_type": "general", "red_flags": [], "turns": 0}, cfg)
        print(f"\nassistant> {out['messages'][-1].content}")
        print(f"   [turn {out.get('turns')} | record: {json.dumps(out.get('record', {}))}]\n")
        if out.get("escalation") or out.get("complete"):
            break


if __name__ == "__main__":
    main()
