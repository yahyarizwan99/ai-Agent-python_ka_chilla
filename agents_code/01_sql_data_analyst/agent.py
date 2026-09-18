"""
01 - SQL Data Analyst Agent
Pattern: tool calling + self-correction loop on SQL errors.
Spec: ../../guide/agents/01_sql_data_analyst.md
"""
from __future__ import annotations

import argparse
import operator
import os
import re
import sqlite3
from typing import Annotated, Literal, TypedDict

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

load_dotenv()
MODEL = os.getenv("MODEL", "claude-sonnet-5")
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(__file__), "sales.db"))
MAX_ROWS = int(os.getenv("MAX_ROWS", "50"))
MAX_ATTEMPTS = 3

# ---- State -----------------------------------------------------------------


class State(TypedDict, total=False):
    question: str
    schema_text: str
    sql: str
    rows: list[dict]
    error: str | None
    attempts: Annotated[int, operator.add]
    answer: str


# ---- Tools (local SQLite; swap for your DB) ---------------------------------

SENSITIVE = re.compile(r"(email|ssn|password|phone)", re.I)


def get_schema(db_path: str) -> str:
    """Return a compact DDL summary: table(col type, ...). Sensitive columns are named but never sampled."""
    con = sqlite3.connect(db_path)
    try:
        tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        lines = []
        for t in tables:
            cols = con.execute(f"PRAGMA table_info({t})").fetchall()
            lines.append(f"{t}(" + ", ".join(f"{c[1]} {c[2]}" for c in cols) + ")")
        return "\n".join(lines)
    finally:
        con.close()


def execute_sql(db_path: str, sql: str, max_rows: int) -> dict:
    """Read-only execution. Returns {'rows': [...]} or {'error': '...'}. Never raises."""
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I):
        return {"error": "Only SELECT/WITH statements are allowed."}
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.execute(sql)
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, r)) for r in cur.fetchmany(max_rows)]
        return {"rows": rows}
    except sqlite3.Error as e:
        return {"error": str(e)}
    finally:
        con.close()


# ---- Model -----------------------------------------------------------------

llm = ChatAnthropic(model=MODEL, max_tokens=2048)

WRITE_SQL_PROMPT = """You are an expert SQL analyst. Write ONE read-only SQL query (SQLite dialect)
that answers the user's question using only the tables and columns below.

SCHEMA:
{schema_text}

RULES:
- Only SELECT or WITH statements. Never modify data.
- Always add LIMIT {max_rows} unless the query is an aggregate returning one row.
- Use explicit JOINs and qualified column names.
- Return ONLY the SQL inside a ```sql fence, nothing else.
- If the question is not answerable from this database (unrelated, or asks to modify data),
  reply with exactly: NOT_A_DATA_QUESTION
{repair}"""

SUMMARISE_PROMPT = """You are a data analyst explaining a query result to a non-technical manager.
Question: {question}
SQL used: {sql}
Rows (JSON): {rows}
Error (if any): {error}

Write 1-2 sentences answering the question with concrete numbers, then a markdown table of at most 10 rows,
then the SQL in a code block. If rows is empty, say so plainly and suggest one likely reason.
If an error is present instead of rows, explain that the query could not be completed after {max_attempts}
attempts and show the last error. If the error says the question was refused, explain that this assistant
only answers read-only questions about the database. Never invent numbers."""


# ---- Nodes -----------------------------------------------------------------


def inspect_schema(state: State) -> dict:
    return {"schema_text": get_schema(DB_PATH)}


def write_sql(state: State) -> dict:
    repair = ""
    if state.get("error") and state.get("sql"):
        repair = f"\nYour previous query failed. Fix it.\nPREVIOUS SQL:\n{state['sql']}\nERROR:\n{state['error']}\n"
    prompt = WRITE_SQL_PROMPT.format(schema_text=state["schema_text"], max_rows=MAX_ROWS, repair=repair)
    reply = llm.invoke([SystemMessage(prompt), HumanMessage(state["question"])])
    text = reply.content if isinstance(reply.content, str) else str(reply.content)
    if "NOT_A_DATA_QUESTION" in text:
        return {"sql": "", "error": "refused"}
    m = re.search(r"```sql\s*(.*?)```", text, re.S | re.I)
    sql = (m.group(1) if m else text).strip().rstrip(";")
    return {"sql": sql, "error": None}


def validate_sql(state: State) -> dict:
    if state.get("error") == "refused":
        return {}
    sql = state["sql"]
    if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I):
        return {"error": "Static check failed: query must start with SELECT or WITH."}
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|ATTACH|PRAGMA)\b", sql, re.I):
        return {"error": "Static check failed: data-modifying keyword detected."}
    if ";" in sql:
        return {"error": "Static check failed: statement chaining is not allowed."}
    return {"error": None}


def run_sql(state: State) -> dict:
    result = execute_sql(DB_PATH, state["sql"], MAX_ROWS)
    if "error" in result:
        return {"rows": [], "error": result["error"], "attempts": 1}
    return {"rows": result["rows"], "error": None, "attempts": 1}


def summarise(state: State) -> dict:
    prompt = SUMMARISE_PROMPT.format(
        question=state["question"],
        sql=state.get("sql") or "(none)",
        rows=state.get("rows", [])[:10],
        error=state.get("error") or "none",
        max_attempts=MAX_ATTEMPTS,
    )
    reply = llm.invoke([HumanMessage(prompt)])
    return {"answer": reply.content if isinstance(reply.content, str) else str(reply.content)}


# ---- Routers ---------------------------------------------------------------


def after_validate(state: State) -> Literal["write_sql", "run_sql", "summarise"]:
    if state.get("error") == "refused":
        return "summarise"
    return "write_sql" if state.get("error") else "run_sql"


def after_run(state: State) -> Literal["write_sql", "summarise"]:
    if state.get("error") and state.get("attempts", 0) < MAX_ATTEMPTS:
        return "write_sql"
    return "summarise"


# ---- Graph -----------------------------------------------------------------


def build_graph():
    b = StateGraph(State)
    b.add_node("inspect_schema", inspect_schema)
    b.add_node("write_sql", write_sql)
    b.add_node("validate_sql", validate_sql)
    b.add_node("run_sql", run_sql)
    b.add_node("summarise", summarise)
    b.add_edge(START, "inspect_schema")
    b.add_edge("inspect_schema", "write_sql")
    b.add_edge("write_sql", "validate_sql")
    b.add_conditional_edges("validate_sql", after_validate)
    b.add_conditional_edges("run_sql", after_run)
    b.add_edge("summarise", END)
    return b.compile()


graph = build_graph()

# ---- CLI -------------------------------------------------------------------


def ask(question: str) -> dict:
    return graph.invoke({"question": question, "attempts": 0})


def main() -> None:
    p = argparse.ArgumentParser(description="SQL Data Analyst Agent")
    p.add_argument("question", nargs="?", help="Natural-language question about the database")
    p.add_argument("--graph", action="store_true", help="Print the mermaid diagram and exit")
    a = p.parse_args()

    if a.graph:
        print(graph.get_graph().draw_mermaid())
        return

    if not os.path.exists(DB_PATH):
        from seed_db import seed
        seed(DB_PATH)
        print(f"Seeded sample database at {DB_PATH}\n")

    if a.question:
        r = ask(a.question)
        print(r["answer"])
        print(f"\n[attempts: {r.get('attempts')}]")
        return

    print("SQL Data Analyst - ask questions about the sales database (type 'quit' to exit)")
    print("Try: Which 5 products had the highest revenue last month?\n")
    while True:
        q = input("you> ").strip()
        if q.lower() in {"quit", "exit", "q"}:
            break
        if not q:
            continue
        r = ask(q)
        print("\n" + r["answer"] + f"\n[attempts: {r.get('attempts')}]\n")


if __name__ == "__main__":
    main()
