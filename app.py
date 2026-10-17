"""Simple UI. Start with python app.py or python -m streamlit run app.py."""
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

# Plain Python launches the UI server. Inside Streamlit, render the page instead.
if __name__ == "__main__" and get_script_run_ctx(suppress_warning=True) is None:
    import sys
    from streamlit.web import cli
    sys.argv = ["streamlit", "run", str(Path(__file__).resolve()),
                "--server.address", "127.0.0.1", "--server.headless", "false"]
    cli.main()
    raise SystemExit(0)

from analyzer.engine import ollama_request
from analyzer.parser import parse_report
from analyzer.store import Store
from ui_workflow import analyze_report

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("test_failure_analyzer")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s",
                                           datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
CONFIGURED_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:4b")
st.set_page_config(page_title="AI Test Failure Analyzer", page_icon="🔎", layout="centered")


@st.cache_resource
def database():
    return Store(os.getenv("ANALYZER_DB", str(ROOT / "data" / "analyzer.db")))


@st.cache_data(ttl=10)
def installed_models():
    try:
        response = ollama_request(URL, "/api/tags", timeout=3)
        return [m["name"] for m in response.get("models", []) if m["name"].lower().startswith("qwen")]
    except Exception:
        return []


def result(run, view):
    if run.get("original_log"):
        with st.expander("Full uploaded log"):
            st.code(run["original_log"], language=None)
            st.download_button("Download full log", run["original_log"], "original.log", "text/plain", key="log_" + view + "_" + run["id"])
    st.subheader("Analysis")
    st.caption(f"{run['file']} · {datetime.fromtimestamp(run['created']):%d %b %Y, %H:%M} · "
               + (f"Qwen AI: {run['model']}" if run["mode"] == "ollama" else "Demo rules · No AI"))
    failures = [t for t in run["tests"] if t["status"] == "failed"]
    if not failures:
        st.success("No failed tests found in this report.")
    categories = sorted({t.get("analysis", {}).get("category") for t in failures
                         if t.get("analysis", {}).get("category")})
    if categories:
        category = st.selectbox("Category", ["All"] + categories,
                               format_func=lambda c: c.replace("_", " ").title(),
                               key="category_" + view + "_" + run["id"])
        if category != "All":
            failures = [t for t in failures if t.get("analysis", {}).get("category") == category]
    if len(failures) > 1:
        index = st.selectbox("Error", [None] + list(range(len(failures))),
                            format_func=lambda i: "All" if i is None else failures[i]["name"],
                            key="error_" + view + "_" + run["id"])
        if index is not None:
            failures = [failures[index]]
    for test in failures:
        with st.expander(test["name"], expanded=len(failures) <= 3):
            if test.get("analysis_error"):
                st.error(test["analysis_error"])
                continue
            analysis = test.get("analysis")
            if not analysis:
                st.info("This analysis did not finish. Submit the error again.")
                continue
            st.markdown("**Category**")
            st.write(analysis["category"].replace("_", " ").title())
            st.markdown("**Likely cause**")
            st.write(analysis["root_cause"])
            st.markdown("**Suggested fix / investigation**")
            st.write(analysis["suggested_fix"])
            if analysis["evidence"]:
                st.markdown("**Evidence**")
                for quote in analysis["evidence"]:
                    st.code(quote, language=None)
            if analysis.get("confidence") is not None:
                st.caption(f"AI confidence: {analysis['confidence']:.0%} (uncalibrated estimate)")
            elif analysis.get("source") == "ollama":
                st.caption("AI confidence unavailable. Review the evidence and suggested investigation.")
            with st.expander("Original error"):
                st.code(test["error"], language=None)


def submit(filename, text, mode, model, source, selected_index=None):
    try:
        if not text.strip():
            raise ValueError("Enter an error message or upload a non-empty log file.")
        if len(text.encode("utf-8")) > 2 * 1024 * 1024:
            raise ValueError("Maximum file or error size is 2 MB.")
        logger.info("Submitted %s (%s)\n--- FULL INPUT ---\n%s\n--- END INPUT ---", filename, source, text)
        actual_name, actual_text = filename, text
        if filename.lower().endswith((".txt", ".log")):
            try:
                parse_report(filename, text)
            except ValueError as exc:
                if "No test records found" not in str(exc):
                    raise
                # Any typed error is accepted, even without a framework traceback.
                actual_name = "error.json"
                actual_text = json.dumps({"tests": [{"name": "Entered error" if source == "typed" else filename,
                                                   "status": "failed", "error": text}]})
        if selected_index is not None:
            parsed = parse_report(actual_name, actual_text)
            actual_name = "selected-error.json"
            actual_text = json.dumps({"tests": [parsed[selected_index]]})
        if mode == "ollama" and model not in installed_models():
            raise ValueError("Start Ollama with an installed Qwen model, then try again.")
        with st.spinner("Analyzing…"):
            bar = st.progress(0, text="Analyzing")
            def update_progress(done, total):
                bar.progress(done / max(total, 1), text=f"Analyzed {done} of {total} records")
            try:
                run = analyze_report(database(), 0, actual_name, actual_text, "Local analysis", mode, model, URL,
                                     progress=update_progress)
            finally:
                bar.empty()
        run["file"] = filename
        run["original_log"] = text
        run["selected_index"] = selected_index
        database().save_run(0, run)
        logger.info("Saved analysis %s for %s", run["id"], filename)
        st.session_state["latest_run"] = run["id"]
        st.session_state["latest_source"] = source
    except (ValueError, UnicodeDecodeError) as exc:
        logger.warning("Input rejected for %s: %s", filename, exc)
        st.error(str(exc))
    except Exception as exc:
        logger.exception("Analysis failed for %s", filename)
        st.error(f"Could not analyze this error: {exc}")


st.title("AI Test Failure Analyzer")
st.caption("Upload a log or type an error. View the analysis and your saved history.")
models = installed_models()
mode = "ollama"
model = CONFIGURED_MODEL if CONFIGURED_MODEL in models else (models[0] if models else CONFIGURED_MODEL)

upload_tab, type_tab, history_tab = st.tabs(["Upload file", "Type error", "History"])
with upload_tab:
    uploaded = st.file_uploader("Upload your error log", type=["log", "txt", "xml", "json"])
    selected_index = None
    if uploaded is not None and uploaded.size <= 2 * 1024 * 1024:
        try:
            parsed = parse_report(uploaded.name, uploaded.getvalue().decode("utf-8-sig"))
            failed_indices = [i for i, t in enumerate(parsed) if t["status"] == "failed"]
            if len(failed_indices) > 1:
                selected_index = st.selectbox(
                    "Error to analyze", [None] + failed_indices,
                    format_func=lambda i: "All" if i is None else parsed[i]["name"])
        except (ValueError, UnicodeDecodeError):
            pass  # Submission shows the existing validation message.
    if st.button("Analyze file", type="primary"):
        if uploaded is None:
            st.error("Choose a log file first.")
        elif uploaded.size > 2 * 1024 * 1024:
            st.error("Maximum file size is 2 MB.")
        else:
            try:
                text = uploaded.getvalue().decode("utf-8-sig")
                submit(uploaded.name, text, mode, model, "upload", selected_index)
            except UnicodeDecodeError:
                st.error("Please upload a UTF-8 text log or supported report.")
    if st.session_state.get("latest_source") == "upload":
        saved = database().run(0, st.session_state["latest_run"])
        if (saved and uploaded is not None
                and saved["file"] == uploaded.name
                and saved.get("original_log", "").encode("utf-8") == uploaded.getvalue().removeprefix(b"\xef\xbb\xbf")
                and saved.get("selected_index") == selected_index):
            result(saved, "upload")

with type_tab:
    with st.form("type_form"):
        error = st.text_area("Type or paste your error", height=180, placeholder="AssertionError: expected 200 but got 500")
        if st.form_submit_button("Analyze error", type="primary"):
            submit("typed-error.log", error, mode, model, "typed")
    if st.session_state.get("latest_source") == "typed":
        saved = database().run(0, st.session_state["latest_run"])
        if saved and saved.get("original_log") == error:
            result(saved, "typed")

with history_tab:
    runs = database().runs(0)
    if not runs:
        st.info("No history yet. Analyze an error to save it here.")
    else:
        by_id = {r["id"]: r for r in runs}
        run_id = st.selectbox("Previous analysis", list(by_id), format_func=lambda key:
                             f"{datetime.fromtimestamp(by_id[key]['created']):%d %b %Y %H:%M:%S} · {by_id[key]['file']} · {key[:6]}")
        if st.checkbox("Show selected history", value=False):
            result(by_id[run_id], "history")
