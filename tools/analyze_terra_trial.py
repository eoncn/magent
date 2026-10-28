#!/usr/bin/env python3
"""Analyze a small paired Luna vs Terra experiment; do not infer statistical significance."""
import argparse
import collections
import csv
from pathlib import Path
import statistics
import sys

FIELDS = (
    "case_id", "arm", "project", "base_sha", "prompt_sha256",
    "initial_route", "standard_calls", "observed_model", "observed_effort",
    "verifier_model", "verifier_effort", "verdict", "acceptance_pass",
    "elapsed_sec", "input_tokens", "output_tokens", "retries",
    "worker_calls", "verifier_calls", "notes")
ARMS = {
    "control": ("gpt-6-luna", "xhigh"),
    "terra": ("gpt-6.1-terra", "high"),
}
INFRA = {"blocked", "infra_fail", "setup_fail"}
VERDICTS = INFRA | {"pass", "fail"}
NONNEGATIVE = {"standard_calls", "elapsed_sec", "input_tokens", "output_tokens",
               "retries", "worker_calls", "verifier_calls"}
REQUIRED_INTEGER = {"standard_calls"}


def parse_number(value, field, line_no):
    value = value.strip()
    if not value and field not in REQUIRED_INTEGER:
        return None
    try:
        number = float(value) if field == "elapsed_sec" else int(value)
    except ValueError as err:
        raise ValueError("row {}: invalid {}={!r}".format(line_no, field, value)) from err
    if number < 0 or (field == "elapsed_sec" and number != number):
        raise ValueError("row {}: negative/nonfinite {}".format(line_no, field))
    if field == "elapsed_sec" and number == float("inf"):
        raise ValueError("row {}: infinite elapsed_sec".format(line_no))
    return number


def load(path):
    with open(path, encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or any(f not in reader.fieldnames for f in FIELDS):
            raise ValueError("CSV must include: " + ",".join(FIELDS))
        raw = list(reader)
    if not raw:
        raise ValueError("Empty CSV")
    pairs = collections.defaultdict(dict)
    for num, row in enumerate(raw, 2):
        if None in row:
            raise ValueError("row {}: extra columns".format(num))
        case = row["case_id"].strip()
        arm = row["arm"].strip()
        if not case or arm not in ARMS or arm in pairs[case]:
            raise ValueError("row {}: missing case / bad arm / duplicate".format(num))
        if row["verdict"].strip() not in VERDICTS:
            raise ValueError("row {}: invalid verdict".format(num))
        if row["acceptance_pass"].strip() not in ("0", "1"):
            raise ValueError("row {}: acceptance_pass must be 0 or 1".format(num))
        if (row["verdict"] == "pass") != (row["acceptance_pass"] == "1"):
            raise ValueError("row {}: pass requires acceptance_pass=1, others =0".format(num))
        for field in NONNEGATIVE:
            row[field] = parse_number(row[field] or "", field, num)
        pairs[case][arm] = row
    for case, pair in pairs.items():
        if set(pair) != set(ARMS):
            raise ValueError("Unpaired case {}".format(case))
        if any(pair["control"][k].strip() != pair["terra"][k].strip()
               for k in ("project", "base_sha", "prompt_sha256")):
            raise ValueError("Case {}: project/base_sha/prompt_sha256 mismatch".format(case))
        if not all(pair["control"][k].strip() for k in ("project", "base_sha", "prompt_sha256")):
            raise ValueError("Case {}: missing frozen project/base/prompt SHA".format(case))
    return dict(sorted(pairs.items()))


def valid_pair(pair):
    for arm, row in pair.items():
        if row["verdict"] in INFRA:
            return False, "environment or setup: " + arm + "=" + row["verdict"]
        if row["initial_route"].strip() != "standard":
            return False, arm + " did not naturally select standard"
        if row["standard_calls"] is None or row["standard_calls"] < 1:
            return False, arm + " has no actual standard-worker invocation"
        if row["worker_calls"] is None or row["worker_calls"] != row["standard_calls"]:
            return False, arm + " other worker tiers also invoked; model effect confounded"
        if (row["observed_model"].strip(), row["observed_effort"].strip()) != ARMS[arm]:
            return False, arm + " observed model/effort differs from pin"
        if (row["verifier_model"].strip(), row["verifier_effort"].strip()) != (
                "gpt-6-luna", "xhigh"):
            return False, arm + " independent verifier not pinned to Luna/xhigh"
    return True, ""


def display_num(value, digits=1):
    return "N/A" if value is None else ("{:." + str(digits) + "f}").format(value)


def report(pairs):
    valid = {}
    excluded = {}
    for case, pair in pairs.items():
        yes, reason = valid_pair(pair)
        (valid if yes else excluded)[case] = pair if yes else reason
    result = ["# Terra-standard targeted A/B report", "",
              "Same magent skill in both arms; standard worker model/effort is the intended difference.",
              "This is a tiny, non-randomized engineering benchmark, not causal proof or a bill estimate.", "",
              "Total task pairs: {}; comparable: {}; excluded: {}.".format(
                  len(pairs), len(valid), len(excluded)), ""]
    if len(pairs) < 4:
        result.append("WARNING: fewer than four paired cases have been supplied.")
    rows = {arm: [pair[arm] for pair in valid.values()] for arm in ARMS}
    result += ["| Metric | Luna control | Terra experiment |",
               "| --- | ---: | ---: |"]
    def metric(label, key):
        result.append("| {} | {} | {} |".format(label, key("control"), key("terra")))
    metric("Independent acceptance PASS",
           lambda arm: "{}/{}".format(sum(x["acceptance_pass"] == "1" for x in rows[arm]), len(valid)))
    metric("Mean active seconds (complete only)",
           lambda arm: display_num(statistics.mean(x["elapsed_sec"] for x in rows[arm])
                                   if rows[arm] and all(x["elapsed_sec"] is not None for x in rows[arm])
                                   else None))
    metric("Known retry total (missing excluded)",
           lambda arm: str(sum(x["retries"] for x in rows[arm] if x["retries"] is not None)))
    token_cases = {
        case: pair for case, pair in valid.items()
        if all(pair[arm][k] is not None for arm in ARMS
               for k in ("input_tokens", "output_tokens"))
    }
    metric("Token sum on complete paired subset",
           lambda arm: str(sum(pair[arm]["input_tokens"] + pair[arm]["output_tokens"]
                               for pair in token_cases.values())))
    metric("Known complete token pairs", lambda arm: str(len(token_cases)))
    result += ["", "## Pair-level outcomes", ""]
    for case, pair in valid.items():
        a, b = pair["control"], pair["terra"]
        result.append("- {}: Luna={}, Terra={}; Luna={}s, Terra={}s; "
                      "retries Luna={}, Terra={}.".format(
                          case, a["verdict"], b["verdict"], display_num(a["elapsed_sec"]),
                          display_num(b["elapsed_sec"]), a["retries"], b["retries"]))
    if not valid:
        result.append("- No valid paired samples; stop. Do not claim a Terra benefit.")
    result += ["", "## Invalid or excluded pairs (not silently dropped)", ""]
    for case, reason in excluded.items():
        result.append("- {}: {}".format(case, reason))
    if not excluded:
        result.append("- None")
    result += ["", "## Decision limits", "",
               "- A valid comparison needs same frozen repo/prompt and actual natural standard dispatch "
               "with observed model and verifier pins for BOTH arms.",
               "- Record every preflight candidate in the separate screening sheet; do not select by outcome.",
               "- Only {} pairs have complete input+output token numbers; "
               "missing observations are not zero.".format(len(token_cases)),
               "- Separate active-wall time from interruption/usage-limit intervals; "
               "never equate tokens with billed subscription consumption.",
               "- If Terra has failures not seen in control or exceeds time/tokens, retain stable "
               "production configuration until cause is understood.",
               "- If both are successful, small-n results may justify a larger trial, "
               "not an automatic rollout."]
    return "\n".join(result) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv_file", type=Path)
    p.add_argument("--out", type=Path)
    args = p.parse_args(argv)
    try:
        text = report(load(args.csv_file))
        if args.out:
            args.out.write_text(text, encoding="utf-8")
            print("Wrote " + str(args.out))
        else:
            print(text, end="")
        return 0
    except (ValueError, OSError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
