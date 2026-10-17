"""Standalone Streamlit workflow; shares parsers, model engine and SQLite with the API."""
import csv
import io
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from analyzer.engine import ai_analysis, cluster_failures, demo_analysis
from analyzer.parser import parse_report
logger = logging.getLogger("test_failure_analyzer")


def analyze_report(store, user_id, filename, text, project, mode, model, base_url, build="", progress=None):
    if mode not in {"demo", "ollama"}:
        raise ValueError("Invalid analysis mode.")
    if not project.strip() or len(project) > 100:
        raise ValueError("Project name must contain 1–100 characters.")
    if len(text.encode("utf-8")) > 2 * 1024 * 1024:
        raise ValueError("Maximum report size is 2 MB.")
    tests = parse_report(filename, text)
    started = time.monotonic()
    logger.info("Starting analysis: %s | model=%s | records=%d", filename, model, len(tests))
    for i, test in enumerate(tests):
        test["id"] = i + 1
    run = {"id": uuid.uuid4().hex, "project": project.strip(), "build": build[:200],
           "created": time.time(), "file": filename, "mode": mode,
           "model": model if mode == "ollama" else None, "state": "processing",
           "processed": 0, "tests": tests, "clusters": []}
    store.save_run(user_id, run)
    try:
        for test in tests:
            previous = store.history(user_id, project.strip(), test["suite"], test["name"], run["created"])
            outcomes = previous + ([test["status"]] if test["status"] != "skipped" else [])
            mixed = "passed" in outcomes and "failed" in outcomes
            test["history"] = {"recent_outcomes": outcomes, "observations": len(outcomes),
                               "flaky_candidate": mixed or test["retry"],
                               "reason": "Mixed pass/fail history; compare build and environment." if mixed else
                                         ("Retry evidence is present." if test["retry"] else "No inconsistency observed.")}
        # Only AI requests run in worker threads. SQLite and UI updates stay
        # on the calling thread, and tests keep their original report order.
        failures = [test for test in tests if test["status"] == "failed"]
        run["processed"] = len(tests) - len(failures)
        # This local Ollama instance has one processing slot. Do not queue five
        # requests that can expire before the model begins generating.
        run["workers"] = min(1, len(failures))
        if progress:
            progress(run["processed"], len(tests))
        if failures:
            with ThreadPoolExecutor(max_workers=run["workers"]) as executor:
                futures = {
                    (executor.submit(demo_analysis, test) if mode == "demo" else
                     executor.submit(ai_analysis, test, model, base_url)): test
                    for test in failures
                }
                for future in as_completed(futures):
                    test = futures[future]
                    try:
                        test["analysis"] = future.result()
                        logger.info("Result: %s | category=%s | cause=%s | investigation=%s",
                                    test["name"], test["analysis"]["category"],
                                    test["analysis"]["root_cause"], test["analysis"]["suggested_fix"])
                    except Exception as exc:
                        test["analysis_error"] = str(exc)
                        logger.error("AI error for %s: %s", test["name"], exc)
                    run["processed"] += 1
                    logger.info("Progress: %d/%d records", run["processed"], len(tests))
                    store.save_run(user_id, run)
                    if progress:
                        progress(run["processed"], len(tests))
        run["clusters"] = cluster_failures(tests)
        run["analysis_errors"] = sum(bool(t.get("analysis_error")) for t in tests)
        run["state"] = "completed_with_errors" if run["analysis_errors"] else "completed"
        logger.info("Finished: %s | state=%s | errors=%d | elapsed=%.1fs",
                    filename, run["state"], run["analysis_errors"], time.monotonic() - started)
    except Exception:
        run["state"] = "failed"
        run["error"] = "Processing interrupted. Upload again; check the launcher window for details."
        raise
    finally:
        run["finished"] = time.time()
        store.save_run(user_id, run)
    return run


def csv_export(run):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["test", "suite", "status", "category", "source", "confidence_uncalibrated",
                     "root_cause", "suggested_fix", "flaky_candidate", "reviewed_category", "analysis_error"])
    for t in run["tests"]:
        a = t.get("analysis", {})
        values = [t["name"], t["suite"], t["status"], a.get("category", ""), a.get("source", ""),
                  a.get("confidence", ""), a.get("root_cause", ""), a.get("suggested_fix", ""),
                  t.get("history", {}).get("flaky_candidate", False), t.get("reviewed_category", ""), t.get("analysis_error", "")]
        writer.writerow(["'" + str(v) if str(v).lstrip().startswith(("=", "+", "-", "@")) else v for v in values])
    return out.getvalue()
