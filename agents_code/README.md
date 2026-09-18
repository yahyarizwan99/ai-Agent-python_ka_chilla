# Runnable LangGraph Agents

Working implementations of the 11 agents specified in [`../guide/agents/`](../guide/agents/). Each agent lives in its own folder with its **own virtual environment, `requirements.txt`, sample data, and README** — you can run any one of them without touching the others.

| # | Folder | Pattern | External services needed |
|---|---|---|---|
| 01 | [01_sql_data_analyst](01_sql_data_analyst/) | Tool loop + SQL self-correction | none (SQLite auto-seeded) |
| 02 | [02_csv_excel_data_analyst](02_csv_excel_data_analyst/) | File tools + structured output | none (sample CSV auto-created) |
| 03 | [03_bi_dashboard_insights](03_bi_dashboard_insights/) | Multi-step reasoning + summariser | none (JSON metrics) |
| 04 | [04_customer_support](04_customer_support/) | Intent router + checkpointed chat | none (in-memory orders/KB) |
| 05 | [05_hr_resume_screener](05_hr_resume_screener/) | `Send` fan-out + reduce | none (text resumes) |
| 06 | [06_finance_expense_auditor](06_finance_expense_auditor/) | Rules + `interrupt` approval, SQLite resume | none |
| 07 | [07_marketing_content](07_marketing_content/) | Generate → critique → revise loop | none |
| 08 | [08_legal_document_reviewer](08_legal_document_reviewer/) | Chunking + fan-out + citation verification | none (sample contract, optional PDF) |
| 09 | [09_healthcare_intake](09_healthcare_intake/) | Fail-closed guardrails + PII scrub + escalation | none |
| 10 | [10_devops_incident_triage](10_devops_incident_triage/) | Parallel gather + severity matrix + approval gate | none (JSON fixtures) |
| 11 | [11_ecommerce_recommender](11_ecommerce_recommender/) | Thread checkpointer + cross-session `Store` | none (JSON catalogue) |

All external systems (databases, order APIs, metrics, EHR, paging) are replaced with **local fakes** so every agent runs end to end on a laptop. Swap the functions in each `agent.py`'s `# ---- Tools` section for real integrations.

## Requirements

- Python **3.11+** (tested on 3.12)
- An Anthropic API key: <https://console.anthropic.com/>
- Internet access for model calls

Default model is `claude-sonnet-5` (the course/guide stack). Override with `MODEL=claude-opus-5` in `.env` if you want the stronger model.

## One-time setup for any agent

Every agent folder follows the same recipe. Replace `01_sql_data_analyst` with the folder you want.

### Windows (PowerShell)

```powershell
cd agents_code\01_sql_data_analyst
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
copy .env.example .env      # then edit .env and paste your API key
python agent.py
```

If PowerShell refuses to run the activate script: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry.

### macOS / Linux

```bash
cd agents_code/01_sql_data_analyst
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env        # then edit .env and paste your API key
python agent.py
```

### Leaving / re-entering the environment

- Deactivate: `deactivate`
- Re-activate later: `.\.venv\Scripts\Activate.ps1` (Windows) or `source .venv/bin/activate` (macOS/Linux)
- Delete and rebuild: remove the `.venv` folder and repeat the steps

## Common conventions in every `agent.py`

- `load_dotenv()` reads `.env` → `ANTHROPIC_API_KEY`, optional `MODEL`.
- Sections are marked `# ---- State`, `# ---- Tools`, `# ---- Nodes`, `# ---- Graph`, `# ---- CLI` and map one-to-one onto the spec sections.
- `python agent.py --graph` prints the mermaid diagram of the compiled graph so you can compare it with the spec.
- Run without arguments for an interactive demo; each README lists the flags.

## Verifying your setup without spending tokens

```bash
python agent.py --graph
```

builds and compiles the graph, prints the diagram, and exits **without calling the model**. If that works, your environment is fine.
