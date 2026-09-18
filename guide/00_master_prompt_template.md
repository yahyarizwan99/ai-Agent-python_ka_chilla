# Master Prompt Template — LangGraph Agent Spec Generator

Copy the block below, replace every `{{PLACEHOLDER}}`, and send it to an LLM. The response is a complete, implementation-ready LangGraph agent spec with 11 fixed sections.

---

## The prompt

```text
You are a senior LangGraph architect. Produce a complete, implementation-ready
specification for a LangGraph agent. Do NOT write runnable code — write a spec a
developer can implement without asking questions.

Target stack: Python 3.11+, LangGraph 0.2+ (StateGraph API), LangChain @tool
decorators, Claude Sonnet 5 as default model, MemorySaver / SQLite checkpointers.

Use EXACTLY the 11 numbered "##" headings below, in this order, with no extras.

Agent name: {{AGENT_NAME}}
Domain: {{DOMAIN}}
Primary user: {{WHO_USES_IT}}
Core LangGraph pattern to showcase: {{PATTERN}}
    (e.g. tool loop, router, fan-out/reduce, human-in-the-loop, critique loop,
     chunking, memory across sessions)
Hard constraints: {{CONSTRAINTS}}
    (e.g. no PII leaves the graph, max 5 tool calls, must cite sources)
Available tools / data sources: {{TOOLS_AND_DATA}}
Tone / persona: {{TONE}}

## 1. Role & Persona
One paragraph: who the agent is, how it speaks, what it will and will not do.

## 2. Objective & Success Criteria
- Objective: one sentence.
- Success criteria: 3-5 measurable bullets (latency, accuracy, coverage,
  escalation rate, etc.).

## 3. Inputs / Outputs
Table: name | type | required | example. One row per input, one per output.
Show one concrete example input and the matching example output.

## 4. State Schema
A TypedDict field table: field | type | reducer (overwrite / add_messages /
operator.add / custom) | purpose. Mark which fields are set by the user, which
by nodes, which are internal.

## 5. Graph Design
- Node list: name | responsibility | reads | writes.
- Edge list: normal edges and conditional edges with their routing rules.
- Loop limits and termination conditions.
- A mermaid `flowchart TD` diagram of the graph. Quote labels that contain
  punctuation. Use START and END nodes.

## 6. Tools
Table: tool name | signature | when the agent calls it | failure behaviour
(retry / fallback / escalate / abort).

## 7. Per-Node System Prompts
For every LLM-backed node: a ready-to-paste system prompt in a fenced block,
plus the exact state fields injected into it.

## 8. Guardrails & Safety
Refusal rules, PII handling, max iterations, human-in-the-loop trigger
conditions, what happens on tool failure, what the agent must never do.

## 9. Example Run
One full trace: user input -> state after each node (only changed fields) ->
final output. Use realistic data.

## 10. Evaluation
At least 5 test cases as a table: id | input | expected behaviour | pass
criterion. Include one happy path, one edge case, one adversarial input,
one tool-failure case, one guardrail trigger.

## 11. Stretch Extensions
Exactly 3 bullets, each one sentence, each a meaningful next step.
```

---

## Filled mini-example (abbreviated)

Placeholders filled for a tiny "Weather Briefing" agent, to show the shape of a good answer. Real specs are 3–5× longer; see [agents/](agents/).

```text
Agent name: Weather Briefing Agent
Domain: Personal productivity
Primary user: Commuters checking the morning forecast
Core LangGraph pattern to showcase: single tool loop
Hard constraints: max 2 tool calls; never invent a forecast if the tool fails
Available tools / data sources: get_forecast(city: str, date: str) -> dict
Tone / persona: brief, friendly, no emoji
```

Expected response outline:

- **1. Role & Persona** — "You are a concise weather assistant…"
- **2. Objective** — "Give a 3-sentence briefing for a city and date." Success: answers in under 5 s; cites the tool; says "unavailable" on tool failure.
- **3. I/O** — input `city`, `date`; output `briefing: str`, `source: str`.
- **4. State** — `messages` (add_messages), `city`, `date`, `forecast` (overwrite), `tool_calls_made: int` (operator.add).
- **5. Graph** — `START → plan → tools → compose → END`, conditional edge from `plan` to `tools` or `compose`; loop limit 2.
- **6. Tools** — `get_forecast`: retry once, then fallback text.
- **7. Prompts** — one system prompt for `plan`, one for `compose`.
- **8. Guardrails** — never fabricate; refuse non-weather questions.
- **9. Example run** — Karachi, tomorrow → forecast dict → 3-sentence briefing.
- **10. Eval** — 5 cases including tool timeout and "what is the capital of France?".
- **11. Stretch** — multi-city, push notifications, hourly granularity.

---

## Adapting for a new domain — checklist

- [ ] **PATTERN** is the most important placeholder. Pick one primary pattern; two at most.
- [ ] **CONSTRAINTS** should be testable — "max 5 tool calls" beats "be efficient".
- [ ] **TOOLS_AND_DATA** must list real signatures. If you don't know them yet, write the ones you *wish* existed; the spec will tell you what to build.
- [ ] Decide **who is the human in the loop** and on what condition they are pulled in — even if the answer is "nobody".
- [ ] Decide **what the agent must never do** before you decide what it should do.
- [ ] Ask for the example run to use **your own real data shape**, not toy values.
- [ ] After generation, check all 11 headings are present and in order; regenerate section-by-section if any are thin.
