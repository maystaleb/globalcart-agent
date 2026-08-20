# GlobalCart Operations Resolver Agent — Quest #04, Part A

A single autonomous agent that reads a freeform GlobalCart support ticket,
investigates it using real tool calls, and returns a structured, auditable
resolution: an automatic refund, a reasoned rejection, or an honest
escalation to a human.

```
quest4/
├── agent/
│   ├── resolver_agent.py   the tool-use loop, system prompt, submit_resolution schema
│   ├── cli.py               run one ticket or the full scenario suite
│   └── __init__.py
├── starter-kit/              provided tool box — untouched (see note below)
│   ├── mock_services.py
│   ├── data/{orders,users,policies}.json
│   ├── examples/{verify_scenarios.py, scenarios.md}
│   └── api_docs/app.py
├── requirements.txt          agent dependencies (anthropic, python-dotenv)
├── .env.example
└── README.md                 this file

## 1. Architecture and why this framework

**Framework: the Anthropic Python SDK's native tool use**, no additional
agent framework on top.

Why: `mock_services.TOOL_SCHEMAS` is already emitted in Anthropic's exact
`input_schema` shape (the starter kit's own README shows this explicitly), so
there is nothing to translate or re-describe — the tool box's docstrings
become the tool descriptions the model sees, verbatim. That matters because
the brief is explicit that *"if the agent picks the wrong tool, the fix is in
the tool descriptions, not the system prompt"* — going through a heavier
framework (LangGraph, CrewAI) would insert an abstraction layer between me
and those descriptions, making that debugging loop slower for no benefit at
this scale (one agent, four read/write tools, no multi-agent coordination
yet — that's Part B).

**The loop** (`agent/resolver_agent.py: OperationsResolverAgent.resolve`):

1. The ticket text is sent to Claude with the four business tools *and* a
   fifth, local-only tool called `submit_resolution` (see §2).
2. Whatever tool calls the model makes are dispatched to
   `mock_services.TOOL_REGISTRY` and the raw JSON result is fed straight
   back — the agent layer never edits, filters, or reinterprets what a tool
   returned.
3. The loop ends the instant the model calls `submit_resolution`. There is
   also a hard cap (`MAX_TOOL_TURNS = 8`): if the model never submits, the
   agent stops itself and reports an honest escalation rather than looping
   forever or fabricating an answer. This is the concrete implementation of
   the brief's "an agent needs a stop condition" requirement.
4. If a tool call comes back with an `"error"` key, that string is handed to
   the model like any other tool result — the model sees it, and the system
   prompt explicitly instructs it not to retry the same call and not to
   invent data to fill the gap.

**Separation of agent and tool layers:** `agent/resolver_agent.py` never
imports from `starter-kit/data/` directly and contains zero business rules
of its own (no refund caps, no window arithmetic, no policy ids hardcoded
anywhere). Every number the agent reports came out of a `mock_services` call
in that run. The only thing the agent layer owns is *when* to call which
tool and how to phrase the customer-facing reply.

---

## 2. Reasoning chain and structured output

The brief asks for three fields: `reasoning_chain`, `action_taken`,
`customer_response`. Rather than asking the model to emit raw JSON as its
final text message (and hoping it parses), the final answer is *itself* a
tool call — `submit_resolution` — with a JSON-Schema `input_schema` that
requires all three fields with the right types. This means:

- The output is guaranteed parseable; there is no "strip markdown fences and
  hope" step.
- The loop's termination condition is unambiguous: it ends when — and only
  when — this specific tool is called, not when the text "looks done."
- `reasoning_chain` is a list of short factual statements, and the system
  prompt instructs the model to cite the concrete order ids, dates, amounts,
  tiers, and **policy ids** it actually retrieved (e.g. `POL-RET-01`,
  `POL-REF-02`) rather than generic justifications — this is what makes a
  reviewer able to audit the decision without re-running the tools.

Run `python -m agent.cli --ticket "..."` and the final stdout is exactly the
three-field JSON object from the brief (`to_json()` on the result strips out
the internal tool-call log and raw transcript, which are kept on the result
object for debugging but aren't part of the graded output).

---

## 3. Edge cases and guardrails

The three minimum scenarios from the brief, plus the ones explicitly called
out as worth checking, are all in `starter-kit/examples/scenarios.md` (the
official file from the Place-il) and runnable with `python -m agent.cli
--scenarios` via the `SCENARIOS` list in `agent/cli.py`:

| # | Scenario | Order | Expected `decision` |
|---|---|---|---|
| 1 | VIP, damaged item, $35 | `ORD-1001` | `AUTO_REFUND_APPROVED` |
| 2 | Damaged item, $150 (over Standard cap) | `ORD-1002` | `ESCALATED` |
| 3 | Return requested 60 days after delivery | `ORD-1003` | `REJECTED` |
| 4 | Non-returnable category (gift card) | `ORD-1008` | `REJECTED` |
| 5a | Exactly at the cap | `ORD-1010`, $48 | `AUTO_REFUND_APPROVED` |
| 5b | $2 over the cap | `ORD-1011`, $52 | `ESCALATED` |
| 6 | Fraud flag + high fraud score + 3 claims in 60 days | `ORD-1005` | `ESCALATED` |
| 7a | Order still `processing`, hasn't shipped | `ORD-1007` | `REJECTED` (`POL-REF-04`) |
| 7b | Order `cancelled` before it ever shipped | `ORD-1009` | `REJECTED` (`POL-REF-04`) |
| 9 | **The hallucination trap** — order does not exist | `ORD-2222` | `NOT_FOUND`, no invented data |



**All ten pass against the live model**, run against the official
`scenarios.md`, transcript in `docs/scenario_run.md`. One minor, non-fabricating
observation from that run: on 7a/7b the agent's `policy_ids` include
`POL-RET-01` and `POL-REF-01` alongside the actually-decisive `POL-REF-04` —
those two policies are real but weren't the reason for the rejection here,
so it's a bit of over-citation rather than an error.

### Two bugs found by actually running it, and how they were fixed

Running the scenarios against the real model — not just the offline
`_dispatch` wiring check — surfaced two problems that no unit test against
`mock_services` could have caught, because both are about *model behavior*,
not tool correctness.

**Bug 1 — the model invented a fourth decision path.** On the $52-over-cap
ticket (`ORD-1011`, scenario 5b), the first version of the system prompt
only said *"process_refund's response is authoritative."* The model took
that literally but crept around the guardrail: it called `process_refund`
with **$50** (the cap) instead of the $52 actually owed, got `APPROVED`, and
reported `AUTO_REFUND_APPROVED` for a partial amount. That is a reasonable
customer-service instinct, but it is not one of the three outcomes the brief
defines (auto-approve the full amount / reject / escalate), and it defeats
the entire point of scenario 5b, which exists specifically to prove the cap
can't be talked around. Fixed by making the system prompt state explicitly
that `process_refund` must always be called with the *full* amount owed,
and that a partial request to dodge escalation is not a valid outcome. After
the fix, the same ticket correctly escalates the full $52.

**Bug 2 — the model guessed a `user_id` instead of reading it.** In several
scenarios (e.g. `ORD-1011`, `ORD-1007`), the trace showed the model firing
`get_order_details` and `get_user_profile` in the *same* turn, guessing the
user id from the order id (`ORD-1011` → `USR-1011`) before it had actually
seen the order record. The guess was wrong, `get_user_profile` returned
`USER_NOT_FOUND`, and the model recovered on the next turn with the real id
from the order — so the *final* output was never wrong, but it burned an
extra tool call on data it should never have guessed in the first place, and
it's exactly the kind of fabrication-under-pressure the brief warns about
even when it happens to be harmless. Fixed by adding an explicit instruction
to wait for `get_order_details` to return before calling `get_user_profile`,
and to copy the `user_id` field verbatim rather than inferring it. After the
fix, every scenario calls `get_user_profile` exactly once, with the correct
id, on the turn immediately after the order lookup.

Guardrails, and where they live:

- **The refund cap is enforced in `mock_services.process_refund`, not in the
  prompt.** No system-prompt wording makes it return `APPROVED` above the
  cap — it mechanically returns `ESCALATION_REQUIRED`. The agent's job is
  only to detect that and never claim otherwise to the customer; the system
  prompt says this explicitly ("you cannot talk this tool into a different
  answer").
- **Missing data is data, not a crash.** `ORDER_NOT_FOUND` /
  `USER_NOT_FOUND` come back as ordinary dicts with an `"error"` key. The
  agent is instructed to treat that as the final answer for that lookup —
  report it honestly, don't retry, don't invent a delivery date or amount to
  paper over the gap.
- **Programmer-level errors don't crash the loop either.** If a tool call
  somehow gets a wrong argument type, `mock_services` raises `TypeError` by
  design; `OperationsResolverAgent._dispatch` catches that and turns it into
  a structured `TOOL_ARGUMENT_ERROR` result so a single malformed call can't
  take down the whole run.
- **The turn budget is the loop's stop condition.** `MAX_TOOL_TURNS = 8`
  caps how many rounds of tool calls a single ticket can take. If that's
  exhausted without a `submit_resolution` call, the agent reports an
  `ESCALATED` outcome with `stopped_reason="max_turns_exceeded"` instead of
  spinning indefinitely or hallucinating a resolution just to produce output.

---

## 4. How to run it

```bash
# from the quest4/ directory
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set ANTHROPIC_API_KEY

# sanity-check the data/rule engine first (should print "All 33 checks passed.")
python3 starter-kit/examples/verify_scenarios.py

# then run the agent
python -m agent.cli --ticket "Hi, order ORD-1001 arrived with a cracked case, can I get a refund?"
python -m agent.cli --scenarios              # all 10 tickets
python -m agent.cli --scenario-index 9       # just the hallucination trap
```

Each run prints a live trace of every tool call and argument (unless
`--quiet` is passed) followed by the final three-field JSON object.

---

## 5. Live run confirmation

All ten scenarios were run against the real Anthropic API (not just the
offline wiring checks) and produced the expected `decision` in every case,
after the two fixes described in §3 above. The full transcript — every tool
call, argument, and final JSON — is saved in `docs/scenario_run.md` for
reference.

## 6. Known limitations

- No demo video is included (optional per the brief).
- The model is not perfectly deterministic — wording in `customer_response`
  and the exact phrasing of `reasoning_chain` entries can vary slightly
  between runs, even though the `decision` and `policy_ids` have been
  consistent across repeated runs of all ten scenarios.