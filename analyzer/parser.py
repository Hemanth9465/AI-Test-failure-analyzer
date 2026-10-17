"""Report adapters produce the same record format, including passing tests."""
import json
import re
import xml.etree.ElementTree as ET

MAX_TESTS = 500


def record(name, status, error="", suite="", duration=0, retry=False):
    status = str(status).lower()
    status = {"failure": "failed", "error": "failed", "fail": "failed", "broken": "failed",
              "pass": "passed", "success": "passed", "skip": "skipped", "pending": "skipped"}.get(status, status)
    if status not in {"passed", "failed", "skipped"}:
        raise ValueError(f"Unsupported test status: {status}")
    try:
        duration = max(0, float(duration or 0))
    except (TypeError, ValueError):
        duration = 0
    return {"name": str(name)[:300], "suite": str(suite)[:300], "status": status,
            "error": str(error)[:30000], "duration": duration, "retry": bool(retry)}


def parse_junit(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise ValueError("XML declarations containing DTDs or entities are unsupported.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid XML: {exc}") from exc
    rows = []
    for node in root.iter():
        if node.tag.split("}")[-1] != "testcase":
            continue
        children = {c.tag.split("}")[-1]: c for c in node}
        errors = [c for c in node if c.tag.split("}")[-1] in {"failure", "error"}]
        reruns = [c for c in node if c.tag.split("}")[-1] in {"flakyFailure", "flakyError", "rerunFailure", "rerunError"}]
        status = "failed" if errors else ("skipped" if "skipped" in children else "passed")
        details = "\n".join(f"{c.get('type', '')}: {c.get('message', '')}\n{c.text or ''}" for c in errors + reruns)
        rows.append(record(node.get("name", "unnamed"), status, details,
                           node.get("classname", ""), node.get("time", 0), bool(reruns)))
        if len(rows) > MAX_TESTS:
            raise ValueError(f"Maximum {MAX_TESTS} tests per report.")
    return rows


def parse_json(text):
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc
    if isinstance(data, list):
        tests = data
    elif isinstance(data, dict) and isinstance(data.get("tests"), list):
        tests = data["tests"]  # normalized records or pytest-json-report
    elif isinstance(data, dict) and "status" in data:
        tests = [data]  # one Allure result file
    else:
        raise ValueError("JSON needs a tests array, a record array, or an Allure result object.")
    if len(tests) > MAX_TESTS:
        raise ValueError(f"Maximum {MAX_TESTS} tests per report.")
    result = []
    for t in tests:
        if not isinstance(t, dict):
            raise ValueError("Each JSON test must be an object.")
        stages = [t.get(k, {}) for k in ("setup", "call", "teardown")]
        failed = [s for s in stages if isinstance(s, dict) and s.get("outcome") == "failed"]
        status = "failed" if failed else t.get("status", t.get("outcome", "unknown"))
        details = t.get("statusDetails", {})
        error = t.get("error", t.get("message", ""))
        if isinstance(details, dict):
            error = error or "\n".join(str(details.get(k, "")) for k in ("message", "trace"))
        if failed:
            error = "\n".join(str(s.get("longrepr", s.get("crash", s.get("traceback", "")))) for s in failed)
        duration = t.get("duration", sum(s.get("duration", 0) or 0 for s in stages
                                       if isinstance(s, dict) and isinstance(s.get("duration", 0), (int, float))))
        result.append(record(t.get("name", t.get("nodeid", t.get("fullName", "unnamed"))),
                             status, error, t.get("suite", t.get("classname", "")), duration,
                             t.get("retry", False)))
    return result


def parse_text(text):
    # pytest detailed failure sections take precedence over its summary lines.
    headers = list(re.finditer(r"(?m)^_{3,}\s+(.+?)\s+_{3,}\s*$", text))
    rows = []
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        error = text[h.end():end].split("short test summary info")[0].strip()
        rows.append(record(h.group(1), "failed", error))
    if rows:
        return rows
    # Explicit blocks are supported for browser logs / normalized plain text.
    blocks = re.split(r"(?mi)(?=^(?:TEST|CASE):\s*)", text)
    for block in blocks:
        match = re.search(r"(?mi)^(?:TEST|CASE):\s*(.+)$", block)
        if match:
            status = re.search(r"(?mi)^STATUS:\s*(\w+)", block)
            rows.append(record(match.group(1).strip(), status.group(1) if status else "failed", block.strip(),
                               retry=bool(re.search(r"(?i)passed (?:on|after) retry|retry succeeded", block))))
    if rows:
        return rows
    summaries = list(re.finditer(r"(?m)^(FAILED|PASSED|SKIPPED)\s+(\S+)(?:\s+-\s+(.*))?$", text))
    for h in summaries:
        rows.append(record(h.group(2), h.group(1).lower(), h.group(3) or ""))
    if rows:
        # A single failure can retain preceding traceback evidence.
        if len(rows) == 1 and rows[0]["status"] == "failed":
            rows[0]["error"] = text[:30000]
        return rows
    if re.search(r"(?i)exception|assertionerror|traceback|error:|failed|timeout", text):
        name = re.search(r"(?i)starting test\s+(\S+)", text)
        return [record(name.group(1) if name else "uploaded_failure", "failed", text,
                       retry=bool(re.search(r"(?i)passed (?:on|after) retry|retry succeeded", text)))]
    raise ValueError("No test records found. Use JUnit XML, supported JSON, or TEST:/STATUS: text blocks.")


def parse_report(filename, text):
    if not text.strip():
        raise ValueError("Report is empty.")
    suffix = filename.lower().rsplit(".", 1)[-1]
    if suffix == "xml":
        rows = parse_junit(text)
    elif suffix == "json":
        rows = parse_json(text)
    elif suffix in {"txt", "log"}:
        rows = parse_text(text)
    else:
        raise ValueError("Supported extensions: .xml, .json, .txt, .log")
    if not rows:
        raise ValueError("Report contains no supported test records.")
    if len(rows) > MAX_TESTS:
        raise ValueError(f"Maximum {MAX_TESTS} tests per report.")
    # Duplicate identity within a run must not create fake flaky history.
    identities = [(r["suite"], r["name"]) for r in rows]
    if len(set(identities)) != len(identities):
        raise ValueError("Duplicate test identity in one report. Combine retry attempts into one record using retry=true.")
    return rows


def clean_log(text):
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text.replace("\x00", ""))
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?Z?", "[time]", text)
    # Retain timeout durations, HTTP codes and expected/actual values.
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)((?:password|api_key|access_token)\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    return text.strip()[:12000]

