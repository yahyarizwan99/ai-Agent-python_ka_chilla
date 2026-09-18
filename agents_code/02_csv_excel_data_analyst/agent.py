"""
02 - CSV/Excel Data Analyst Agent
Pattern: file-based tools + structured output (Pydantic-validated result).
Spec: ../../guide/agents/02_csv_excel_data_analyst.md
"""
from __future__ import annotations

import argparse
import json
import operator
import os
import re
from typing import Annotated, Literal, TypedDict

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

load_dotenv()
MODEL = os.getenv("MODEL", "claude-sonnet-5")
MAX_ROWS = 50
MAX_FORMAT_ATTEMPTS = 2

# ---- Structured output schema ---------------------------------------------


class ChartSpec(BaseModel):
    type: Literal["bar", "line", "scatter"]
    x: str
    y: str
    title: str


class AnalysisResult(BaseModel):
    summary: str = Field(description="1-3 sentences with concrete numbers from the result rows only")
    table: list[dict] = Field(description="The result rows, unchanged, at most 50")
    chart: ChartSpec | None = Field(default=None, description="Only when there is an obvious x/y pair")
    caveats: list[str] = Field(default_factory=list)
    code_used: str


# ---- State -----------------------------------------------------------------


class State(TypedDict, total=False):
    file_path: str
    sheet: str | None
    question: str
    profile: dict
    plan: str
    exec_error: str | None
    exec_retries: Annotated[int, operator.add]
    raw_result: dict
    result: dict | None
    validation_error: str | None
    format_attempts: Annotated[int, operator.add]


# ---- Tools -----------------------------------------------------------------

PII_COLS = re.compile(r"(email|phone|ssn|passport|dob|birth)", re.I)
DENY = re.compile(r"(__|import|open\(|eval\(|exec\(|compile\(|getattr|setattr|globals|locals|os\.|sys\.|subprocess)")


def load_df(path: str, sheet: str | None) -> pd.DataFrame:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return pd.read_csv(path)
    if ext in {".xlsx", ".xls"}:
        return pd.read_excel(path, sheet_name=sheet or 0)
    if ext == ".parquet":
        return pd.read_parquet(path)
    raise ValueError(f"Unsupported file type {ext}. Supported: csv, xlsx, xls, parquet")


def profile_table(path: str, sheet: str | None) -> dict:
    df = load_df(path, sheet)
    sample = df.head(5).copy()
    for c in sample.columns:
        if PII_COLS.search(str(c)):
            sample[c] = "<redacted>"
    return {
        "columns": [str(c) for c in df.columns],
        "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
        "null_pct": {str(c): round(float(v) * 100, 1) for c, v in df.isna().mean().items()},
        "rows": int(len(df)),
        "sample": json.loads(sample.to_json(orient="records", date_format="iso")),
    }


def run_pandas(path: str, sheet: str | None, expr: str, max_rows: int = MAX_ROWS) -> dict:
    """Evaluate ONE pandas expression in a restricted namespace. Returns rows or an error; never raises."""
    if DENY.search(expr):
        return {"error": "Expression contains a forbidden token."}
    try:
        df = load_df(path, sheet)
        value = eval(expr, {"__builtins__": {}}, {"df": df, "pd": pd, "np": np})  # noqa: S307 - sandboxed namespace
        if isinstance(value, pd.Series):
            value = value.reset_index()
        if isinstance(value, pd.DataFrame):
            value = value.head(max_rows)
            return {"rows": json.loads(value.to_json(orient="records", date_format="iso"))}
        return {"rows": [{"value": value if isinstance(value, (int, float, str, bool)) else str(value)}]}
    except Exception as e:  # noqa: BLE001 - any failure is fed back to the planner
        return {"error": f"{type(e).__name__}: {e}"}


# ---- Model -----------------------------------------------------------------

llm = ChatAnthropic(model=MODEL, max_tokens=2048)
formatter = llm.with_structured_output(AnalysisResult)

PLAN_PROMPT = """You are a pandas expert. A DataFrame named `df` is already loaded.
Profile of df (columns, dtypes, null %, sample rows, row count):
{profile}

Write a SINGLE pandas expression (no imports, no prints, no assignments, no semicolons) whose value answers
the question below. The expression must evaluate to a DataFrame, Series, or scalar. Use only column names that
appear in the profile, exactly as spelled. Prefer groupby/agg over loops. Limit output to 50 rows with .head(50)
if it could be larger.

Question: {question}
{repair}
Respond with ONLY the expression, no code fences, no explanation."""

FORMAT_PROMPT = """You convert a computed pandas result into a structured AnalysisResult.
Question: {question}
Expression used: {plan}
Result rows (JSON, already truncated to 50): {rows}
Column null percentages: {null_pct}

Rules:
- summary: 1-3 sentences with concrete numbers taken ONLY from the result rows.
- table: copy the result rows exactly; do not add or rename columns.
- chart: propose a bar/line/scatter spec only if there is an obvious x/y pair; otherwise null.
- caveats: mention any used column with > 5 % nulls, and any ambiguity in the question.
- code_used: the expression verbatim.
{repair}"""


# ---- Nodes -----------------------------------------------------------------


def profile_file(state: State) -> dict:
    return {"profile": profile_table(state["file_path"], state.get("sheet"))}


def plan_analysis(state: State) -> dict:
    repair = ""
    if state.get("exec_error"):
        repair = f"\nYour previous expression raised: {state['exec_error']}\nPrevious expression: {state.get('plan')}\nFix it.\n"
    prompt = PLAN_PROMPT.format(profile=json.dumps(state["profile"], indent=1), question=state["question"], repair=repair)
    reply = llm.invoke([HumanMessage(prompt)])
    text = reply.content if isinstance(reply.content, str) else str(reply.content)
    expr = re.sub(r"^```(?:python)?|```$", "", text.strip(), flags=re.M).strip()
    # column-existence check: any quoted identifier must be a real column
    cols = set(state["profile"]["columns"])
    quoted = set(re.findall(r"['\"]([^'\"]+)['\"]", expr))
    unknown = [q for q in quoted if q not in cols and q not in {"records", "index", "columns"} and len(q) < 40]
    if unknown and state.get("exec_retries", 0) < 1:
        return {"plan": expr, "exec_error": f"Unknown column(s): {unknown}. Available: {sorted(cols)}", "exec_retries": 1}
    return {"plan": expr, "exec_error": None}


def execute_plan(state: State) -> dict:
    res = run_pandas(state["file_path"], state.get("sheet"), state["plan"])
    if "error" in res:
        return {"raw_result": {}, "exec_error": res["error"], "exec_retries": 1}
    return {"raw_result": res, "exec_error": None}


def format_result(state: State) -> dict:
    repair = ""
    if state.get("validation_error"):
        repair = f"\nYour previous output failed schema validation: {state['validation_error']}. Correct it."
    prompt = FORMAT_PROMPT.format(
        question=state["question"],
        plan=state["plan"],
        rows=json.dumps(state["raw_result"].get("rows", []))[:6000],
        null_pct=state["profile"]["null_pct"],
        repair=repair,
    )
    try:
        obj = formatter.invoke([HumanMessage(prompt)])
        return {"result": obj.model_dump(), "validation_error": None, "format_attempts": 1}
    except Exception as e:  # noqa: BLE001
        return {"result": None, "validation_error": str(e), "format_attempts": 1}


def fallback_result(state: State) -> dict:
    err = state.get("validation_error") or state.get("exec_error") or "unknown error"
    return {
        "result": AnalysisResult(
            summary="Could not complete the analysis.",
            table=state.get("raw_result", {}).get("rows", [])[:MAX_ROWS],
            chart=None,
            caveats=[err],
            code_used=state.get("plan", ""),
        ).model_dump()
    }


# ---- Routers ---------------------------------------------------------------


def after_plan(state: State) -> Literal["plan_analysis", "execute_plan"]:
    return "plan_analysis" if state.get("exec_error") else "execute_plan"


def after_execute(state: State) -> Literal["plan_analysis", "format_result", "fallback_result"]:
    if state.get("exec_error"):
        return "plan_analysis" if state.get("exec_retries", 0) < 2 else "fallback_result"
    return "format_result"


def after_format(state: State) -> Literal["format_result", "fallback_result", "__end__"]:
    if state.get("result"):
        return END
    return "format_result" if state.get("format_attempts", 0) < MAX_FORMAT_ATTEMPTS else "fallback_result"


# ---- Graph -----------------------------------------------------------------


def build_graph():
    b = StateGraph(State)
    b.add_node("profile_file", profile_file)
    b.add_node("plan_analysis", plan_analysis)
    b.add_node("execute_plan", execute_plan)
    b.add_node("format_result", format_result)
    b.add_node("fallback_result", fallback_result)
    b.add_edge(START, "profile_file")
    b.add_edge("profile_file", "plan_analysis")
    b.add_conditional_edges("plan_analysis", after_plan)
    b.add_conditional_edges("execute_plan", after_execute)
    b.add_conditional_edges("format_result", after_format)
    b.add_edge("fallback_result", END)
    return b.compile()


graph = build_graph()

# ---- CLI -------------------------------------------------------------------


def make_sample(path: str) -> None:
    rng = np.random.default_rng(3)
    n = 400
    df = pd.DataFrame(
        {
            "order_id": np.arange(1000, 1000 + n),
            "region": rng.choice(["North", "South", "East", "West"], n),
            "product": rng.choice(["Aurora Lamp", "Nimbus Chair", "Pulse Speaker", "Halo Monitor"], n),
            "amount": np.round(rng.gamma(2.0, 60.0, n), 2),
            "discount": np.where(rng.random(n) < 0.12, np.nan, np.round(rng.random(n) * 0.3, 2)),
            "order_date": pd.date_range("2026-04-01", periods=n, freq="6h").strftime("%Y-%m-%d"),
            "customer_email": [f"user{i}@example.com" for i in range(n)],
        }
    )
    df.to_csv(path, index=False)


def analyse(file_path: str, question: str, sheet: str | None = None) -> dict:
    out = graph.invoke({"file_path": file_path, "sheet": sheet, "question": question, "exec_retries": 0, "format_attempts": 0})
    return out["result"]


def main() -> None:
    p = argparse.ArgumentParser(description="CSV/Excel Data Analyst Agent")
    p.add_argument("question", nargs="?")
    p.add_argument("--file", default=os.path.join(os.path.dirname(__file__), "sample_sales.csv"))
    p.add_argument("--sheet", default=None, help="Excel sheet name (optional)")
    p.add_argument("--graph", action="store_true")
    a = p.parse_args()

    if a.graph:
        print(graph.get_graph().draw_mermaid())
        return
    if not os.path.exists(a.file):
        if a.file.endswith("sample_sales.csv"):
            make_sample(a.file)
            print(f"Created sample file {a.file}\n")
        else:
            raise SystemExit(f"File not found: {a.file}")

    def show(q: str) -> None:
        r = analyse(a.file, q, a.sheet)
        print("\n" + r["summary"])
        if r["table"]:
            print(pd.DataFrame(r["table"]).head(15).to_string(index=False))
        if r.get("chart"):
            print(f"chart: {r['chart']}")
        if r["caveats"]:
            print("caveats: " + "; ".join(r["caveats"]))
        print(f"code: {r['code_used']}\n")

    if a.question:
        show(a.question)
        return
    print(f"CSV/Excel Analyst on {os.path.basename(a.file)} (type 'quit' to exit)")
    print("Try: Average order amount by region\n")
    while True:
        q = input("you> ").strip()
        if q.lower() in {"quit", "exit", "q"}:
            break
        if q:
            show(q)


if __name__ == "__main__":
    main()
