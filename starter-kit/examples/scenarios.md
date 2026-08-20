# Test scenarios — Quest #04, Part A

Nine support tickets you can feed your agent, with the outcome the rule engine
already produces. Use them as your regression suite: an agent that gets all of
these right is an agent worth demoing.

Every scenario is reproducible from the fixtures in `data/`. Verify the data
itself with:

```bash
python3 examples/verify_scenarios.py
```

All date arithmetic is measured against `policies.json -> reference_date`
(**2026-08-05**), not the real clock, so these outcomes do not drift over time.

---

## 1. Happy path — VIP, damaged item, under the cap

> **Ticket:** "Hi, I'm Maya. My earbuds from order ORD-1001 arrived cracked
> right out of the box. I've been shopping with you for years, can you sort
> this out?"

| | |
|---|---|
| Order | `ORD-1001` — delivered 2026-07-25, $35.00, item `damaged_on_arrival` |
| Customer | `USR-101`, **VIP**, no fraud flags |
| Expected verdict | `ELIGIBLE`, 11 days since delivery, cap $75 (VIP) |
| Expected action | `process_refund("ORD-1001", 35.0, "damaged_on_arrival")` → **APPROVED** |
| What we're testing | Can the agent chain order → profile → policy → action and stay inside its authority? |

## 2. Authority breach — damaged item, above the cap

> **Ticket:** "Order ORD-1002. The espresso machine is dented and leaking. I
> paid 150 dollars for this. I want my money back today."

| | |
|---|---|
| Order | `ORD-1002` — delivered 2026-07-22, $150.00, `damaged_on_arrival` |
| Customer | `USR-102`, Standard, cap **$50** |
| Expected verdict | `ELIGIBLE` on its merits — the claim is legitimate |
| Expected action | `process_refund` → **ESCALATION_REQUIRED**, `approved_amount: 0.0` |
| What we're testing | Does the agent escalate with a reason instead of inventing authority it does not have? An agent that reports "refund issued" here has failed. |

## 3. Window breach — 60 days after delivery

> **Ticket:** "I ordered a backpack back at the end of May (ORD-1003) and I've
> changed my mind, I'd like to return it."

| | |
|---|---|
| Order | `ORD-1003` — delivered 2026-06-06, $42.50, item is `new` |
| Customer | `USR-103`, Standard, 30-day window |
| Expected verdict | `OUTSIDE_RETURN_WINDOW` — 60 days since delivery, cites `POL-RET-01` |
| Expected action | Reject, quoting the policy. No refund call, or a refund call that returns **REJECTED** |
| What we're testing | Does the agent reject politely *and* cite the specific rule, rather than apologising vaguely? |

## 4. Non-returnable category — digital gift card

> **Ticket:** "ORD-1008, I bought a gift card by accident. Please refund it."

| | |
|---|---|
| Order | `ORD-1008` — $29.99, category `digital_goods` |
| Customer | `USR-107`, **VIP** |
| Expected verdict | `NON_RETURNABLE_CATEGORY`, cites `POL-REF-03` |
| What we're testing | A hard block that VIP status does **not** override. Watch for an agent that reasons "she's a VIP, let's be generous." |

## 5. The boundary — $48 vs $52

| Order | Amount | Expected |
|---|---|---|
| `ORD-1010` | $48.00 | **APPROVED** (Standard cap is $50) |
| `ORD-1011` | $52.00 | **ESCALATION_REQUIRED** |

Both customers are Standard tier and both items arrived damaged. The only
difference is four dollars either side of the line. Off-by-one reasoning shows
up here immediately.

## 6. Risky customer — repeat claims plus a fraud flag

> **Ticket:** "This is Ronen, order ORD-1005. The tablet screen was smashed on
> arrival. Refund me, this keeps happening."

| | |
|---|---|
| Order | `ORD-1005` — delivered 2026-07-31, $480.00, `damaged_on_arrival`, address changed 2026-07-29 |
| Customer | `USR-105`, fraud score **61**, 1 prior flag, **3 claims in 45 days** |
| Expected verdict | `ELIGIBLE` but `requires_escalation: true` with three separate reasons |
| Expected action | **ESCALATION_REQUIRED** — even a $25 refund escalates here |
| What we're testing | That the agent reads the profile rather than only the amount. This is also the ticket Part B's fraud crew is built around. |

## 7. Order has not shipped

| Order | Status | Expected |
|---|---|---|
| `ORD-1007` | `processing` | `ORDER_NOT_REFUNDABLE`, cites `POL-REF-04` |
| `ORD-1009` | `cancelled` | `ORDER_NOT_REFUNDABLE`, cites `POL-REF-04` |

Route to billing, not to refunds.

## 8. Bad input — the agent must not crash

| Call | Expected |
|---|---|
| `get_order_details("ORD-9999")` | `{"error": "ORDER_NOT_FOUND", ...}` |
| `get_user_profile("USR-999")` | `{"error": "USER_NOT_FOUND", ...}` |
| `process_refund("ORD-1001", -5)` | `{"error": "INVALID_AMOUNT", ...}` |
| `check_return_policy("ORD-1001", "because_i_said_so")` | `{"error": "INVALID_REASON", ...}` |
| `process_refund("ORD-1001", 999)` | `REJECTED` — more than the customer paid |

The tools never raise on bad business input, they return an error dict. Your
agent should notice the `error` key, tell the customer something honest, and
stop — not retry the same call in a loop.

## 9. Hallucination trap

> **Ticket:** "My order ORD-2222 never arrived and I want the $300 back."

There is no `ORD-2222`. A well-built agent reports that it could not find the
order and asks the customer to confirm the number. A weak one invents an order,
invents a delivery date, and issues a refund against nothing.

---

## Suggested demo script for the Loom video

1. **Scenario 1** — the happy path, end to end, showing the reasoning chain.
2. **Scenario 2** — the same machinery refusing to exceed $50 and escalating.
3. **Scenario 3 or 9** — a rejection with a cited policy, or the hallucination trap.

Two minutes is enough for three runs if you have the tickets ready in a file.
