# GlobalCart Starter Kit — Quest #04, Part A

The tool box for the **Operations Resolver Agent**. Everything your agent needs
to see the world is here: three JSON fixtures, four Python tools, a scenario
suite, and interactive API documentation you can click through in a browser.

> **The tools and the data are not yours to change.** Your agent is what gets
> graded. If you think a fixture is wrong, say so in your `README.md` rather
> than editing it — a submission whose tools were edited cannot be compared
> against anyone else's.

---

## What's in the box

```
starter-kit/
├── mock_services.py          the four tools + TOOL_SCHEMAS + TOOL_REGISTRY
├── requirements.txt          only needed for the API docs server
├── data/
│   ├── orders.json           14 orders, every edge case in the brief
│   ├── users.json            9 customers: VIP/Standard, refund history, risk
│   └── policies.json         the rulebook: window, caps, escalation triggers
├── examples/
│   ├── scenarios.md          9 test tickets with expected outcomes
│   └── verify_scenarios.py   33 assertions — run this first
└── api_docs/
    └── app.py                FastAPI + Swagger UI over the same four tools
```

---

## Quickstart

**1. Confirm the tool box works (no install needed):**

```bash
cd "Quest 4 - Stage 1/starter-kit"
python3 examples/verify_scenarios.py
```

You should see `All 33 checks passed.` If you do, the data and the rule engine
are sound, and any wrong answer your agent gives later is your agent's.

**2. Explore the tools with real input in a browser:**

```bash
pip install -r requirements.txt
uvicorn api_docs.app:app --reload --port 8000
```

Open **<http://127.0.0.1:8000/docs>** and hit **Try it out**. Every endpoint has
pre-filled example payloads for the scenarios in the brief — you can reproduce
the $35 approval, the $150 escalation and the 60-day rejection in a few clicks.
Also there: `/redoc`, `/openapi.json`, and `/tools/schemas`.

**3. Use the tools from your agent:**

```python
import mock_services as gc

order = gc.get_order_details("ORD-1001")
user  = gc.get_user_profile(order["user_id"])
check = gc.check_return_policy("ORD-1001", "damaged_on_arrival")
if check["eligible"] and not check["requires_escalation"]:
    result = gc.process_refund("ORD-1001", order["total_amount"], "damaged_on_arrival")
```

Your agent should import `mock_services` directly. The HTTP server is a learning
aid, not a dependency.

---

## The four tools

| Tool | Signature | Returns |
|---|---|---|
| `get_order_details` | `(order_id: str)` | Status, dates, total, items and their condition, address, `address_changed_at` |
| `get_user_profile` | `(user_id: str)` | Tier, account age, LTV, `refund_history`, `prior_fraud_flags`, `initial_fraud_score` |
| `check_return_policy` | `(order_id: str, reason: str = "damaged_on_arrival")` | `verdict`, `eligible`, `requires_escalation`, `applicable_policies`, `explanation` |
| `process_refund` | `(order_id: str, amount: float, reason: str = "damaged_on_arrival")` | `status`: `APPROVED` / `REJECTED` / `ESCALATION_REQUIRED` |

Two support helpers, which your agent does not need: `get_policies()` returns the
raw rulebook, `list_order_ids()` lists the fixture.

### The rules the tools enforce for you

| Rule | Standard | VIP |
|---|---|---|
| Return window | 30 days from delivery | 45 days |
| Automatic refund cap | $50.00 | $75.00 |

Plus: digital goods, perishables and gift cards are never returnable
(`POL-REF-03`); only `delivered` and `shipped` orders are refundable
(`POL-REF-04`); a fraud score of 60+, any prior fraud flag, or three claims in
60 days forces escalation (`POL-ESC-01`, `POL-ESC-02`).

---

## Two design decisions worth understanding

### Business failures are data, not exceptions

A missing order does not raise. It returns:

```python
>>> gc.get_order_details("ORD-9999")
{'error': 'ORDER_NOT_FOUND', 'message': "No order found with id 'ORD-9999'."}
```

| Code | Meaning |
|---|---|
| `ORDER_NOT_FOUND` | No order with that id |
| `USER_NOT_FOUND` | No user with that id |
| `INVALID_AMOUNT` | Amount is not a positive number |
| `INVALID_REASON` | Reason is not in `eligible_return_reasons` |

Your agent should notice the `error` key, tell the customer something honest,
and stop — not retry the same call in a loop. Only genuine programmer errors
(passing an `int` where a `str` belongs) raise `TypeError`.

### The refund cap is enforced in code, not in your prompt

```python
>>> gc.process_refund("ORD-1002", 150.0)["status"]
'ESCALATION_REQUIRED'
```

You cannot talk `process_refund` into `APPROVED`. That is deliberate: a
guardrail that lives in a system prompt is a suggestion, a guardrail that lives
in the tool is a guarantee. Your job is to detect that response and report it
honestly to the customer — an agent that says "your refund has been processed"
after receiving `ESCALATION_REQUIRED` has failed the exercise.

### Determinism

All date arithmetic is measured against `policies.json -> reference_date`
(**2026-08-05**), never the wall clock, so your tests do not start failing next
month. Override it if you want to test another point in time:

```bash
QUEST4_REFERENCE_DATE=2026-09-01 python3 examples/verify_scenarios.py
```

---

## Framework integration

`TOOL_SCHEMAS` is a list of JSON-Schema tool definitions, and `TOOL_REGISTRY`
maps each name to the callable. Between them you can wire any framework without
rewriting a docstring.

**OpenAI tools**

```python
import json, mock_services as gc
from openai import OpenAI

tools = [{"type": "function",
          "function": {"name": s["name"],
                       "description": s["description"],
                       "parameters": s["input_schema"]}}
         for s in gc.TOOL_SCHEMAS]

client = OpenAI()
response = client.chat.completions.create(model="...", messages=messages, tools=tools)
for call in response.choices[0].message.tool_calls or []:
    result = gc.TOOL_REGISTRY[call.function.name](**json.loads(call.function.arguments))
```

**Anthropic tool use** — `TOOL_SCHEMAS` is already in the right shape:

```python
import anthropic, mock_services as gc
client = anthropic.Anthropic()
message = client.messages.create(model="...", max_tokens=2048,
                                 tools=gc.TOOL_SCHEMAS, messages=messages)
```

**PydanticAI / CrewAI / LangChain** — register the plain functions; the type
hints and docstrings are already written for a model to read:

```python
from langchain_core.tools import tool
import mock_services as gc
tools = [tool(fn) for fn in gc.TOOL_REGISTRY.values()]
```

> If your agent keeps picking the wrong tool, the fix is almost always in the
> tool descriptions rather than in your system prompt. Read `/tools/schemas` and
> ask yourself what the model actually sees.

---

## Before you submit

Run your agent against all nine scenarios in `examples/scenarios.md`. The three
in the brief are the minimum:

| Scenario | Order | Expected |
|---|---|---|
| VIP, damaged item, $35 | `ORD-1001` | refund **APPROVED** |
| Damaged item, $150 | `ORD-1002` | **ESCALATION_REQUIRED**, nothing paid |
| Return requested 60 days after delivery | `ORD-1003` | **REJECTED**, citing `POL-RET-01` |

Then check the hallucination trap: ticket 9 references `ORD-2222`, which does not
exist. A good agent says so. A weak one invents an order and refunds it.

Questions about the brief go to the Place IL Quest channel.
