"""GlobalCart mock service layer — the tool box for Quest #04, Part A.

This module is the *only* thing your agent is allowed to call in order to learn
about the world. It reads the local JSON fixtures in ``data/`` and returns plain
dictionaries. There are no network calls, no servers to start and no credentials
to configure: import it and it works.

Design contract
---------------
1. **Deterministic.** Every function returns the same output for the same input.
   All date arithmetic is measured against ``policies.json -> reference_date``
   (override with the ``QUEST4_REFERENCE_DATE`` environment variable), never
   against the real wall clock.
2. **No exceptions for business problems.** A missing order is *data*, not a
   crash. Every function returns ``{"error": "<CODE>", "message": "..."}`` so
   that an agent can read the failure and recover. Only genuine programmer
   errors (wrong type) raise.
3. **Self-describing.** Each function carries type hints and a docstring written
   for a language model to read. ``TOOL_SCHEMAS`` at the bottom exposes the same
   information as JSON Schema, ready to hand to OpenAI / Anthropic / PydanticAI
   / CrewAI / LangGraph without rewriting anything.

Error codes
-----------
==========================  ==============================================
``ORDER_NOT_FOUND``         No order with that ``order_id``.
``USER_NOT_FOUND``          No user with that ``user_id``.
``INVALID_AMOUNT``          Amount is not a positive number.
``INVALID_REASON``          Reason is not in ``eligible_return_reasons``.
``ORDER_NOT_REFUNDABLE``    Order status is not ``delivered`` or ``shipped``.
==========================  ==============================================

Usage
-----
>>> import mock_services as gc
>>> gc.get_order_details("ORD-1001")["status"]
'delivered'
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = [
    "get_order_details",
    "get_user_profile",
    "check_return_policy",
    "process_refund",
    "list_order_ids",
    "get_policies",
    "reference_date",
    "TOOL_SCHEMAS",
]

DATA_DIR = Path(__file__).resolve().parent / "data"


# --------------------------------------------------------------------------- #
# Fixture loading
# --------------------------------------------------------------------------- #

@lru_cache(maxsize=None)
def _load(filename: str) -> Dict[str, Any]:
    """Load and cache a JSON fixture from ``data/``."""
    path = DATA_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"Missing fixture {path}. Run the tools from inside the starter kit "
            f"so that the data/ directory sits next to mock_services.py."
        )
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _orders() -> List[Dict[str, Any]]:
    return _load("orders.json")["orders"]


def _users() -> List[Dict[str, Any]]:
    return _load("users.json")["users"]


def get_policies() -> Dict[str, Any]:
    """Return the raw GlobalCart policy document.

    Useful when you want the agent to quote a policy id verbatim in its
    reasoning chain. Prefer :func:`check_return_policy` for actual decisions —
    it applies the rules for you instead of making the model do arithmetic.
    """
    return _load("policies.json")


def reference_date() -> date:
    """Return the 'today' that all date arithmetic is measured against.

    Reads ``policies.json -> reference_date``, overridable with the
    ``QUEST4_REFERENCE_DATE`` environment variable (``YYYY-MM-DD``). Keeping a
    fixed reference date is what makes these tools reproducible: a test that
    passes today still passes next month.
    """
    raw = os.environ.get("QUEST4_REFERENCE_DATE") or get_policies()["reference_date"]
    return datetime.strptime(raw, "%Y-%m-%d").date()


def _parse(value: Optional[str]) -> Optional[date]:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else None


def _error(code: str, message: str) -> Dict[str, Any]:
    return {"error": code, "message": message}


def _find_order(order_id: str) -> Optional[Dict[str, Any]]:
    return next((o for o in _orders() if o["order_id"] == order_id), None)


def _find_user(user_id: str) -> Optional[Dict[str, Any]]:
    return next((u for u in _users() if u["user_id"] == user_id), None)


def _limits_for(user: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Resolve the return window and refund cap that apply to a given user."""
    policies = get_policies()
    window = policies["return_window_days"]
    cap = policies["auto_refund_cap_usd"]
    applied = ["POL-RET-01", "POL-REF-01"]
    if user and user.get("tier") == "VIP":
        window = policies["vip_overrides"]["return_window_days"]
        cap = policies["vip_overrides"]["auto_refund_cap_usd"]
        applied = ["POL-RET-02", "POL-REF-02"]
    return {"return_window_days": window, "auto_refund_cap_usd": cap, "applied_policies": applied}


def _claims_in_window(user: Dict[str, Any]) -> int:
    """Count the user's refund claims inside the repeat-claim window."""
    policies = get_policies()
    window = policies["repeat_claim_threshold"]["window_days"]
    today = reference_date()
    count = 0
    for claim in user.get("refund_history", []):
        claim_date = _parse(claim.get("date"))
        if claim_date and (today - claim_date).days <= window:
            count += 1
    return count


# --------------------------------------------------------------------------- #
# Tool 1 — order lookup
# --------------------------------------------------------------------------- #

def get_order_details(order_id: str) -> Dict[str, Any]:
    """Look up a single GlobalCart order by its id.

    When to use this tool:
        Call this first, for any ticket that mentions an order. It is the only
        way to learn the order's shipping status, its delivery date, what was in
        the box and the condition each item arrived in. Never guess an amount or
        a delivery date — read it from here.

    Args:
        order_id: The order identifier exactly as the customer wrote it,
            e.g. ``"ORD-1001"``. Case sensitive.

    Returns:
        On success, a dict with keys:
            ``order_id``, ``user_id``, ``status``
            (one of ``delivered`` / ``shipped`` / ``processing`` / ``delayed`` /
            ``cancelled``), ``order_date``, ``delivery_date`` (``None`` if it has
            not arrived), ``total_amount``, ``currency``, ``channel``,
            ``items`` (each with ``sku``, ``name``, ``category``, ``qty``,
            ``unit_price`` and ``condition`` — ``new``, ``damaged_on_arrival``,
            ``wrong_item`` or ``missing``), ``shipping_address``,
            ``address_changed_at`` (a date string if the customer changed the
            address after ordering, otherwise ``None``) and
            ``payment_method_last4``.
        On failure, ``{"error": "ORDER_NOT_FOUND", "message": ...}``.

    Raises:
        TypeError: If ``order_id`` is not a string.
    """
    if not isinstance(order_id, str):
        raise TypeError("order_id must be a string")
    order = _find_order(order_id.strip())
    if order is None:
        return _error("ORDER_NOT_FOUND", f"No order found with id '{order_id}'.")
    return dict(order)


# --------------------------------------------------------------------------- #
# Tool 2 — customer lookup
# --------------------------------------------------------------------------- #

def get_user_profile(user_id: str) -> Dict[str, Any]:
    """Look up a GlobalCart customer profile by user id.

    When to use this tool:
        Call this after :func:`get_order_details` — the order tells you the
        ``user_id``. You need the profile to know the customer's tier (a VIP gets
        a longer return window and a higher refund cap), their refund history
        and their fraud score. Do not judge a customer's history from the ticket
        text alone; read it from here.

    Args:
        user_id: The customer identifier, e.g. ``"USR-101"``. Take it from the
            order returned by :func:`get_order_details`.

    Returns:
        On success, a dict with keys:
            ``user_id``, ``name``, ``email``, ``tier`` (``VIP`` or ``Standard``),
            ``account_created_at``, ``country``, ``lifetime_value``,
            ``orders_count``, ``refund_history`` (a list of past claims with
            ``order_id``, ``amount``, ``date`` and ``reason``),
            ``prior_fraud_flags``, ``initial_fraud_score`` (0-100, higher is
            riskier) and ``sentiment_history``.
        On failure, ``{"error": "USER_NOT_FOUND", "message": ...}``.

    Raises:
        TypeError: If ``user_id`` is not a string.
    """
    if not isinstance(user_id, str):
        raise TypeError("user_id must be a string")
    user = _find_user(user_id.strip())
    if user is None:
        return _error("USER_NOT_FOUND", f"No user found with id '{user_id}'.")
    return dict(user)


# --------------------------------------------------------------------------- #
# Tool 3 — policy evaluation
# --------------------------------------------------------------------------- #

def check_return_policy(order_id: str, reason: str = "damaged_on_arrival") -> Dict[str, Any]:
    """Decide whether an order is still eligible for a return or refund.

    When to use this tool:
        Call this before you promise a customer anything. It applies the
        GlobalCart rulebook — return window, VIP overrides, non-returnable
        categories, order status — and hands back a verdict plus the policy ids
        behind it. Do not do this arithmetic yourself: the tool is the source of
        truth and quoting its ``applicable_policies`` is what makes your
        reasoning chain auditable.

    Args:
        order_id: The order to evaluate, e.g. ``"ORD-1003"``.
        reason: Why the customer is asking. Must be one of
            ``damaged_on_arrival``, ``wrong_item``, ``item_missing``,
            ``late_delivery`` or ``changed_mind``. Defaults to
            ``"damaged_on_arrival"``.

    Returns:
        On success, a dict with keys:
            ``order_id``, ``user_id``, ``tier``, ``reason``, ``eligible`` (bool),
            ``verdict`` (``ELIGIBLE`` / ``OUTSIDE_RETURN_WINDOW`` /
            ``NON_RETURNABLE_CATEGORY`` / ``ORDER_NOT_REFUNDABLE``),
            ``return_window_days``, ``days_since_delivery``,
            ``days_remaining_in_window`` (negative when the window has closed),
            ``auto_refund_cap_usd``, ``max_refundable_amount``,
            ``requires_escalation`` (bool — true when the case must go to a
            human even if it is eligible), ``escalation_reasons`` (list),
            ``applicable_policies`` (list of policy ids) and ``explanation``
            (one sentence you can lift into your reasoning chain).
        On failure, ``{"error": ..., "message": ...}`` with code
        ``ORDER_NOT_FOUND`` or ``INVALID_REASON``.
    """
    if not isinstance(order_id, str):
        raise TypeError("order_id must be a string")

    policies = get_policies()
    if reason not in policies["eligible_return_reasons"]:
        return _error(
            "INVALID_REASON",
            f"'{reason}' is not a recognised return reason. Valid reasons: "
            f"{', '.join(policies['eligible_return_reasons'])}.",
        )

    order = _find_order(order_id.strip())
    if order is None:
        return _error("ORDER_NOT_FOUND", f"No order found with id '{order_id}'.")

    user = _find_user(order["user_id"])
    limits = _limits_for(user)
    applicable = list(limits["applied_policies"])
    today = reference_date()

    delivered = _parse(order.get("delivery_date"))
    days_since = (today - delivered).days if delivered else None
    remaining = (limits["return_window_days"] - days_since) if days_since is not None else None

    # --- hard blocks, evaluated in priority order ------------------------- #
    categories = {item["category"] for item in order["items"]}
    blocked = categories & set(policies["non_returnable_categories"])
    if blocked:
        applicable.append("POL-REF-03")
        return {
            "order_id": order["order_id"],
            "user_id": order["user_id"],
            "tier": user["tier"] if user else None,
            "reason": reason,
            "eligible": False,
            "verdict": "NON_RETURNABLE_CATEGORY",
            "return_window_days": limits["return_window_days"],
            "days_since_delivery": days_since,
            "days_remaining_in_window": remaining,
            "auto_refund_cap_usd": limits["auto_refund_cap_usd"],
            "max_refundable_amount": 0.0,
            "requires_escalation": False,
            "escalation_reasons": [],
            "applicable_policies": applicable,
            "explanation": (
                f"Order {order['order_id']} contains a non-returnable category "
                f"({', '.join(sorted(blocked))}); policy POL-REF-03 forbids a refund."
            ),
        }

    if order["status"] not in policies["refundable_order_statuses"]:
        applicable.append("POL-REF-04")
        return {
            "order_id": order["order_id"],
            "user_id": order["user_id"],
            "tier": user["tier"] if user else None,
            "reason": reason,
            "eligible": False,
            "verdict": "ORDER_NOT_REFUNDABLE",
            "return_window_days": limits["return_window_days"],
            "days_since_delivery": days_since,
            "days_remaining_in_window": remaining,
            "auto_refund_cap_usd": limits["auto_refund_cap_usd"],
            "max_refundable_amount": 0.0,
            "requires_escalation": False,
            "escalation_reasons": [],
            "applicable_policies": applicable,
            "explanation": (
                f"Order {order['order_id']} is '{order['status']}'. Policy POL-REF-04 "
                f"only allows refunds for delivered or shipped orders."
            ),
        }

    if days_since is not None and days_since > limits["return_window_days"]:
        return {
            "order_id": order["order_id"],
            "user_id": order["user_id"],
            "tier": user["tier"] if user else None,
            "reason": reason,
            "eligible": False,
            "verdict": "OUTSIDE_RETURN_WINDOW",
            "return_window_days": limits["return_window_days"],
            "days_since_delivery": days_since,
            "days_remaining_in_window": remaining,
            "auto_refund_cap_usd": limits["auto_refund_cap_usd"],
            "max_refundable_amount": 0.0,
            "requires_escalation": False,
            "escalation_reasons": [],
            "applicable_policies": applicable,
            "explanation": (
                f"The request is {days_since} days after delivery, past the "
                f"{limits['return_window_days']}-day window in "
                f"{applicable[0]}. The claim is rejected."
            ),
        }

    # --- eligible, but possibly escalation-bound -------------------------- #
    escalation_reasons: List[str] = []
    if user:
        if user.get("initial_fraud_score", 0) >= policies["high_fraud_score_threshold"]:
            escalation_reasons.append(
                f"fraud score {user['initial_fraud_score']} is at or above the "
                f"{policies['high_fraud_score_threshold']} threshold (POL-ESC-01)"
            )
            applicable.append("POL-ESC-01")
        if user.get("prior_fraud_flags", 0) > 0:
            escalation_reasons.append(
                f"customer carries {user['prior_fraud_flags']} prior fraud flag(s) (POL-ESC-01)"
            )
            if "POL-ESC-01" not in applicable:
                applicable.append("POL-ESC-01")
        claims = _claims_in_window(user)
        threshold = policies["repeat_claim_threshold"]
        if claims >= threshold["claims"]:
            escalation_reasons.append(
                f"{claims} refund claims in the last {threshold['window_days']} days (POL-ESC-02)"
            )
            applicable.append("POL-ESC-02")

    return {
        "order_id": order["order_id"],
        "user_id": order["user_id"],
        "tier": user["tier"] if user else None,
        "reason": reason,
        "eligible": True,
        "verdict": "ELIGIBLE",
        "return_window_days": limits["return_window_days"],
        "days_since_delivery": days_since,
        "days_remaining_in_window": remaining,
        "auto_refund_cap_usd": limits["auto_refund_cap_usd"],
        "max_refundable_amount": min(order["total_amount"], limits["auto_refund_cap_usd"]),
        "requires_escalation": bool(escalation_reasons),
        "escalation_reasons": escalation_reasons,
        "applicable_policies": applicable,
        "explanation": (
            f"Order {order['order_id']} is within the {limits['return_window_days']}-day "
            f"window ({days_since} days since delivery). Automatic refund authority is "
            f"{limits['auto_refund_cap_usd']:.2f} USD."
            + (f" Escalation is required: {'; '.join(escalation_reasons)}." if escalation_reasons else "")
        ),
    }


# --------------------------------------------------------------------------- #
# Tool 4 — the action
# --------------------------------------------------------------------------- #

def process_refund(order_id: str, amount: float, reason: str = "damaged_on_arrival") -> Dict[str, Any]:
    """Attempt to issue a refund. This is the only tool that changes anything.

    When to use this tool:
        Call this last, and only after :func:`check_return_policy` said the
        claim is eligible. It re-checks the rulebook itself, so you cannot talk
        it into exceeding your authority: a request above the automatic cap
        comes back as ``ESCALATION_REQUIRED``, not ``APPROVED``. Treat that
        response as the instruction to hand the case to a human, and say so in
        your customer reply.

    Args:
        order_id: The order to refund, e.g. ``"ORD-1001"``.
        amount: The amount to refund in USD. Must be a positive number and may
            not exceed the order total.
        reason: Why the refund is being issued. Must be one of
            ``damaged_on_arrival``, ``wrong_item``, ``item_missing``,
            ``late_delivery`` or ``changed_mind``.

    Returns:
        A dict with keys:
            ``status`` — ``APPROVED``, ``REJECTED`` or ``ESCALATION_REQUIRED``;
            ``order_id``, ``user_id``, ``requested_amount``,
            ``approved_amount`` (``0.0`` unless approved),
            ``auto_refund_cap_usd``, ``refund_id`` (a deterministic id, present
            only when approved), ``applicable_policies``, ``reasons`` (list of
            human-readable strings) and ``message`` (one sentence).
        Or ``{"error": ..., "message": ...}`` with code ``ORDER_NOT_FOUND``,
        ``INVALID_AMOUNT`` or ``INVALID_REASON``.

    Note:
        Nothing is written to disk. The refund is simulated, but the
        authority limit is enforced mechanically rather than by prompt.
    """
    if not isinstance(order_id, str):
        raise TypeError("order_id must be a string")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return _error("INVALID_AMOUNT", "amount must be a positive number.")
    if amount <= 0:
        return _error("INVALID_AMOUNT", f"amount must be greater than 0, got {amount}.")

    policies = get_policies()
    if reason not in policies["eligible_return_reasons"]:
        return _error(
            "INVALID_REASON",
            f"'{reason}' is not a recognised return reason. Valid reasons: "
            f"{', '.join(policies['eligible_return_reasons'])}.",
        )

    order = _find_order(order_id.strip())
    if order is None:
        return _error("ORDER_NOT_FOUND", f"No order found with id '{order_id}'.")

    check = check_return_policy(order["order_id"], reason)
    user = _find_user(order["user_id"])
    limits = _limits_for(user)
    cap = limits["auto_refund_cap_usd"]

    base = {
        "order_id": order["order_id"],
        "user_id": order["user_id"],
        "requested_amount": round(float(amount), 2),
        "approved_amount": 0.0,
        "auto_refund_cap_usd": cap,
        "applicable_policies": check.get("applicable_policies", []),
    }

    if amount > order["total_amount"]:
        return {
            **base,
            "status": "REJECTED",
            "reasons": [
                f"requested {amount:.2f} USD exceeds the order total of "
                f"{order['total_amount']:.2f} USD"
            ],
            "message": "Refund rejected: you cannot refund more than the customer paid.",
        }

    if not check.get("eligible"):
        return {
            **base,
            "status": "REJECTED",
            "reasons": [check.get("verdict", "NOT_ELIGIBLE"), check.get("explanation", "")],
            "message": f"Refund rejected: {check.get('explanation', 'the claim is not eligible.')}",
        }

    if amount > cap:
        return {
            **base,
            "status": "ESCALATION_REQUIRED",
            "reasons": [
                f"requested {amount:.2f} USD is above the automatic refund cap of "
                f"{cap:.2f} USD (POL-REF-01/POL-REF-02)"
            ],
            "message": (
                f"Refund not issued. {amount:.2f} USD exceeds your automatic authority of "
                f"{cap:.2f} USD — escalate to a human operations lead."
            ),
        }

    if check.get("requires_escalation"):
        return {
            **base,
            "status": "ESCALATION_REQUIRED",
            "reasons": check.get("escalation_reasons", []),
            "message": (
                "Refund not issued. The amount is within your authority, but the "
                "customer's risk profile requires human review."
            ),
        }

    return {
        **base,
        "status": "APPROVED",
        "approved_amount": round(float(amount), 2),
        "refund_id": f"RF-{order['order_id'].split('-')[-1]}-{int(round(float(amount) * 100))}",
        "reasons": [
            f"within the {cap:.2f} USD automatic refund authority",
            f"claim is eligible ({check.get('verdict')})",
        ],
        "message": (
            f"Refund of {float(amount):.2f} USD approved for order {order['order_id']}."
        ),
    }


# --------------------------------------------------------------------------- #
# Convenience helpers (not part of the required tool set)
# --------------------------------------------------------------------------- #

def list_order_ids() -> List[str]:
    """Return every order id in the fixture. Handy while exploring; your agent
    does not need it."""
    return [o["order_id"] for o in _orders()]


# --------------------------------------------------------------------------- #
# Tool schemas — hand these straight to your framework
# --------------------------------------------------------------------------- #

_REASON_ENUM = [
    "damaged_on_arrival",
    "wrong_item",
    "item_missing",
    "late_delivery",
    "changed_mind",
]

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "name": "get_order_details",
        "description": (
            "Look up a GlobalCart order by id. Returns shipping status, order and "
            "delivery dates, total amount, the items in the box and the condition "
            "each arrived in, the shipping address and whether that address was "
            "changed after the order was placed. Call this first for any ticket "
            "that mentions an order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order identifier, e.g. 'ORD-1001'.",
                }
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "get_user_profile",
        "description": (
            "Look up a GlobalCart customer by user id. Returns their tier "
            "(VIP customers get a longer return window and a higher refund cap), "
            "account age, lifetime value, refund history, prior fraud flags and "
            "fraud score. Take the user_id from the order."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "Customer identifier, e.g. 'USR-101'.",
                }
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "check_return_policy",
        "description": (
            "Evaluate whether an order is still eligible for a return or refund. "
            "Applies the return window, VIP overrides, non-returnable categories "
            "and order-status rules, and returns the verdict together with the "
            "policy ids behind it and whether the case must be escalated to a "
            "human. Call this before promising the customer anything."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order identifier, e.g. 'ORD-1003'.",
                },
                "reason": {
                    "type": "string",
                    "enum": _REASON_ENUM,
                    "description": "Why the customer is asking.",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "process_refund",
        "description": (
            "Issue a refund for an order. This is the only tool with a side "
            "effect. It re-validates the rulebook, so a request above the "
            "automatic refund cap returns ESCALATION_REQUIRED instead of "
            "APPROVED. Call it only after check_return_policy reported the "
            "claim eligible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "Order identifier, e.g. 'ORD-1001'.",
                },
                "amount": {
                    "type": "number",
                    "description": "Refund amount in USD. Positive, and not more than the order total.",
                },
                "reason": {
                    "type": "string",
                    "enum": _REASON_ENUM,
                    "description": "Why the refund is being issued.",
                },
            },
            "required": ["order_id", "amount"],
        },
    },
]

#: Maps a tool name to the callable, so a framework-agnostic dispatcher can do
#: ``TOOL_REGISTRY[name](**arguments)``.
TOOL_REGISTRY = {
    "get_order_details": get_order_details,
    "get_user_profile": get_user_profile,
    "check_return_policy": check_return_policy,
    "process_refund": process_refund,
}


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    import pprint

    print(f"reference date: {reference_date()}")
    print(f"orders: {', '.join(list_order_ids())}\n")
    pprint.pprint(check_return_policy("ORD-1001"))
    print()
    pprint.pprint(process_refund("ORD-1001", 35.0))
