"""Command-line entry point for the Operations Resolver Agent.

Usage:
    python -m agent.cli --ticket "My order ORD-1001 arrived damaged..."
    python -m agent.cli --scenarios          # run the regression suite below
    python -m agent.cli --scenario-index 3   # run just scenario #3 (1-indexed)

Note on scenario source: the official starter-kit/examples/scenarios.md
mixes two formats — some scenarios are freeform customer tickets in a
blockquote (1, 2, 3, 4, 6, 9), while others (5, 7) are given only as a
table of order ids / amounts with no natural-language ticket, and scenario
8 is not a customer ticket at all — it's direct bad-input tests against the
tools (get_order_details("ORD-9999"), etc.), already fully covered by
starter-kit/examples/verify_scenarios.py at the tool level with zero LLM
involvement. So scenario 8 has nothing to hand the agent and is excluded
from this list; the boundary (5) and not-shipped (7) tickets below are
freeform tickets *constructed* from the table each one describes, so the
agent still has to extract the order id and reason from realistic text.
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv

from .resolver_agent import OperationsResolverAgent

# (label, ticket_text) — matches starter-kit/examples/scenarios.md exactly for
# 1, 2, 3, 4, 6, 9 (copied verbatim from the official blockquotes); 5a/5b and
# 7a/7b are freeform tickets built from that file's boundary/not-shipped
# tables, since those two scenarios don't include quoted ticket text.
SCENARIOS: list[tuple[str, str]] = [
    (
        "1. Happy path — VIP, damaged item, under the cap (ORD-1001)",
        "Hi, I'm Maya. My earbuds from order ORD-1001 arrived cracked right "
        "out of the box. I've been shopping with you for years, can you "
        "sort this out?",
    ),
    (
        "2. Authority breach — damaged item, above the cap (ORD-1002)",
        "Order ORD-1002. The espresso machine is dented and leaking. I "
        "paid 150 dollars for this. I want my money back today.",
    ),
    (
        "3. Window breach — 60 days after delivery (ORD-1003)",
        "I ordered a backpack back at the end of May (ORD-1003) and I've "
        "changed my mind, I'd like to return it.",
    ),
    (
        "4. Non-returnable category — digital gift card (ORD-1008)",
        "ORD-1008, I bought a gift card by accident. Please refund it.",
    ),
    (
        "5a. The boundary — at the cap, should approve (ORD-1010, $48)",
        "Order ORD-1010, my item arrived damaged on arrival, I'd like a "
        "refund for the full $48.00 I paid.",
    ),
    (
        "5b. The boundary — over the cap, should escalate (ORD-1011, $52)",
        "Order ORD-1011, my item arrived damaged on arrival, I'd like a "
        "refund for the full $52.00 I paid.",
    ),
    (
        "6. Risky customer — repeat claims plus a fraud flag (ORD-1005)",
        "This is Ronen, order ORD-1005. The tablet screen was smashed on "
        "arrival. Refund me, this keeps happening.",
    ),
    (
        "7a. Order has not shipped — status processing (ORD-1007)",
        "Order ORD-1007 arrived damaged on arrival and I'd like a refund, "
        "please process it.",
    ),
    (
        "7b. Order has not shipped — status cancelled (ORD-1009)",
        "Order ORD-1009 arrived damaged on arrival and I'd like a refund, "
        "please process it.",
    ),
    (
        "9. Hallucination trap — order does not exist",
        "My order ORD-2222 never arrived and I want the $300 back.",
    ),
]


def _print_result(index_label: str, ticket: str, result) -> None:
    print(f"\n{'=' * 78}\n{index_label}\n{'-' * 78}")
    print(f"Ticket: {ticket}\n")
    print(result.to_json())
    if result.stopped_reason != "submitted":
        print(f"\n  ** stopped_reason: {result.stopped_reason} **")


def main() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.",
            file=sys.stderr,
        )
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Run the Operations Resolver Agent.")
    parser.add_argument("--ticket", type=str, help="A single freeform ticket to resolve.")
    parser.add_argument("--scenarios", action="store_true", help="Run all scenarios.")
    parser.add_argument("--scenario-index", type=int, help="Run one scenario by number (1-based).")
    parser.add_argument("--quiet", action="store_true", help="Suppress the tool-call trace.")
    args = parser.parse_args()

    agent = OperationsResolverAgent()

    if args.ticket:
        result = agent.resolve(args.ticket, verbose=not args.quiet)
        _print_result("Single ticket", args.ticket, result)
        return

    if args.scenario_index:
        label, ticket = SCENARIOS[args.scenario_index - 1]
        result = agent.resolve(ticket, verbose=not args.quiet)
        _print_result(label, ticket, result)
        return

    if args.scenarios:
        for i, (label, ticket) in enumerate(SCENARIOS, start=1):
            result = agent.resolve(ticket, verbose=not args.quiet)
            _print_result(f"Scenario {i}/{len(SCENARIOS)} — {label}", ticket, result)
        return

    parser.print_help()


if __name__ == "__main__":
    main()