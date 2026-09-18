# Module 03 — State Schemas

*Time: 40 min · Builds on: Module 02*

## What you will be able to do after this module

- Explain what a reducer is and why state needs them
- Use `operator.add` to accumulate lists and counters
- Use `add_messages` to hold a conversation in state
- Tell the three message types apart

> 🎤 Talking points
> This is the most important "boring" module. Almost every confusing LangGraph bug is a reducer bug. Go slowly.

## The problem: what does "merge" mean?

Node returns `{"items": ["b"]}`. State already has `{"items": ["a"]}`. After merging, is `items`:

- `["b"]` (replace)?
- `["a", "b"]` (append)?

- LangGraph must pick one — and the answer should depend on the key
- By default it **replaces**
- A **reducer** tells LangGraph how to combine old and new for one key

> 🎤 Talking points
> Ask the room to vote before revealing. Both answers are reasonable — that is why it must be declared, not guessed.

## Declaring a reducer with `Annotated`

```python
from typing import Annotated, TypedDict
import operator

class State(TypedDict):
    items: Annotated[list[str], operator.add]   # append
    attempts: Annotated[int, operator.add]      # add numbers
    status: str                                  # replace (default)
```

- `Annotated[type, reducer]` attaches a merge function to a key
- `operator.add` on lists = concatenate; on ints = sum
- Keys without a reducer are overwritten

> 🎤 Talking points
> Show `attempts`: a node returns `{"attempts": 1}` and the state goes from 2 to 3. This is how loop counters work in LangGraph — nodes say "+1", never "set to 3".

## Why reducers matter for loops

```python
def try_once(state: State) -> dict:
    return {"attempts": 1, "items": ["tried"]}
```

- Run it three times through a loop → `attempts == 3`, `items` has three entries
- The node never reads `attempts`, so it cannot get it wrong
- Later, an edge will use `attempts` to stop the loop

> 🎤 Talking points
> Preview Module 05: the edge will say `if state["attempts"] >= 3: go to END`. Reducers make that counter trustworthy.

## Conversations are state too

- An LLM call needs the message history
- The natural place for history is a state key: a list of messages
- LangChain gives us message classes; LangGraph gives us a reducer for them

```python
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
```

- `SystemMessage` — instructions for the model
- `HumanMessage` — what the user said
- `AIMessage` — what the model said (may include tool requests, later)

> 🎤 Talking points
> Three types only for now. A fourth type, for tool results, appears in Module 04. Students should be able to say who "authored" each type.

## The `add_messages` reducer

```python
from langgraph.graph import add_messages

class State(TypedDict):
    messages: Annotated[list, add_messages]
```

- Like `operator.add`, it appends
- Plus: if a new message has the same `id` as an old one, it **replaces** it
- Plus: it accepts plain dicts or tuples and converts them to message objects

> 🎤 Talking points
> The replace-by-id rule is why `add_messages` exists — it lets you edit a message (e.g. redact) without duplicating it. Beginners can treat it as "append, but smarter".

## A chat node

```python
def chat(state: State) -> dict:
    reply = llm.invoke(state["messages"])
    return {"messages": [reply]}
```

- Pass the whole list to the model
- Return a one-element list — the reducer appends it
- Never return the full history; that would duplicate everything

> 🎤 Talking points
> This is the single most common pattern in LangGraph. Every chatbot, every agent, has a node shaped exactly like this.

## Running the chat graph

```python
graph.invoke({"messages": [HumanMessage("Hi, I'm Ada")]})
# state["messages"] -> [HumanMessage("Hi, I'm Ada"), AIMessage("Hello Ada! ...")]
```

- Input: a list with one human message
- Output: a list with two messages
- Call `invoke` again with the *output* list plus a new human message to continue the conversation

> 🎤 Talking points
> Note the manual work: the caller must carry the list between calls. Module 06 removes that chore with checkpointing. For now, feel the friction.

## Recap

- A reducer defines how a node's returned value merges into state for one key; the default is overwrite.
- `Annotated[list, operator.add]` appends; `Annotated[int, operator.add]` sums — perfect for loop counters.
- `add_messages` is the reducer for conversation history; chat nodes return `{"messages": [reply]}`.

## Exercise

Build a graph with two nodes: `chat` (as above) and `log` (appends the string `"turn done"` to a `log: Annotated[list[str], operator.add]` key and adds 1 to `turns: Annotated[int, operator.add]`). Run it three times, feeding the output messages back in each time. Check that `turns == 3` and `log` has three entries.

*Hint:* the initial state for run 2 is `{"messages": result["messages"] + [HumanMessage("...")], ...}` — the reducers handle the rest.

## Common mistakes

- Returning the full `messages` list from a node — with `add_messages`, that duplicates the history.
- Forgetting `Annotated` and wondering why the list keeps being replaced.
- Using `operator.add` on a key that should be overwritten (e.g. `status`) — it becomes `"pendingdone"`.
