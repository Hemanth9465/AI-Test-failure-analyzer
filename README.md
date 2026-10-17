# AI Test Failure Analyzer

A local application that explains software test errors using Qwen AI through Ollama.
No account, password, model-selection screen, or separate launcher is required.
The UI has three tabs: Upload file, Type error, and History.

## Requirements

- Python 3.10 or newer (Python 3.12 is supported).
- Ollama installed and running on your computer.
- An installed Qwen model. This project has been used with qwen2.5:7b.
- Internet for the first dependency installation and model download.
  Afterwards, analysis runs locally using the downloaded model.

## First-time setup

Open PowerShell inside this project folder.

Optional but recommended: create an isolated Python environment:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

If activation is blocked, use .\.venv\Scripts\python.exe in place of python.

Install the app dependency:

```powershell
python -m pip install -r requirements.txt
```

Open Ollama. Download the model once:

```powershell
ollama pull qwen2.5:7b
```

If Ollama is not running, run ollama serve in a separate terminal.
Do not run another copy if Ollama is already serving.

## Run the app

With Ollama running, open a terminal in this folder and run:

```powershell
python app.py
```

The app starts a local Streamlit server and opens your browser.
Use the URL printed in the terminal, normally http://127.0.0.1:8501.
Keep the terminal open. Press Ctrl+C to stop the server.
The terminal also displays full submitted input, progress, AI result summaries,
and errors. Full input is printed only when you click Analyze, not when viewing
History. Do not share terminal output without checking it for sensitive data.
If using a virtual environment, activate it first each time.

Alternative launch command, including when port 8501 is busy:

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8503
```

Open the address printed by that command. Stop old copies before restarting.

## How to use it

1. Upload a UTF-8 .log, .txt, .xml, or .json report, or paste an error in Type error.
2. For multi-error files, select Error to analyze. All is the default and sends
   each failure for analysis. Or select a single error.
   Click Analyze file or Analyze error. Maximum input size is 2 MB.
3. Review the full submitted log and the AI analysis:
   category, likely cause, suggested investigation, and supporting evidence.
4. Use Download full log to save the original input.
5. Open History, select a run, and tick Show selected history to revisit it.
   History results are hidden by default. Results have Category and Error filters:
   choose a specific category/error or All.
6. To print a log, open a sample or downloaded log in Notepad and press Ctrl+P.

## Are we using AI?

Yes. The application calls Ollama's local /api/chat endpoint with a Qwen model.
The AI reads error text and proposes a likely failure category, explanation,
investigation steps, and short evidence quotes.
There is no demo/rules selector in this UI and no silent rules-only fallback:
if Qwen is unavailable, the app reports an error.
An empty response, invalid diagnosis, or timeout can prevent an analysis.

The project uses an existing pretrained model; it does not train or fine-tune Qwen.
Parsing, input validation, SQLite history storage, and text-similarity grouping
are ordinary program logic, not AI. Internal grouping is not shown in this simple UI.

## Processing flow

Input -> validate size/format -> parse failures -> send failure text to Qwen
-> validate structured response -> verify evidence quotes against the log
-> save locally -> display results and history.

Plain-text inputs without recognizable test records are treated as one error.
Supported structured reports include JUnit XML and the JSON formats shown in sample_logs.
The categories are real_bug, flaky_test, environment, ui_change, network,
timeout, test_data, and unknown.
An explanation is a hypothesis, not proof. A single timeout does not prove flakiness.

Qwen returns structured JSON. Numeric strings and explicit confidence percentages
are accepted; unusable confidence is omitted without discarding an otherwise valid
analysis. Confidence is a model estimate, not measured accuracy.
Evidence quotes must occur in the supplied log; unsupported quotes are removed.
The app does not execute suggested fixes.

## Folder guide

- app.py: UI and its direct Python entry point.
- ui_workflow.py: coordinates parsing, analysis, history, and internal grouping.
- analyzer/parser.py: reads supported reports and cleans log text.
- analyzer/engine.py: Ollama client, response validation, and similarity utilities.
- analyzer/store.py: SQLite persistence.
- analyzer/__init__.py: Python package marker.
- requirements.txt: Streamlit dependency.
- .streamlit/config.toml: local UI/server settings.
- sample_logs/: uploadable failure examples only. Every included file has been
  checked with the report parser. Use these files to try the analysis UI.
- data/analyzer.db: locally saved history, created automatically.
- logs/: retained output from older launches; the current app prints startup output
  in the terminal.
- __pycache__/: automatically generated Python cache, not a file you need to run.

There is no tests folder or separate start/run script in the project.
The previous launch files were moved into a recoverable backup outside this folder.
Existing history was preserved.

## Model and connection settings

The app automatically uses an installed Qwen model. It prefers OLLAMA_MODEL
when installed, otherwise the first installed Qwen model reported by Ollama.
The code's default preference is qwen3:4b; qwen2.5:7b also works.

Optional PowerShell settings, before starting the app:

```powershell
$env:OLLAMA_MODEL = "qwen2.5:7b"
$env:OLLAMA_URL = "http://127.0.0.1:11434"
python app.py
```

OLLAMA_URL defaults to the local address above. Changing it to a remote server
will send your submitted logs to that server.

## Troubleshooting and privacy

- No Qwen ready: start Ollama; use ollama list to check installed models.
- Slow analysis: a 7B model can be slow on CPU or limited memory. Wait for the
  request; the app can report a timeout. This computer's Ollama has one processing
  slot, so the app uses one request at a time to avoid a queue of expiring requests.
  Responses are requested in concise form with a 512-token output limit and a
  300-second request timeout. This is not a speed guarantee; All still needs one
  AI request per failure. Database writes remain on the main thread.
- Old login page: stop the old server and run app.py from this exact folder.
- Saved failed analysis: rerun the input; old history entries are not rewritten.
- Duplicate buttons were fixed by giving each tab its own download-button ID.
- Do not upload passwords, API keys, personal data, or confidential company logs
  without authorization. Submitted logs and analyses are saved in the local database.
- The UI binds to localhost. It is a single-user local app, not an authenticated
  multi-user production service.
- Review AI output before acting. This is a project prototype, not a guaranteed
  root-cause detector.
