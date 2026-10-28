#!/usr/bin/env python3
"""Validate paired local magent A/B results and render a reviewable Markdown summary."""
import argparse
import collections
import csv
from pathlib import Path
import statistics
import sys

COLUMNS = ("case_id", "variant", "difficulty", "project", "base_sha", "route",
           "verdict", "acceptance_pass", "elapsed_sec", "input_tokens", "output_tokens",
           "worker_calls", "verifier_calls", "retries", "checkpoint_recovered", "notes")
DIFFICULTIES = {"S", "M", "H"}
VERDICTS = {"pass", "fail", "blocked", "infra_fail", "setup_fail"}
EXCLUDED = {"blocked", "infra_fail", "setup_fail"}
NUMERIC = {"elapsed_sec", "input_tokens", "output_tokens", "worker_calls",
           "verifier_calls", "retries"}


def optional_number(value, key):
    if not value.strip():
        return None
    try:
        number = float(value) if key == "elapsed_sec" else int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {key}: {value!r}") from exc
    if number < 0:
        raise ValueError(f"{key} cannot be negative")
    return number


def load_pairs(path):
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or any(column not in reader.fieldnames for column in COLUMNS):
            raise ValueError("Missing required CSV columns: " + ", ".join(COLUMNS))
        raw = list(reader)
    if not raw:
        raise ValueError("Empty results CSV")
    pairs = collections.defaultdict(dict)
    for index, row in enumerate(raw, 2):
        cid = row["case_id"].strip()
        variant = row["variant"].strip()
        if not cid or variant not in ("old", "new"):
            raise ValueError(f"row {index}: invalid case_id or variant")
        if row["difficulty"].strip() not in DIFFICULTIES or row["verdict"].strip() not in VERDICTS:
            raise ValueError(f"row {index}: invalid difficulty or verdict")
        if row["acceptance_pass"].strip() not in ("0", "1"):
            raise ValueError(f"row {index}: acceptance_pass must be 0 or 1")
        if row["verdict"] == "pass" and row["acceptance_pass"] != "1":
            raise ValueError(f"row {index}: pass requires independent acceptance")
        if row["verdict"] != "pass" and row["acceptance_pass"] != "0":
            raise ValueError(f"row {index}: only pass may set acceptance_pass=1")
        if row["checkpoint_recovered"].strip() not in ("", "0", "1"):
            raise ValueError(f"row {index}: invalid checkpoint_recovered")
        for number in NUMERIC:
            row[number] = optional_number(row[number], number)
        if variant in pairs[cid]:
            raise ValueError(f"Duplicate {cid}/{variant}")
        pairs[cid][variant] = row
    for cid, group in pairs.items():
        if set(group) != {"old", "new"}:
            raise ValueError(f"Incomplete pair {cid}: expected old and new")
        old, new = group["old"], group["new"]
        if any(old[field].strip() != new[field].strip() for field in ("difficulty", "project", "base_sha")):
            raise ValueError(f"Pair {cid}: difficulty/project/base_sha inconsistent")
    return dict(sorted(pairs.items()))


def statistics_for(groups):
    eligible = {k: p for k, p in groups.items()
                if not any(x["verdict"] in EXCLUDED for x in p.values())}
    num = len(eligible)
    result = {"paired": len(groups), "eligible": num, "excluded": len(groups) - num}
    for variant in ("old", "new"):
        rows = [p[variant] for p in eligible.values()]
        result[variant + "_pass"] = sum(x["acceptance_pass"] == "1" for x in rows)
        times = [x["elapsed_sec"] for x in rows if x["elapsed_sec"] is not None]
        result[variant + "_time_avg"] = statistics.mean(times) if len(times) == num and num else None
        result[variant + "_retries"] = sum(x["retries"] for x in rows if x["retries"] is not None)
        result[variant + "_route"] = dict(collections.Counter(x["route"] or "(unknown)" for x in rows))
    token_pairs = [p for p in eligible.values() if all(
        p[v][f] is not None for v in ("old", "new") for f in ("input_tokens", "output_tokens"))]
    result["token_pairs"] = len(token_pairs)
    for v in ("old", "new"):
        result[v + "_tokens"] = sum(p[v]["input_tokens"] + p[v]["output_tokens"] for p in token_pairs)
    return result


def render(pairs):
    lines = ["# magent Stage 3 paired A/B report", "",
             "Generated from local CSV; NOT a claim of statistically significant model improvement.", "",
             "| Difficulty | Valid pairs | Old pass | New pass | Old avg sec | New avg sec | Excluded |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    results = {}
    for label, subset in [("ALL", pairs)] + [(key, {cid: p for cid, p in pairs.items()
                                    if p["old"]["difficulty"] == key}) for key in ("S", "M", "H")]:
        s = statistics_for(subset)
        results[label] = s
        def sec(value):
            return "N/A" if value is None else f"{value:.1f}"
        lines.append(f"| {label} | {s['eligible']} | {s['old_pass']} | {s['new_pass']} | "
                     f"{sec(s['old_time_avg'])} | {sec(s['new_time_avg'])} | {s['excluded']} |")
    all_stats = results["ALL"]
    # Route is the initially declared route, not a complete history of agents
    # actually invoked after remediation; the latter requires session logs.
    medium_pairs = {cid: pair for cid, pair in pairs.items()
                    if pair["old"]["difficulty"] == "M"}
    medium_valid = {cid: pair for cid, pair in medium_pairs.items()
                    if not any(row["verdict"] in EXCLUDED for row in pair.values())}
    medium_standard_initial = sum(
        pair["new"]["route"] == "standard" for pair in medium_valid.values())
    missing_token_pairs = all_stats["eligible"] - all_stats["token_pairs"]
    lines += ["", f"Total paired cases: {all_stats['paired']}. Valid comparisons: {all_stats['eligible']}.",
              f"Known complete-token pairs: {all_stats['token_pairs']}; "
              f"old={all_stats['old_tokens']}, new={all_stats['new_tokens']} (input+output only).",
              f"Known retry totals: old={all_stats['old_retries']}, new={all_stats['new_retries']} "
              "(missing retry values are omitted, not treated as measured zero).",
              f"Old route distribution: {all_stats['old_route']}",
              f"New route distribution: {all_stats['new_route']}",
              f"New M-class initial standard route: {medium_standard_initial}/{len(medium_valid)} "
              "(route field records the initial declaration, not all subagent calls).",
              f"Missing complete token pairs: {missing_token_pairs}/{all_stats['eligible']} "
              "(missing values are not zero).",
              f"Known retry rows: old={sum(p['old']['retries'] is not None for p in pairs.values())}, "
              f"new={sum(p['new']['retries'] is not None for p in pairs.values())} "
              "(includes excluded pairs; inspect status before interpreting).",
              "", "## Pairs needing attention", ""]
    flagged = False
    for cid, pair in pairs.items():
        a, b = pair["old"], pair["new"]
        reason = None
        if a["verdict"] in EXCLUDED or b["verdict"] in EXCLUDED:
            reason = f"excluded ({a['verdict']} / {b['verdict']})"
        elif a["acceptance_pass"] == "1" and b["acceptance_pass"] == "0":
            reason = "REGRESSION: old passed, new failed"
        elif b["acceptance_pass"] == "0":
            reason = "both failed or new failed"
        if reason:
            flagged = True
            lines.append(f"- {cid}: {reason}")
    if not flagged:
        lines.append("- None detected in reported pairs.")
    lines += ["", "## Interpretation rules", "",
              "- Investigate every regression and out-of-scope edit before changing default routing.",
              "- Excluded blocked/setup/infra pairs must be rerun after the environmental issue is resolved.",
              "- Time is compared only when every eligible row in that variant has elapsed_sec.",
              "- Token comparisons include only cases with both variants' input/output tokens recorded.",
              "- 20 cases are a directional smoke benchmark, not a confidence interval or randomized controlled trial.",
              "- Return this report, the CSV, task manifest, redacted failures and a long-task recovery report."]
    if medium_valid and medium_standard_initial == 0:
        lines += ["", "WARNING: no new M-class case declared standard as its initial "
                  "route; this CSV cannot establish a Terra standard-worker "
                  "quality or efficiency benefit. Inspect actual session logs "
                  "for escalation/worker history."]
    if missing_token_pairs:
        lines += ["", f"WARNING: token comparison covers only "
                  f"{all_stats['token_pairs']}/{all_stats['eligible']} eligible pairs; "
                  "do not present it as total experiment usage or billing."]
    if len(pairs) < 20:
        lines += ["", f"WARNING: only {len(pairs)} / 20 planned cases supplied."]
    return "\n".join(lines) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        report = render(load_pairs(args.csv_file))
        if args.out:
            args.out.write_text(report, encoding="utf-8")
            print(f"Report written: {args.out}")
        else:
            print(report, end="")
        return 0
    except (ValueError, OSError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
