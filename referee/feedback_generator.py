"""Diagnostic feedback and prompt gradient extraction for adversarial benchmark co-evolution."""

import json
from typing import Any, Dict, List, Optional


def generate_prompt_gradient(
    evaluation_report: Dict[str, Any],
    benchmark_spec: Optional[Dict[str, Any]] = None,
    round_num: int = 1,
) -> Dict[str, Any]:
    """Extracts prompt gradients and co-evolution recommendations from solver performance."""
    accuracy = evaluation_report.get("accuracy", 0.0)
    correct_count = evaluation_report.get("correct_count", 0)
    total_items = evaluation_report.get("total_items", 30)
    per_item = evaluation_report.get("per_item", {})

    recommendations: List[str] = []

    if correct_count > 18:
        # Benchmark too easy for current solver capability
        recommendations.append("Increase adversarial complexity: introduce multi-currency lodging folios and mixed-currency credit memos.")
        recommendations.append("Add ambiguous manager exception clauses in emails.eml requiring secondary rule lookup.")
        recommendations.append("Introduce multi-day business trips where cumulative daily meal caps ($140) bind across multiple receipts.")
    elif correct_count < 10:
        # Benchmark too difficult or ambiguous
        recommendations.append("Clarify policy documentation in solver_packet.md regarding tax and tip proration formulas.")
        recommendations.append("Ensure explicit receipt currency codes and clear itemization labels.")
    else:
        recommendations.append("Candidate achieved ideal canonical equilibrium discriminative range (10-18 / 30 items).")

    gradient_payload = {
        "round": round_num,
        "solver_accuracy": accuracy,
        "solver_score": f"{correct_count}/{total_items}",
        "discriminative_status": "IDEAL" if (10 <= correct_count <= 18) else ("TOO_EASY" if correct_count > 18 else "TOO_HARD"),
        "recommendations": recommendations,
        "gradient_prompt": (
            f"Adversarial Feedback (Round {round_num}): Current solver scored {correct_count}/{total_items} ({accuracy*100:.1f}%).\n"
            f"Target: Maintain 33-60% discriminative score.\n"
            f"Directives:\n- " + "\n- ".join(recommendations)
        )
    }

    return gradient_payload
