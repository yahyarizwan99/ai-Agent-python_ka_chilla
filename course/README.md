# LangGraph for Beginners — Course Outline

A 12-module course that takes someone who knows basic Python from "what is an agent?" to building, debugging, and shipping a multi-step LangGraph agent.

## Who this is for

- You can write Python functions, use dicts and lists, and have seen a class definition.
- You have **never** used LangGraph or LangChain.
- You have an API key for an LLM provider (the course uses Claude Sonnet 5 by default; any chat model works).

## Stack used throughout

| Item | Version / choice |
|---|---|
| Python | 3.11+ |
| LangGraph | 0.2+ (`StateGraph` API) |
| LangChain | tool decorators + chat model wrappers |
| Model | `claude-sonnet-5` via `langchain-anthropic` |
| Checkpointer | `MemorySaver` (in-memory) for lessons; SQLite mentioned in Module 06 |

## Learning objectives

By the end you can:

1. Explain what State, Nodes, and Edges are and why a graph beats a loop for agents.
2. Build a graph, give it tools, and route between nodes with conditions.
3. Persist conversations, pause for human approval, and resume.
4. Compose supervisors, parallel branches, and subgraphs.
5. Stream output, trace runs, add guardrails, and understand deployment options.
6. Read an agent spec and turn it into a working graph.

## How each module is structured

Modules are written **slide-style**:

- Every `## ` heading is one slide, with at most 6 bullets and one idea.
- Under most slides is a `> 🎤 Talking points` block — the speaker notes.
- Code snippets are short (≤ 10 lines) and illustrative, not full programs.
- Every module ends with **Recap**, **Exercise** (10–15 min, with a hint), and **Common mistakes**.

Estimated time: 30–45 minutes per module including the exercise.

## Modules

| # | Module | Concepts introduced |
|---|---|---|
| 00 | [Setup & Mental Model](modules/00_setup_and_mental_model.md) | Install, API key, `llm.invoke`, what an "agent" is, agent vs chain |
| 01 | [Why LangGraph](modules/01_why_langgraph.md) | Graph vs loop; State / Node / Edge vocabulary |
| 02 | [Your First Graph](modules/02_your_first_graph.md) | `TypedDict` state, `StateGraph`, `add_node`, `add_edge`, `START`/`END`, `compile`, `graph.invoke` |
| 03 | [State Schemas](modules/03_state_schemas.md) | `Annotated` reducers, `operator.add`, `add_messages`, message types |
| 04 | [Tools](modules/04_tools.md) | `@tool`, `bind_tools`, `ToolNode`, `tools_condition`, the tool-call loop |
| 05 | [Conditional Edges & Routing](modules/05_conditional_edges.md) | `add_conditional_edges`, router functions, `Literal` return types |
| 06 | [Memory & Checkpointing](modules/06_memory_and_checkpointing.md) | `MemorySaver`, `thread_id`, `config`, `get_state`, SQLite saver |
| 07 | [Human-in-the-Loop](modules/07_human_in_the_loop.md) | `interrupt`, `Command(resume=...)`, approval gates |
| 08 | [Multi-Agent Patterns](modules/08_multi_agent_patterns.md) | Supervisor, `Send` fan-out, subgraphs |
| 09 | [Streaming & Observability](modules/09_streaming_and_observability.md) | `stream` modes, `astream_events`, LangSmith tracing |
| 10 | [Guardrails & Deployment](modules/10_guardrails_and_deployment.md) | Loop limits, input validation, error nodes, `Store`, LangGraph Platform overview |
| 11 | [Capstone Case Studies](modules/11_capstone_case_studies.md) | Walk through four agents from the guide end to end |

Concepts are introduced strictly in this order; no module uses an API name before the module that introduces it.

## Prerequisites checklist

- [ ] Python 3.11+ installed and on your PATH
- [ ] A virtual environment you know how to activate
- [ ] An `ANTHROPIC_API_KEY` (or another provider's key) in your environment
- [ ] A code editor and a terminal
