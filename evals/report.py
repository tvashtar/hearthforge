"""Assemble the comparison report from per-cell results."""

from __future__ import annotations


def _judge_cells(judge) -> list[str]:
    if judge is None:
        return ["judge-failed"] * 5
    dims = [judge.narrative_quality, judge.mechanical_fidelity,
            judge.ruling_quality, judge.player_experience]
    avg = sum(d.score for d in dims) / 4
    return [str(d.score) for d in dims] + [f"{avg:.2f}"]


def render_report(results: list[dict], *, judge_model: str) -> str:
    lines = [
        "# DM Model Eval Report", "",
        f"Judge: {judge_model} (fixed across all cells; scores comparable within "
        "this report only)", "",
        "| cell | resolved model | wall clock (s) | median turn (s) | out tokens "
        "| beats done | refusals | crashes | retry loops | orphaned T2 | supplied viol "
        "| schema rej | tools/msg | narr | mech | ruling | player | judge avg |",
        "|" + "---|" * 18,
    ]
    for r in results:
        m = r["metrics"]
        beats = f"{len(r['beats_completed'])}/{len(r['beats_completed']) + len(r['beats_failed'])}"
        if r.get("error"):
            beats += f" (INCOMPLETE: {r['error']})"
        row = [
            r["cell"], r.get("resolved_model") or "?", f"{r['wall_clock_s']:.0f}",
            str(r.get("median_turn_s", "?")), str(r.get("output_tokens", "?")), beats,
            str(m["refusals"]), str(m["crashes"]), str(m["refusal_retry_loops"]),
            str(m["orphaned_tier2"]), str(m["player_supplied_violations"]),
            str(m["schema_rejections"]),
            str(m["tool_calls_per_player_message"]), *_judge_cells(r["judge"]),
        ]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    for r in results:
        lines += [f"## {r['cell']}", ""]
        if r["judge"] is not None:
            for name in ("narrative_quality", "mechanical_fidelity",
                         "ruling_quality", "player_experience"):
                dim = getattr(r["judge"], name)
                lines.append(f"- **{name}** ({dim.score}/5): {dim.justification}")
            lines += ["", f"> {r['judge'].overall_comments}", ""]
        else:
            lines += ["- judge-failed: mechanical metrics only", ""]
    return "\n".join(lines)
