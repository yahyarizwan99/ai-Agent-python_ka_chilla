# LangGraph Agent Prompt Library

A library of **prompt-first specifications** for building LangGraph agents. Nothing here is runnable code — every file is a spec you hand to an LLM (or a teammate) to scaffold the real implementation.

## Target stack

| Item          | Version / choice                                |
| ------------- | ----------------------------------------------- |
| Python        | 3.11+                                           |
| LangGraph     | 0.2+ (`StateGraph` API)                      |
| Tools         | LangChain`@tool` decorator, `ToolNode`      |
| Default model | Claude Sonnet 5 (`claude-sonnet-5`)           |
| Checkpointer  | `MemorySaver` for dev, SQLite for persistence |

State these once; every spec below assumes them.

## How to use this library

1. Open [00_master_prompt_template.md](00_master_prompt_template.md).
2. Fill every `{{PLACEHOLDER}}` for your use case (the adaptation checklist at the bottom tells you which ones matter most).
3. Paste the filled prompt into an LLM. The output is a complete agent spec with the same 11 sections as the files in [agents/](agents/).
4. Use one of the 11 filled specs as a worked reference — pick the one whose *pattern* is closest to your problem, not necessarily the one whose *domain* matches.

## Agent index

| #  | Agent                                                          | Domain         | Complexity | Pattern showcased                                 |
| -- | -------------------------------------------------------------- | -------------- | ---------- | ------------------------------------------------- |
| 01 | [SQL Data Analyst](agents/01_sql_data_analyst.md)               | Data analytics | 1          | Tool calling + self-correction loop on SQL errors |
| 02 | [CSV/Excel Data Analyst](agents/02_csv_excel_data_analyst.md)   | Data analytics | 1          | File-based tools + structured output              |
| 03 | [BI Dashboard Insights](agents/03_bi_dashboard_insights.md)     | Data analytics | 2          | Multi-step reasoning, summariser node             |
| 04 | [Customer Support](agents/04_customer_support.md)               | CX             | 1          | Router / conditional edges by intent              |
| 05 | [HR Resume Screener](agents/05_hr_resume_screener.md)           | HR             | 2          | Parallel fan-out over candidates + reduce         |
| 06 | [Finance Expense Auditor](agents/06_finance_expense_auditor.md) | Finance        | 2          | Rule-based checks + human-in-the-loop approval    |
| 07 | [Marketing Content](agents/07_marketing_content.md)             | Marketing      | 2          | Generate → critique → revise loop               |
| 08 | [Legal Document Reviewer](agents/08_legal_document_reviewer.md) | Legal          | 3          | Long-document chunking + citation state           |
| 09 | [Healthcare Intake](agents/09_healthcare_intake.md)             | Healthcare     | 3          | Guardrails, PII handling, escalation              |
| 10 | [DevOps Incident Triage](agents/10_devops_incident_triage.md)   | DevOps         | 3          | Multi-tool orchestration + severity routing       |
| 11 | [E-commerce Recommender](agents/11_ecommerce_recommender.md)    | Retail         | 3          | Memory / checkpointing across sessions            |

Complexity ramps 1 → 3, so the library can also be read front to back.

## Spec contract

Every file in `agents/` has **exactly these 11 `##` headings, in this order**:

1. Role & Persona
2. Objective & Success Criteria
3. Inputs / Outputs
4. State Schema
5. Graph Design
6. Tools
7. Per-Node System Prompts
8. Guardrails & Safety
9. Example Run
10. Evaluation
11. Stretch Extensions

If you add a new agent, keep the contract — it is what makes the specs mechanically checkable and comparable.
