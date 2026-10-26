"""Validate the CCB envelope, exact schema and verdict/finding consistency."""
import json
import math
import sys


def fail(message, code):
    print("ERROR: " + message, file=sys.stderr)
    raise SystemExit(code)


def validate(value):
    if isinstance(value, list):
        if (not value or not all(isinstance(item, dict) for item in value)
                or sum(item.get("type") == "result" for item in value) != 1):
            fail("Invalid CCB event array", 31)
        value = value[-1]
    if (not isinstance(value, dict) or value.get("type") != "result"
            or value.get("subtype") != "success" or value.get("is_error") is not False
            or not isinstance(value.get("structured_output"), dict)):
        fail("CCB returned no valid successful structured result", 31)
    result = value["structured_output"]
    verdict = result.get("verdict")
    if not isinstance(verdict, str) or verdict not in ("ship", "fix-first", "rethink"):
        fail("Invalid review verdict", 32)
    if (set(result) != {"verdict", "summary", "findings"}
            or not isinstance(result["summary"], str) or not isinstance(result["findings"], list)):
        fail("Review output violates the required schema", 33)
    for item in result["findings"]:
        if not isinstance(item, dict) or set(item) != {"severity", "file", "line", "issue", "recommendation"}:
            fail("Review finding violates the required schema", 33)
        if (not isinstance(item["severity"], str) or item["severity"] not in ("critical", "high", "medium", "low")
                or not all(isinstance(item[key], str) for key in ("file", "issue", "recommendation"))):
            fail("Review finding violates the required schema", 33)
        line = item["line"]
        if line is not None and (isinstance(line, bool) or not isinstance(line, (int, float))
                                 or isinstance(line, float) and (not math.isfinite(line) or not line.is_integer())):
            fail("Finding line must be an integer or null", 33)
    if ((verdict == "ship" and any(item["severity"] != "low" for item in result["findings"]))
            or (verdict != "ship" and not result["findings"])):
        fail("Review verdict contradicts its findings", 34)
    return result


if __name__ == "__main__":
    try:
        def invalid_constant(value):
            raise ValueError("Invalid JSON constant: " + value)
        data = json.load(sys.stdin, parse_constant=invalid_constant)
    except (ValueError, OSError) as error:
        fail("Invalid CCB JSON: " + str(error), 31)
    print(json.dumps(validate(data), ensure_ascii=True))
