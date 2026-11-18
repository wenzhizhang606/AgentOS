"""Small grounded-QA comparison of single-node and externally composed workflows.

The callback provisions a fresh project scope for each task. The two branches
are independent; dispatch is sequential so the benchmark also runs with one
worker. The application assembles the handoff outside AgentOS.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
import re
from time import monotonic


@dataclass(frozen=True)
class Source:
    source_id: str
    title: str
    text: str
    location: str = "section-1"


@dataclass(frozen=True)
class Branch:
    source: Source
    question: str
    expected_value: str


@dataclass(frozen=True)
class Case:
    case_id: str
    left: Branch
    right: Branch
    question: str
    expected_value: str


CASES = (
    Case(
        "refund",
        Branch(
            Source("orchid-order", "Orchid refund order record",
                   "Orchid order AL29 paid 48 credits. Orchid order BL29 paid 44 credits."),
            "For the Orchid refund, how many credits did Orchid order AL29 pay?",
            "48",
        ),
        Branch(
            Source("orchid-policy", "Orchid refund policy",
                   "An Orchid express order refund deducts 9 credits from the amount paid. "
                   "An Orchid standard order refund deducts 6 credits."),
            "For the Orchid refund, how many credits are deducted for an express order?",
            "9",
        ),
        "For an Orchid refund on express order AL29, subtract the policy deduction from "
        "the credits paid. What is the refund amount in credits?",
        "39",
    ),
    Case(
        "schedule",
        Branch(
            Source("quartz-dock", "Quartz dispatch dock calendar",
                   "The Quartz north dock accepts dispatches on July 3, 5, and 8. "
                   "The Quartz south dock accepts dispatches on July 4 and 6."),
            "For Quartz dispatch, list the days of July accepted by the north dock. "
            "Return the days in ascending order joined with hyphens.",
            "3-5-8",
        ),
        Branch(
            Source("quartz-carrier", "Quartz dispatch carrier calendar",
                   "Quartz regular carrier can dispatch on July 2, 5, and 8. "
                   "Quartz emergency carrier can dispatch on July 3 and 7."),
            "For Quartz dispatch, list the days of July available to the regular carrier. "
            "Return the days in ascending order joined with hyphens.",
            "2-5-8",
        ),
        "For Quartz dispatch from the north dock with the regular carrier, what is the "
        "earliest July day accepted by both? Answer with the day number.",
        "5",
    ),
    Case(
        "capacity",
        Branch(
            Source("maple-stock", "Maple allocation stock report",
                   "Maple West has 38 items available for allocation. Maple East has 43 "
                   "items. Expected deliveries have not yet been added to stock."),
            "For Maple allocation, how many items are available now at Maple West?",
            "38",
        ),
        Branch(
            Source("maple-allocation", "Maple allocation rules",
                   "For Maple West, allocations require 40 available items in total. "
                   "For Maple East, allocations require 35 available items. If the minimum "
                   "is met the status is READY; otherwise the status is WAIT."),
            "For Maple allocation, what is the required number of items at Maple West?",
            "40",
        ),
        "For Maple allocation, what is Maple West's status under its location-specific rules? "
        "Answer READY or WAIT.",
        "WAIT",
    ),
    Case(
        "warranty",
        Branch(
            Source("cedar-service", "Cedar warranty service record",
                   "Cedar service request H70 is for a pump purchased 13 months ago. "
                   "Cedar service request A70 is for a filter purchased 3 months ago."),
            "For the Cedar warranty, which product and purchase age in months apply to "
            "service request H70? Use PRODUCT-MONTHS, e.g. VALVE-10.",
            "PUMP-13",
        ),
        Branch(
            Source("cedar-warranty", "Cedar warranty coverage table",
                   "Cedar warranty covers pumps up to 18 months and filters up to 12 months "
                   "from purchase. The status for products within their own coverage "
                   "period is COVERED; otherwise the status is EXPIRED."),
            "For Cedar warranty, what is the coverage period in months for pumps?",
            "18",
        ),
        "For Cedar warranty service request H70, is its product still covered? "
        "Answer COVERED or EXPIRED using the product-specific coverage period.",
        "COVERED",
    ),
)


_VALUE = re.compile(r"^\s*VALUE\s*=\s*([a-zA-Z0-9-]+)(?=\s|\[|\.(?:\s|$)|$)", re.IGNORECASE)
_FORMAT = " Start your answer with VALUE=<value>, followed by the evidence citation labels."


def _observe(label: str, question: str, sources: tuple[Source, ...],
             expected_evidence: tuple[Source, ...], expected_value: str,
             run_task: Callable[[str, str, list[dict]], dict]) -> dict:
    started = monotonic()
    task = run_task(label, question + _FORMAT, [asdict(source) for source in sources])
    result = task.get("result") or {}
    answer = result.get("answer") or ""
    match = _VALUE.match(answer)
    value = match.group(1).upper() if match else None
    citations = result.get("citations") or []
    cited = {(citation["source_id"], citation["location"]) for citation in citations}
    expected_cites = {(source.source_id, source.location) for source in expected_evidence}
    visible_cites = {(source.source_id, source.location) for source in sources}
    grounded = expected_cites <= cited <= visible_cites
    task_fields = ("task_id", "attempt", "snapshot_id", "dispatch_attempt_id", "state",
                   "error_code", "result_error", "accounting_state", "limit_micros",
                   "settled_micros", "reserved_micros", "budget_overrun", "usage")
    return {
        **{key: task.get(key) for key in task_fields},
        "label": label,
        "elapsed_seconds": round(task.get("elapsed_seconds", monotonic() - started), 3),
        "answer": answer,
        "value": value,
        "expected_value": expected_value,
        "expected_sources": [source.source_id for source in expected_evidence],
        "citations": citations,
        "grounded": grounded,
        "passed": task.get("state") == "succeeded" and value == expected_value and grounded,
    }


def _charges(observations: list[dict], field: str) -> int | None:
    values = [observation[field] for observation in observations]
    if any(value is None for value in values):
        return None
    return sum(int(value) for value in values)


def _summarize(observations: list[dict]) -> dict:
    return {
        "completed_task_reports": len(observations),
        "dispatches": sum(bool(observation["dispatch_attempt_id"]) for observation in observations),
        "pending": sum(observation["accounting_state"] == "pending" for observation in observations),
        "budget_overruns": sum(observation["budget_overrun"] is True for observation in observations),
        "limit_micros_sum": _charges(observations, "limit_micros"),
        "settled_micros_sum": _charges(observations, "settled_micros"),
        "reserved_micros_sum": _charges(observations, "reserved_micros"),
        "observed_elapsed_seconds_sum": round(sum(o["elapsed_seconds"] for o in observations), 3),
    }


def run_collaboration(run_task: Callable[[str, str, list[dict]], dict]) -> dict:
    """Run the fixed fixture and retain the result, trace IDs, and ledger per node."""
    rows = []
    single_nodes = []
    team_nodes = []
    for case in CASES:
        label = f"collab-{case.case_id}"
        originals = (case.left.source, case.right.source)
        root_question = case.question + " Cite both original records that support your answer."
        single = _observe(f"{label}-single", root_question, originals, originals,
                          case.expected_value, run_task)
        single_nodes.append(single)
        team = []
        for role, branch in (("left", case.left), ("right", case.right)):
            scope = (branch.source,)
            team.append(_observe(f"{label}-{role}", branch.question, scope, scope,
                                 branch.expected_value, run_task))
        # Only accept an evidence-bound, syntactically valid handoff. Gold answers
        # are for scoring only and cannot gate a downstream call.
        eligible = all(node["state"] == "succeeded" and node["value"] is not None
                       and node["grounded"] for node in team)
        if eligible:
            left, right = team
            handoff = Source(
                f"{case.case_id}-handoff", f"{originals[0].title} and {originals[1].title} handoff",
                f"Branch findings for {case.question} "
                f"{originals[0].source_id}: {left['value']}. "
                f"{originals[1].source_id}: {right['value']}. "
                "The two original records are the factual sources.",
            )
            team.append(_observe(f"{label}-merge", root_question, (*originals, handoff),
                                 originals, case.expected_value, run_task))
        team_nodes.extend(team)
        rows.append({
            "case_id": case.case_id,
            "single_passed": single["passed"],
            "team_passed": eligible and all(node["passed"] for node in team),
            "team_final_passed": eligible and team[-1]["passed"],
            "handoff_completed": eligible,
            "single": single,
            "team": team,
        })
    single_passed = sum(row["single_passed"] for row in rows)
    team_passed = sum(row["team_passed"] for row in rows)
    return {
        "benchmark": "agentos-collaboration-pilot/1",
        "workflow": "external_serial_fan_out_then_merge",
        "cases": len(rows),
        "single_passed": single_passed,
        "team_passed": team_passed,
        "team_final_passed": sum(row["team_final_passed"] for row in rows),
        "paired_gain_percentage_points": round(100 * (team_passed - single_passed) / len(rows), 2),
        "team_final_gain_percentage_points": round(
            100 * (sum(row["team_final_passed"] for row in rows) - single_passed) / len(rows), 2
        ),
        "single_totals": _summarize(single_nodes),
        "team_totals": _summarize(team_nodes),
        "rows": rows,
    }
