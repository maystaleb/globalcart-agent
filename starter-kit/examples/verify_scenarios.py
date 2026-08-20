#!/usr/bin/env python3
"""Sanity check for the Quest #04 Part A tool box.

Run this before you write a single line of agent code. It asserts that the
fixtures and the rule engine agree with the scenarios described in
``scenarios.md``. If it passes, any wrong answer your agent gives later is your
agent's fault, not the data's — which is exactly the position you want to debug
from.

    cd "Quest 4 - Stage 1/starter-kit"
    python3 examples/verify_scenarios.py

Standard library only. No pip install required.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mock_services as gc  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(label: str, actual, expected) -> None:
    global PASSED
    if actual == expected:
        PASSED += 1
        print(f"  ok    {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label}\n          expected: {expected!r}\n          actual:   {actual!r}")


print(f"reference date: {gc.reference_date()}\n")

# --------------------------------------------------------------------------- #
print("Scenario 1 — happy path: VIP, damaged item, 35 USD -> auto approve")
o = gc.get_order_details("ORD-1001")
u = gc.get_user_profile(o["user_id"])
p = gc.check_return_policy("ORD-1001", "damaged_on_arrival")
r = gc.process_refund("ORD-1001", 35.0, "damaged_on_arrival")
check("order is delivered", o["status"], "delivered")
check("item arrived damaged", o["items"][0]["condition"], "damaged_on_arrival")
check("customer is VIP", u["tier"], "VIP")
check("claim is eligible", p["verdict"], "ELIGIBLE")
check("no escalation needed", p["requires_escalation"], False)
check("refund approved", r["status"], "APPROVED")
check("full amount approved", r["approved_amount"], 35.0)

# --------------------------------------------------------------------------- #
print("\nScenario 2 — authority breach: damaged item, 150 USD -> escalate")
p = gc.check_return_policy("ORD-1002", "damaged_on_arrival")
r = gc.process_refund("ORD-1002", 150.0, "damaged_on_arrival")
check("claim is eligible on its merits", p["eligible"], True)
check("automatic cap is 50 USD (Standard tier)", p["auto_refund_cap_usd"], 50.0)
check("refund NOT approved", r["status"], "ESCALATION_REQUIRED")
check("nothing was paid out", r["approved_amount"], 0.0)

# --------------------------------------------------------------------------- #
print("\nScenario 3 — window breach: return requested 60 days after delivery -> reject")
p = gc.check_return_policy("ORD-1003", "changed_mind")
r = gc.process_refund("ORD-1003", 42.5, "changed_mind")
check("60 days since delivery", p["days_since_delivery"], 60)
check("verdict is outside window", p["verdict"], "OUTSIDE_RETURN_WINDOW")
check("not eligible", p["eligible"], False)
check("POL-RET-01 is cited", "POL-RET-01" in p["applicable_policies"], True)
check("refund rejected", r["status"], "REJECTED")

# --------------------------------------------------------------------------- #
print("\nScenario 4 — non-returnable category: digital gift card -> reject")
p = gc.check_return_policy("ORD-1008", "changed_mind")
check("verdict is non-returnable", p["verdict"], "NON_RETURNABLE_CATEGORY")
check("POL-REF-03 is cited", "POL-REF-03" in p["applicable_policies"], True)

# --------------------------------------------------------------------------- #
print("\nScenario 5 — the boundary: 48 USD approves, 52 USD does not")
check("48 USD approved", gc.process_refund("ORD-1010", 48.0)["status"], "APPROVED")
check("52 USD escalated", gc.process_refund("ORD-1011", 52.0)["status"], "ESCALATION_REQUIRED")

# --------------------------------------------------------------------------- #
print("\nScenario 6 — risky customer: repeat claims and a fraud flag -> escalate")
u = gc.get_user_profile("USR-105")
p = gc.check_return_policy("ORD-1005", "damaged_on_arrival")
check("fraud score is high", u["initial_fraud_score"] >= 60, True)
check("three claims on record", len(u["refund_history"]), 3)
check("escalation flagged", p["requires_escalation"], True)
check("small refund still escalates", gc.process_refund("ORD-1005", 25.0)["status"], "ESCALATION_REQUIRED")

# --------------------------------------------------------------------------- #
print("\nScenario 7 — order has not shipped -> not refundable")
check("processing order rejected", gc.check_return_policy("ORD-1007")["verdict"], "ORDER_NOT_REFUNDABLE")
check("cancelled order rejected", gc.check_return_policy("ORD-1009")["verdict"], "ORDER_NOT_REFUNDABLE")

# --------------------------------------------------------------------------- #
print("\nScenario 8 — bad input returns structured errors, never a crash")
check("unknown order", gc.get_order_details("ORD-9999")["error"], "ORDER_NOT_FOUND")
check("unknown user", gc.get_user_profile("USR-999")["error"], "USER_NOT_FOUND")
check("negative amount", gc.process_refund("ORD-1001", -5.0)["error"], "INVALID_AMOUNT")
check("bad reason", gc.check_return_policy("ORD-1001", "because_i_said_so")["error"], "INVALID_REASON")
check("over order total", gc.process_refund("ORD-1001", 999.0)["status"], "REJECTED")

# --------------------------------------------------------------------------- #
print("\nTool schemas")
names = sorted(s["name"] for s in gc.TOOL_SCHEMAS)
check(
    "four tools are exposed",
    names,
    ["check_return_policy", "get_order_details", "get_user_profile", "process_refund"],
)
check("registry matches schemas", sorted(gc.TOOL_REGISTRY), names)

# --------------------------------------------------------------------------- #
print()
if FAILED:
    print(f"{PASSED} passed, {len(FAILED)} FAILED: {', '.join(FAILED)}")
    sys.exit(1)
print(f"All {PASSED} checks passed. The tool box is behaving as documented.")
