"""Shared state for the investigation graph.

The graph moves through four Layer 3 phases -- context collection -> hypothesis
generation -> investigation -> ranking -- optionally followed by a Layer 5 remediate
phase (propose -> approve -> execute) when build_graph is given remediation_tools.
`diagnosis` is Layer 3's terminal field; `remediation` is Layer 5's, and is left
unset entirely when remediation isn't enabled for a run.

Everything here is plain, JSON-serializable data (TypedDicts, not custom classes) so a
full run's state can be persisted and replayed later, per the "record and replay"
design decision in the README.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class Hypothesis(TypedDict):
    statement: str
    affected_service: str | None
    confidence: float
    supporting_evidence: list[str]
    contradicting_evidence: list[str]


class Diagnosis(TypedDict):
    root_cause: str
    affected_service: str
    triggering_change: str | None
    confidence: float
    evidence_summary: str
    hypotheses_considered: list[str]


class RemediationOutcome(TypedDict, total=False):
    """What (if anything) the Layer 5 remediate phase did after diagnosis. `status`
    mirrors the remediation server's own terminal states: none_proposed (the agent
    judged no safe action applied), denied (failed a policy check), pending_approval
    (approved by policy but no human decision arrived -- shouldn't be terminal in
    practice since execute_remediation blocks for a decision, but included for
    completeness), approved/denied_by_human/executed/timed_out/execution_failed."""

    proposed: bool
    tool: str | None
    target: str | None
    justification: str | None
    status: str | None
    detail: str | None


class AgentState(TypedDict, total=False):
    # Inputs, set once at graph invocation -- this is deliberately the *only* thing the
    # agent is told about the incident. It never sees scenario_id, fault_config, or
    # ground_truth; an alert name/condition string is what a real on-call engineer gets
    # paged with, and that's the full extent of this agent's privileged information.
    alert_name: str
    alert_condition: str
    run_id: str | None

    # Populated by context_collection: a fixed, cheap bundle of situational awareness
    # (recent deployments, recent errors, available metrics, available runbooks) so the
    # agent doesn't spend its first LLM turn rediscovering things every run needs.
    context: dict

    # The ReAct investigation transcript: alternating AI/tool messages. This is the
    # part of state most worth persisting for "record and replay" -- it's the full
    # audit trail of what the agent looked at and why.
    messages: Annotated[list[AnyMessage], add_messages]
    tool_call_count: int

    # Populated by hypothesize, refined by rank.
    hypotheses: list[Hypothesis]

    # Final output of Layer 3 -- what Layer 4 scores against ground_truth.
    diagnosis: Diagnosis | None

    # Layer 5 only: populated when build_graph is given remediation_tools. Mirrors
    # tool_call_count/messages' role but for the separate remediate <-> remediation_tools
    # loop, so investigation's own budget/messages are untouched by remediation.
    remediation_tool_call_count: int
    remediation: RemediationOutcome | None
