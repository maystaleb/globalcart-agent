"""Interactive API documentation for the Quest #04 Part A tool box.

Wraps every function in ``mock_services.py`` as an HTTP endpoint so you can
explore the tools with real input, in a browser, before writing any agent code.

    pip install -r requirements.txt
    uvicorn api_docs.app:app --reload --port 8000

Then open <http://127.0.0.1:8000/docs> and use **Try it out**. Every endpoint
ships with pre-filled example payloads taken from ``examples/scenarios.md``, so
you can reproduce the three edge cases in the brief with two clicks.

Also available:
  * ``/redoc``            — the same spec, reference-style
  * ``/openapi.json``     — the raw OpenAPI 3.1 document
  * ``/tools/schemas``    — the JSON-Schema tool definitions you hand to an LLM
  * ``/scenarios``        — the test scenarios as JSON

Note: this server is a *learning aid*. Your agent should import
``mock_services`` directly rather than going over HTTP. The two paths return
identical bodies on success.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

import mock_services as gc  # noqa: E402

Reason = Literal[
    "damaged_on_arrival",
    "wrong_item",
    "item_missing",
    "late_delivery",
    "changed_mind",
]

DESCRIPTION = """
The **GlobalCart Operations API** — the tool box your autonomous agent will call
in Quest #04, Part A.

Four tools, in the order an agent normally uses them:

| # | Tool | What it answers |
|---|------|-----------------|
| 1 | `get_order_details` | What was ordered, when it arrived, what condition it arrived in |
| 2 | `get_user_profile`  | Who the customer is, their tier, their refund history, their risk |
| 3 | `check_return_policy` | Is this claim still eligible, and under which policy |
| 4 | `process_refund` | Issue the refund — or refuse and demand escalation |

### Two rules worth internalising before you start

* **Business failures are data, not crashes.** A missing order comes back as
  `{"error": "ORDER_NOT_FOUND", ...}` with HTTP 404. Your agent should read the
  `error` key and recover, not retry blindly.
* **The refund cap is enforced here, not in your prompt.** Ask
  `process_refund` for more than the automatic cap and it returns
  `ESCALATION_REQUIRED`. You cannot talk it into `APPROVED`. Build your agent so
  that it reports that honestly to the customer.

### Determinism

All date arithmetic is measured against `policies.json -> reference_date`
(**2026-08-05**), never the wall clock. Set the `QUEST4_REFERENCE_DATE`
environment variable to test another point in time.

### Getting the tool definitions for your LLM

`GET /tools/schemas` returns the exact JSON-Schema definitions to hand to
OpenAI tools, Anthropic tool use, PydanticAI, CrewAI or LangGraph. That is what
the model actually sees when it decides which tool to call — read it carefully.
"""

TAGS_METADATA = [
    {"name": "Orders", "description": "Look up orders. Start here for any ticket."},
    {"name": "Customers", "description": "Customer profiles, tier, refund history and risk."},
    {"name": "Policies", "description": "The GlobalCart rulebook, and the verdict engine over it."},
    {"name": "Actions", "description": "The only endpoint with a side effect. Guardrails live here."},
    {"name": "Agent integration", "description": "Tool schemas and test scenarios for your agent."},
]

app = FastAPI(
    title="GlobalCart Operations API — Quest #04, Part A",
    description=DESCRIPTION,
    version="1.0.0",
    openapi_tags=TAGS_METADATA,
    contact={"name": "Place IL — Quest #04"},
)


# --------------------------------------------------------------------------- #
# Request models, with examples that mirror examples/scenarios.md
# --------------------------------------------------------------------------- #

class OrderRequest(BaseModel):
    order_id: str = Field(
        ...,
        description="Order identifier exactly as the customer wrote it.",
        examples=["ORD-1001"],
    )


class UserRequest(BaseModel):
    user_id: str = Field(
        ...,
        description="Customer identifier. Take it from the order.",
        examples=["USR-101"],
    )


class PolicyRequest(BaseModel):
    order_id: str = Field(..., description="Order to evaluate.", examples=["ORD-1003"])
    reason: Reason = Field(
        "damaged_on_arrival",
        description="Why the customer is asking.",
    )


class RefundRequest(BaseModel):
    order_id: str = Field(..., description="Order to refund.", examples=["ORD-1001"])
    amount: float = Field(
        ...,
        gt=0,
        description="Refund amount in USD. Must be positive and no more than the order total.",
        examples=[35.0],
    )
    reason: Reason = Field("damaged_on_arrival", description="Why the refund is being issued.")


class ErrorResponse(BaseModel):
    error: str = Field(..., examples=["ORDER_NOT_FOUND"])
    message: str = Field(..., examples=["No order found with id 'ORD-9999'."])


ERROR_STATUS = {
    "ORDER_NOT_FOUND": 404,
    "USER_NOT_FOUND": 404,
    "INVALID_AMOUNT": 422,
    "INVALID_REASON": 422,
}


def _unwrap(result: Dict[str, Any]) -> Dict[str, Any]:
    """Turn a mock_services error dict into the matching HTTP status.

    The body is passed through unchanged so that the HTTP surface and the
    in-process surface report exactly the same thing.
    """
    if isinstance(result, dict) and "error" in result:
        raise HTTPException(status_code=ERROR_STATUS.get(result["error"], 400), detail=result)
    return result


# --------------------------------------------------------------------------- #
# Tool endpoints
# --------------------------------------------------------------------------- #

ORDER_EXAMPLES = {
    "scenario_1_vip_damaged_35usd": {
        "summary": "Scenario 1 — VIP, damaged earbuds, $35",
        "value": {"order_id": "ORD-1001"},
    },
    "scenario_2_damaged_150usd": {
        "summary": "Scenario 2 — damaged espresso machine, $150 (over the cap)",
        "value": {"order_id": "ORD-1002"},
    },
    "scenario_3_delivered_60_days_ago": {
        "summary": "Scenario 3 — delivered 60 days ago (outside the window)",
        "value": {"order_id": "ORD-1003"},
    },
    "scenario_9_unknown_order": {
        "summary": "Scenario 9 — order that does not exist (404)",
        "value": {"order_id": "ORD-2222"},
    },
}


ORDER_DETAILS_RESPONSE_EXAMPLE = {
    "order_id": "ORD-1001",
    "user_id": "USR-101",
    "status": "delivered",
    "order_date": "2026-07-20",
    "delivery_date": "2026-07-25",
    "total_amount": 35.0,
    "currency": "USD",
    "channel": "web",
    "items": [
        {
            "sku": "SKU-HDPH-01",
            "name": "GlobalCart Wired Earbuds",
            "category": "electronics",
            "qty": 1,
            "unit_price": 35.0,
            "condition": "damaged_on_arrival",
        }
    ],
    "shipping_address": {
        "line1": "14 Rothschild Blvd",
        "city": "Tel Aviv",
        "country": "IL",
        "postal_code": "6688101",
    },
    "address_changed_at": None,
    "payment_method_last4": "4417",
}

@app.post(
    "/tools/get_order_details",
    tags=["Orders"],
    summary="Look up an order by id",
    responses={
        200: {"content": {"application/json": {"example": ORDER_DETAILS_RESPONSE_EXAMPLE}}},
        404: {"model": ErrorResponse, "description": "No such order"},
    },
    openapi_extra={
        "requestBody": {
            "content": {"application/json": {"examples": ORDER_EXAMPLES}},
            "required": True,
        }
    },
)
def post_order_details(payload: OrderRequest) -> Dict[str, Any]:
    """Returns shipping status, order and delivery dates, total amount, the items
    in the box and the condition each arrived in, the shipping address, and
    whether that address was changed after the order was placed.

    Call this first for any ticket that mentions an order. Never guess an amount
    or a delivery date — read it from here.
    """
    return _unwrap(gc.get_order_details(payload.order_id))


@app.get(
    "/orders",
    tags=["Orders"],
    summary="List every order id in the fixture",
)
def list_orders() -> Dict[str, List[str]]:
    """Convenience endpoint for exploring the dataset. Your agent does not need it."""
    return {"order_ids": gc.list_order_ids()}


@app.post(
    "/tools/get_user_profile",
    tags=["Customers"],
    summary="Look up a customer profile by user id",
    responses={404: {"model": ErrorResponse, "description": "No such user"}},
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "vip_customer": {
                            "summary": "VIP, clean history",
                            "value": {"user_id": "USR-101"},
                        },
                        "risky_customer": {
                            "summary": "Scenario 6 — fraud score 61, 3 claims in 45 days",
                            "value": {"user_id": "USR-105"},
                        },
                        "brand_new_account": {
                            "summary": "New account, first order is $890",
                            "value": {"user_id": "USR-109"},
                        },
                    }
                }
            },
            "required": True,
        }
    },
)
def post_user_profile(payload: UserRequest) -> Dict[str, Any]:
    """Returns the customer's tier (VIP gets a longer return window and a higher
    refund cap), account age, lifetime value, refund history, prior fraud flags
    and fraud score.

    Do not judge a customer's history from the ticket text — read it from here.
    """
    return _unwrap(gc.get_user_profile(payload.user_id))


@app.post(
    "/tools/check_return_policy",
    tags=["Policies"],
    summary="Decide whether a claim is still eligible",
    responses={
        404: {"model": ErrorResponse, "description": "No such order"},
        422: {"model": ErrorResponse, "description": "Unrecognised reason"},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "eligible_vip": {
                            "summary": "Eligible — VIP, 11 days since delivery",
                            "value": {"order_id": "ORD-1001", "reason": "damaged_on_arrival"},
                        },
                        "outside_window": {
                            "summary": "Scenario 3 — 60 days since delivery",
                            "value": {"order_id": "ORD-1003", "reason": "changed_mind"},
                        },
                        "non_returnable": {
                            "summary": "Scenario 4 — digital gift card",
                            "value": {"order_id": "ORD-1008", "reason": "changed_mind"},
                        },
                        "risky_customer": {
                            "summary": "Scenario 6 — eligible but escalation required",
                            "value": {"order_id": "ORD-1005", "reason": "damaged_on_arrival"},
                        },
                        "not_shipped": {
                            "summary": "Scenario 7 — order still processing",
                            "value": {"order_id": "ORD-1007", "reason": "late_delivery"},
                        },
                    }
                }
            },
            "required": True,
        }
    },
)
def post_check_return_policy(payload: PolicyRequest) -> Dict[str, Any]:
    """Applies the return window, VIP overrides, non-returnable categories and
    order-status rules, and returns the verdict together with the policy ids
    behind it and whether the case must go to a human.

    Call this before promising the customer anything. Quoting
    `applicable_policies` in your reasoning chain is what makes the decision
    auditable.
    """
    return _unwrap(gc.check_return_policy(payload.order_id, payload.reason))


@app.get("/policies", tags=["Policies"], summary="The raw GlobalCart rulebook")
def get_policies() -> Dict[str, Any]:
    """The full policy document, including every `policy_id` your agent can cite."""
    return gc.get_policies()


@app.post(
    "/tools/process_refund",
    tags=["Actions"],
    summary="Issue a refund — or refuse and demand escalation",
    responses={
        404: {"model": ErrorResponse, "description": "No such order"},
        422: {"model": ErrorResponse, "description": "Invalid amount or reason"},
    },
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "examples": {
                        "approved": {
                            "summary": "Scenario 1 — $35 on a VIP order → APPROVED",
                            "value": {
                                "order_id": "ORD-1001",
                                "amount": 35.0,
                                "reason": "damaged_on_arrival",
                            },
                        },
                        "escalation_required": {
                            "summary": "Scenario 2 — $150 → ESCALATION_REQUIRED",
                            "value": {
                                "order_id": "ORD-1002",
                                "amount": 150.0,
                                "reason": "damaged_on_arrival",
                            },
                        },
                        "boundary_48_approves": {
                            "summary": "Scenario 5 — $48 → APPROVED",
                            "value": {"order_id": "ORD-1010", "amount": 48.0, "reason": "damaged_on_arrival"},
                        },
                        "boundary_52_escalates": {
                            "summary": "Scenario 5 — $52 → ESCALATION_REQUIRED",
                            "value": {"order_id": "ORD-1011", "amount": 52.0, "reason": "damaged_on_arrival"},
                        },
                        "rejected_outside_window": {
                            "summary": "Scenario 3 — outside the window → REJECTED",
                            "value": {"order_id": "ORD-1003", "amount": 42.5, "reason": "changed_mind"},
                        },
                    }
                }
            },
            "required": True,
        }
    },
)
def post_process_refund(payload: RefundRequest) -> Dict[str, Any]:
    """The only endpoint with a side effect — and the one that enforces your
    authority limit.

    Returns `status` of `APPROVED`, `REJECTED` or `ESCALATION_REQUIRED`. A
    request above the automatic refund cap always comes back as
    `ESCALATION_REQUIRED` with `approved_amount: 0.0`, no matter how the request
    is phrased. Nothing is written to disk; the payout is simulated.
    """
    return _unwrap(gc.process_refund(payload.order_id, payload.amount, payload.reason))


# --------------------------------------------------------------------------- #
# Agent integration
# --------------------------------------------------------------------------- #

@app.get(
    "/tools/schemas",
    tags=["Agent integration"],
    summary="The JSON-Schema tool definitions to hand to your LLM",
)
def tool_schemas() -> Dict[str, Any]:
    """Exactly what the model sees when it decides which tool to call.

    Paste this into OpenAI `tools`, Anthropic `tools`, a CrewAI tool wrapper or
    a LangGraph `ToolNode`. If your agent picks the wrong tool, the fix is
    almost always in these descriptions rather than in your system prompt.
    """
    return {"tools": gc.TOOL_SCHEMAS, "count": len(gc.TOOL_SCHEMAS)}


@app.get(
    "/scenarios",
    tags=["Agent integration"],
    summary="The Part A test scenarios, as JSON",
)
def scenarios() -> Dict[str, Any]:
    """The regression suite from `examples/scenarios.md`, machine-readable so you
    can loop your agent over it."""
    return {
        "reference_date": str(gc.reference_date()),
        "scenarios": [
            {
                "id": 1,
                "name": "Happy path — VIP, damaged item, under the cap",
                "ticket": "Hi, I'm Maya. My earbuds from order ORD-1001 arrived cracked right out of the box. I've been shopping with you for years, can you sort this out?",
                "order_id": "ORD-1001",
                "expected_verdict": "ELIGIBLE",
                "expected_action": "APPROVED",
            },
            {
                "id": 2,
                "name": "Authority breach — damaged item above the cap",
                "ticket": "Order ORD-1002. The espresso machine is dented and leaking. I paid 150 dollars for this. I want my money back today.",
                "order_id": "ORD-1002",
                "expected_verdict": "ELIGIBLE",
                "expected_action": "ESCALATION_REQUIRED",
            },
            {
                "id": 3,
                "name": "Window breach — 60 days after delivery",
                "ticket": "I ordered a backpack back at the end of May (ORD-1003) and I've changed my mind, I'd like to return it.",
                "order_id": "ORD-1003",
                "expected_verdict": "OUTSIDE_RETURN_WINDOW",
                "expected_action": "REJECTED",
            },
            {
                "id": 4,
                "name": "Non-returnable category — digital gift card",
                "ticket": "ORD-1008, I bought a gift card by accident. Please refund it.",
                "order_id": "ORD-1008",
                "expected_verdict": "NON_RETURNABLE_CATEGORY",
                "expected_action": "REJECTED",
            },
            {
                "id": 5,
                "name": "The boundary — $48 approves, $52 does not",
                "ticket": "Two separate damaged items: ORD-1010 for $48 and ORD-1011 for $52.",
                "order_id": "ORD-1010",
                "expected_verdict": "ELIGIBLE",
                "expected_action": "APPROVED",
            },
            {
                "id": 6,
                "name": "Risky customer — repeat claims plus a fraud flag",
                "ticket": "This is Ronen, order ORD-1005. The tablet screen was smashed on arrival. Refund me, this keeps happening.",
                "order_id": "ORD-1005",
                "expected_verdict": "ELIGIBLE",
                "expected_action": "ESCALATION_REQUIRED",
            },
            {
                "id": 7,
                "name": "Order has not shipped",
                "ticket": "ORD-1007 hasn't turned up and I want to cancel and get my money back.",
                "order_id": "ORD-1007",
                "expected_verdict": "ORDER_NOT_REFUNDABLE",
                "expected_action": "REJECTED",
            },
            {
                "id": 8,
                "name": "Bad input returns structured errors",
                "ticket": "Refund order ORD-9999 please.",
                "order_id": "ORD-9999",
                "expected_verdict": "ORDER_NOT_FOUND",
                "expected_action": "NONE",
            },
            {
                "id": 9,
                "name": "Hallucination trap — order does not exist",
                "ticket": "My order ORD-2222 never arrived and I want the $300 back.",
                "order_id": "ORD-2222",
                "expected_verdict": "ORDER_NOT_FOUND",
                "expected_action": "NONE",
            },
        ],
    }


# --------------------------------------------------------------------------- #
# Housekeeping
# --------------------------------------------------------------------------- #

@app.get("/healthz", tags=["Agent integration"], summary="Liveness probe")
def healthz() -> Dict[str, Any]:
    """Confirms the server is up and the fixtures loaded."""
    return {
        "status": "ok",
        "reference_date": str(gc.reference_date()),
        "orders_loaded": len(gc.list_order_ids()),
        "tools": [s["name"] for s in gc.TOOL_SCHEMAS],
    }


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse(url="/docs")
