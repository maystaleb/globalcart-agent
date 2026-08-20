"""Operations Resolver Agent — Quest #04, Part A (Single-Agent Resolver).

This module wires the GlobalCart mock_services tool box to an Anthropic
tool-use loop. The agent:

  1. Reads a freeform customer ticket.
  2. Decides for itself which of the four business tools to call, in which
     order, based on their docstrings/schemas — nothing here hardcodes a
     fixed call sequence.
  3. Once it has enough information, calls a fifth, *local* tool —
     ``submit_resolution`` — to hand back a validated structured answer
     instead of relying on the model to emit well-formed JSON as free text.
  4. Stops. It does not retry a failed lookup in a loop, and it never
     overrides what ``process_refund`` returned.

Why a "submit_resolution" tool instead of asking for raw JSON at the end?
---------------------------------------------------------------------------
Anthropic's tool-use mechanism already gives us schema-validated, parseable
arguments for free. Reusing it for the final answer means the three
required fields (`reasoning_chain`, `action_taken`, `customer_response`) are
guaranteed to be present and correctly typed, instead of hoping the model's
free-text JSON happens to parse. It also makes the "did the agent actually
finish its analysis" question unambiguous: the loop ends when — and only
when — the model calls this specific tool.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

# The business tool box lives in starter-kit/, untouched, one level up.
STARTER_KIT = Path(__file__).resolve().parent.parent / "starter-kit"
sys.path.insert(0, str(STARTER_KIT))

import mock_services as gc  # noqa: E402  (starter-kit tool box; not ours to edit)

MODEL = "claude-sonnet-4-6"
MAX_TOOL_TURNS = 8  # hard stop so a confused agent can't loop forever

SYSTEM_PROMPT = """\
You are the GlobalCart Operations Resolver Agent. You handle complex customer \
support tickets — shipping problems, damaged items, billing disputes — that a \
simple FAQ bot cannot resolve. You have four tools that read the real order, \
customer, and policy data, plus one tool to submit your final answer.

How to work a ticket:
1. Read the ticket. Identify the order id(s), the customer's stated problem, \
and their apparent sentiment (calm, frustrated, angry, anxious).
2. Call get_order_details to see what actually happened — never guess a \
delivery date, item condition, or amount.
3. Wait for the result of get_order_details before calling get_user_profile. \
Read the exact user_id field from that result and use it verbatim — never \
guess a user_id from the order id or any other pattern (e.g. do not assume \
order ORD-1011 belongs to user USR-1011). Guessing wastes a tool call when \
it's wrong, and is exactly the kind of fabrication this agent must avoid, \
even when the guess turns out harmless. Then call get_user_profile to learn \
the customer's tier, refund history, and fraud signals. Tier changes the \
refund cap and return window; you cannot know that from the ticket text.
4. Call check_return_policy to get the authoritative verdict and the policy \
ids behind it. Do not compute the return window or the refund cap yourself — \
the tool does that arithmetic for you, and its policy ids are what make your \
reasoning auditable.
5. Only if the claim is eligible, call process_refund to actually attempt \
the refund. Always request the FULL amount the customer paid or is owed \
(the order total, or the specific amount they asked for) — never a reduced \
amount chosen to slip under the automatic cap. If $52 is owed and the cap \
is $50, request $52. There are exactly three possible outcomes for a \
ticket, no others:
   - AUTO_REFUND_APPROVED: process_refund returned APPROVED for the full \
amount. Report the refund_id.
   - ESCALATED: process_refund returned ESCALATION_REQUIRED — you have NO \
authority to approve this, no matter how sympathetic the case or how close \
to the cap. This includes cases where the requested amount is only \
slightly above the cap. Never request a partial amount instead of the \
full amount just to avoid escalating — that is not one of your three \
allowed outcomes and defeats the guardrail. Tell the customer honestly \
that the full amount has been routed to a human operations lead. Do not \
say "your refund has been processed" or imply the money is on its way, \
and do not offer a partial refund on your own initiative.
   - REJECTED: check_return_policy (or process_refund) found the claim not \
eligible. State the reason plainly and cite the policy.
   You cannot talk this tool into a different answer, and you must never \
report a different outcome to the customer than what it actually returned.
6. If a tool returns a dict with an "error" key (ORDER_NOT_FOUND, \
USER_NOT_FOUND, INVALID_AMOUNT, INVALID_REASON), that is not a bug — it is \
the answer. Do not retry the same call hoping for a different result, and \
do not invent an order, a delivery date, or an amount to fill the gap. Tell \
the customer honestly that you could not find what they referenced, and \
route the case to a human if they insist the order exists.
7. When you have enough information to make a decision, call \
submit_resolution exactly once with your final structured answer. Do not \
call any other tool after that.

Decision rubric (the tools enforce this; you are reporting their verdict, \
not inventing your own):
- Eligible AND within the tier's automatic cap AND no escalation risk flags \
  -> call process_refund and report AUTO_REFUND_APPROVED.
- Eligible but process_refund returns ESCALATION_REQUIRED -> report \
  ESCALATED, with the reason (over cap, fraud risk, or repeat claims).
- Not eligible (outside window, non-returnable category, order not \
  refundable, or the order/user does not exist) -> report REJECTED (or \
  NOT_FOUND for a missing order/user), with the specific policy or reason.
- Always pass process_refund the full amount owed, even when it is only a \
  dollar or two over the cap. A request for less than the full amount, made \
  to dodge escalation, is not a valid decision path.

Your reasoning_chain must cite concrete data you actually retrieved — order \
ids, dates, amounts, tiers, and policy ids — not vague statements like \
"policy allows this." Your customer_response must be written directly to \
the customer, matched to their sentiment (extra empathy for a frustrated or \
angry customer, brevity for a routine case), and must never promise \
something the tools did not actually deliver.
"""

SUBMIT_RESOLUTION_TOOL = {
    "name": "submit_resolution",
    "description": (
        "Submit your final, structured resolution for this ticket. Call this "
        "exactly once, as the last thing you do, after you have gathered "
        "enough information with the other tools to make a decision."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "reasoning_chain": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Ordered list of short factual statements explaining the "
                    "decision. Each entry should cite a concrete value you "
                    "retrieved (an order id, date, amount, tier, or policy id) "
                    "— not a generic justification."
                ),
            },
            "action_taken": {
                "type": "object",
                "description": "What the agent actually did.",
                "properties": {
                    "tools_called": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Names of the business tools invoked, in order.",
                    },
                    "decision": {
                        "type": "string",
                        "enum": [
                            "AUTO_REFUND_APPROVED",
                            "ESCALATED",
                            "REJECTED",
                            "NOT_FOUND",
                        ],
                    },
                    "refund_amount": {
                        "type": "number",
                        "description": "Amount approved, if any. 0 if none was approved.",
                    },
                    "refund_id": {
                        "type": "string",
                        "description": "Refund id from process_refund, if a refund was approved.",
                    },
                    "policy_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Policy ids that drove the decision, e.g. POL-RET-01.",
                    },
                },
                "required": ["tools_called", "decision"],
            },
            "customer_response": {
                "type": "string",
                "description": (
                    "The drafted reply to send the customer, matched to their "
                    "tone and honest about the outcome."
                ),
            },
        },
        "required": ["reasoning_chain", "action_taken", "customer_response"],
    },
}


@dataclass
class ResolutionResult:
    """Final structured output of a single agent run."""

    reasoning_chain: List[str]
    action_taken: Dict[str, Any]
    customer_response: str
    tool_call_log: List[Dict[str, Any]] = field(default_factory=list)
    raw_transcript: List[Dict[str, Any]] = field(default_factory=list)
    stopped_reason: str = "submitted"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "reasoning_chain": self.reasoning_chain,
            "action_taken": self.action_taken,
            "customer_response": self.customer_response,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


class OperationsResolverAgent:
    """A single autonomous agent that resolves one GlobalCart support ticket."""

    def __init__(self, client: Optional[anthropic.Anthropic] = None, model: str = MODEL):
        self.client = client or anthropic.Anthropic()
        self.model = model
        # Business tools come straight from the starter kit, unmodified.
        self.tools = list(gc.TOOL_SCHEMAS) + [SUBMIT_RESOLUTION_TOOL]

    def _dispatch(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Run one business tool and return its raw result as a dict."""
        fn = gc.TOOL_REGISTRY[name]
        try:
            result = fn(**arguments)
        except TypeError as exc:
            # A genuine programmer/argument error, per the tool box's own
            # contract. Surface it to the model as data, not a crash.
            result = {"error": "TOOL_ARGUMENT_ERROR", "message": str(exc)}
        return result

    def resolve(self, ticket_text: str, verbose: bool = True) -> ResolutionResult:
        messages: List[Dict[str, Any]] = [
            {"role": "user", "content": f"New support ticket:\n\n{ticket_text}"}
        ]
        tool_call_log: List[Dict[str, Any]] = []

        for turn in range(MAX_TOOL_TURNS):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                tools=self.tools,
                messages=messages,
            )

            # Any tool_use block named submit_resolution ends the run.
            submit_block = next(
                (b for b in response.content if b.type == "tool_use" and b.name == "submit_resolution"),
                None,
            )
            if submit_block is not None:
                payload = submit_block.input
                if verbose:
                    print(f"  [turn {turn + 1}] submit_resolution -> {payload.get('action_taken', {}).get('decision')}")
                return ResolutionResult(
                    reasoning_chain=payload.get("reasoning_chain", []),
                    action_taken=payload.get("action_taken", {}),
                    customer_response=payload.get("customer_response", ""),
                    tool_call_log=tool_call_log,
                    raw_transcript=messages,
                )

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            if not tool_use_blocks:
                # Model responded with plain text and no tool call — nudge it
                # back toward finishing the job instead of looping silently.
                messages.append({"role": "assistant", "content": response.content})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Please continue using the tools, and call "
                            "submit_resolution once you have a decision."
                        ),
                    }
                )
                continue

            messages.append({"role": "assistant", "content": response.content})
            tool_results = []
            for block in tool_use_blocks:
                if verbose:
                    print(f"  [turn {turn + 1}] {block.name}({json.dumps(block.input)})")
                result = self._dispatch(block.name, block.input)
                tool_call_log.append({"tool": block.name, "input": block.input, "output": result})
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            messages.append({"role": "user", "content": tool_results})

        # Guardrail: the agent burned its turn budget without submitting.
        # Fail loudly and honestly rather than fabricating a resolution.
        return ResolutionResult(
            reasoning_chain=[
                f"Agent did not call submit_resolution within {MAX_TOOL_TURNS} turns.",
                "Stopping to avoid an infinite tool-calling loop.",
            ],
            action_taken={"tools_called": [c["tool"] for c in tool_call_log], "decision": "ESCALATED"},
            customer_response=(
                "Thanks for your patience — this case needs a closer look from our "
                "operations team, and I've routed it to them directly."
            ),
            tool_call_log=tool_call_log,
            raw_transcript=messages,
            stopped_reason="max_turns_exceeded",
        )
