# Module 09 — Streaming & Observability

*Time: 35 min · Builds on: Module 08*

## What you will be able to do after this module

- Stream state updates node by node with `stream`
- Stream model tokens as they are generated
- Read a run in LangSmith and find the slow or wrong step
- Add your own trace context

> 🎤 Talking points
> Two audiences here: users (who want to see progress) and developers (who want to see what happened). Same mechanisms serve both.

## Why streaming

- A multi-step agent may take 10–60 seconds
- Users abandon blank screens; showing "searching…", "drafting…" keeps them
- Developers get the same events to debug live
- Nothing in the graph changes — streaming is a different way to *call* it

> 🎤 Talking points
> Show the same graph with `invoke` (silence, then answer) and with `stream` (a line per step). The contrast sells it.

## `stream` — one event per node

```python
for chunk in graph.stream({"messages": [HumanMessage("Hi")]}, config, stream_mode="updates"):
    print(chunk)
# {'agent': {'messages': [AIMessage(...)]}}
# {'tools': {'messages': [ToolMessage(...)]}}
# {'agent': {'messages': [AIMessage(...)]}}
```

- Same arguments as `invoke`, plus `stream_mode`
- `"updates"` yields what each node returned, keyed by node name
- `"values"` yields the full state after each node instead

> 🎤 Talking points
> `"updates"` is the debugging mode: you see exactly what each node wrote. `"values"` is for UIs that re-render the whole state.

## Streaming tokens

```python
for msg, meta in graph.stream(inputs, config, stream_mode="messages"):
    print(msg.content, end="", flush=True)
```

- `"messages"` mode yields model output token by token
- `meta` tells you which node produced it — filter to show only the final answer node
- Works because chat models stream natively; no node changes required

> 🎤 Talking points
> Filter with `if meta["langgraph_node"] == "agent"` so tool-call JSON does not leak into the UI. Beginners forget this and see raw arguments scrolling by.

## Multiple modes at once

```python
for mode, chunk in graph.stream(inputs, config, stream_mode=["updates", "messages"]):
    ...
```

- Pass a list to get tagged events
- Typical UI: tokens for the chat bubble, updates for a progress sidebar

> 🎤 Talking points
> Mention `astream` / `astream_events` exist for async web servers with the same shape — do not dive in; point students to the docs when they build a real frontend.

## From "what" to "why": tracing

- Streaming shows what happened *now*
- Tracing records what happened *for every run*, with timings and inputs
- LangSmith is the hosted tracer built for LangGraph; it needs no code changes

```bash
export LANGSMITH_TRACING=true
export LANGSMITH_API_KEY=ls-...
export LANGSMITH_PROJECT=langgraph-course
```

> 🎤 Talking points
> Three environment variables and every run appears in a web UI as a tree: graph → nodes → model calls → tool calls, each with inputs, outputs, latency, and tokens.

## Reading a trace

- Top level: the graph run, total time, total tokens
- One row per node in order — spot loops by repeated names
- Expand a model call: the exact prompt sent and the exact reply
- Expand a tool call: arguments and result
- Red rows are exceptions

> 🎤 Talking points
> Debugging recipe: find the first node whose *output* looks wrong, then read its *input*. Nine times out of ten the prompt did not contain what you assumed.

## Adding your own context

```python
config = {"configurable": {"thread_id": "t-1"},
          "tags": ["support", "v2"],
          "metadata": {"customer_tier": "gold"}}
```

- Tags and metadata ride along in `config` and show up in the trace
- Filter runs by tag in the UI ("show me all v2 runs that errored")
- Use `run_name` in config to label a run

> 🎤 Talking points
> Encourage tagging by prompt version from day one. When someone asks "did the new prompt help?", the answer is a filter, not an archaeology dig.

## Recap

- `stream(..., stream_mode="updates")` yields per-node changes; `"messages"` yields tokens; pass a list for both.
- Tracing (LangSmith via three env vars) records every run as a tree of nodes, model calls, and tool calls.
- Tags and metadata in `config` make traces searchable; debug by finding the first wrong output and reading its input.

## Exercise

Run the Module 04 agent with `stream_mode=["updates", "messages"]`. Print a progress line (`"→ running tools"`) for each update event and print tokens only from the `agent` node. Then enable tracing and find, in the trace, the exact arguments the model sent to your tool.

*Hint:* the tuple from a multi-mode stream is `(mode, chunk)`; for `"messages"` the chunk is `(message, metadata)`.

## Common mistakes

- Printing every token from every node — tool-call JSON appears in the chat.
- Forgetting `flush=True` when printing tokens — output appears in bursts.
- Enabling tracing in production without thinking about what prompts and data are sent to the tracing service.
