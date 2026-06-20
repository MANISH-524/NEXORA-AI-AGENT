# NEXORA — Next-gen Ops Recovery Agent

**Autonomous AI agent for IT backup & disaster-recovery readiness.**

NEXORA perceives your fleet, reasons over real failure signatures using deep learning,
detects anomalies with LSTM autoencoders, forecasts RPO breaches with Transformer models,
monitors infrastructure visually through YOLO, escalates only what truly needs a human,
and records every decision in a tamper-evident audit trail.

> **Version:** 3.2.0 — Full ML/AI Engine + Local Fine-Tuned SLM  
> **Stack:** Python · FastAPI · PyTorch · HuggingFace Transformers · PEFT/LoRA · Ollama · YOLOv8 · React  
> **Cost:** $0 (all open-source models, free LLM providers, fully offline option)

---

## How NEXORA Works

### The Agent Loop (every 60 seconds)

```
┌──────────────────────────────────────────────────────────────────────┐
│                     NEXORA AUTONOMOUS LOOP                          │
│                                                                     │
│   ┌───────────┐    ┌──────────┐    ┌──────────┐    ┌────────────┐  │
│   │ PERCEIVE  │───→│  REASON  │───→│ PREDICT  │───→│    ACT     │  │
│   └───────────┘    └──────────┘    └──────────┘    └────────────┘  │
│        │                │               │                │         │
│   Fleet state      Risk scoring    RPO breach       Execute &      │
│   from LogHub +    via LLM +       forecasting      escalate       │
│   ML anomaly       rule engine     via Transformer   decisions     │
│   detection                        + LSTM                          │
│        │                │               │                │         │
│        └────────────────┴───────────────┴────────────────┘         │
│                              │                                      │
│                         ┌────┴────┐                                 │
│                         │ PUBLISH │                                 │
│                         └─────────┘                                 │
│                    WebSocket → Dashboard                            │
│                    Audit log → tamper-proof                         │
│                    Telegram → alerts                                │
└──────────────────────────────────────────────────────────────────────┘
```

**Step-by-step breakdown:**

1. **PERCEIVE** — The agent reads the current state of all 100 simulated backup assets.
   Each asset is grounded in one of 16 real LogHub production datasets (HDFS, Apache,
   Windows, Linux, OpenSSH, Spark, Hadoop). The ML layer adds:
   - **LSTM Autoencoder** scores each asset's backup-cadence time series for anomalies
   - **HuggingFace Transformers** classify log lines by severity (DistilBERT sentiment +
     BART-large-mnli zero-shot classification)
   - **Sentence embeddings** (all-MiniLM-L6-v2) find similar past incidents

2. **REASON** — A reasoning brain evaluates each asset:
   ```
   risk_score = RPO_consumed_% × (criticality / 100) × tier_multiplier
   ```
   The brain can be **(a)** a locally fine-tuned SLM (LoRA, fully offline), **(b)** a cloud
   multi-provider LLM, or **(c)** the deterministic rule engine — in that priority order.
   Whichever runs, it receives real log evidence, anomaly scores, and fleet context to
   produce a human-readable justification, and the same risk math validates every output so
   the system never goes dark. See [Local SLM](#local-slm--offline-reasoning).

3. **PREDICT** — Time-series forecasting projects each asset's failure trajectory forward:
   - **PyTorch Transformer** (self-attention encoder, trained per asset) — primary
   - **Holt-Winters Exponential Smoothing** (statsmodels) — fallback
   - **Linear regression** (scipy / pure-Python) — last resort
   
   If the forecast crosses the RPO threshold within the horizon, a breach alert fires.

4. **ACT** — Based on combined risk + anomaly + prediction signals, the agent picks an
   action from the escalation ladder:
   ```
   NONE → WARN → RETRY_BACKUP → SCHEDULE_RESTORE_TEST → ESCALATE_P2 → ESCALATE_P1 → MANUAL_REVIEW
   ```

5. **PUBLISH** — Results push to the dashboard via WebSocket (live updates), get written
   to the HMAC-signed audit trail, and optionally alert via Telegram.

### Visual Monitoring (YOLOv8)

NEXORA generates synthetic dashboard frames from current fleet state using PIL and runs
YOLOv8 object detection to identify visual anomalies — red-zone clusters, alert density,
infrastructure patterns. This is the **vision layer** that "sees" the fleet the way an
operator would see a monitoring dashboard.

### Data Pipeline

```
Real LogHub CSVs (16 datasets)          HuggingFace Hub
        │                                      │
        ▼                                      ▼
  loghub_engine.py                    hf_dataset_loader.py
  (deterministic simulation)          (remote dataset fetch)
        │                                      │
        └──────────────┬───────────────────────┘
                       ▼
              100 simulated assets
              (state = f(time, dataset))
                       │
        ┌──────────────┼──────────────────┐
        ▼              ▼                  ▼
  Transformer     LSTM Anomaly       Time-Series
  Engine          Detector           Forecaster
  (NLP)           (autoencoder)      (Transformer/HW)
        │              │                  │
        └──────────────┼──────────────────┘
                       ▼
              Reasoning Core (LLM + Rules)
                       │
                       ▼
              Action + Audit + Dashboard
```

---

## Quick Start

### Option A — One Click (Windows)

Double-click **`start.bat`** in the NEXORA folder — it opens 3 terminal windows
automatically (API, Agent, Dashboard). Wait ~20 seconds, then open http://localhost:3000.

### Option B — Step-by-Step (any OS)

> **Prerequisites:** Python 3.11+, Node.js 18+, Git

**Step 1 — Clone & create virtual environment:**

```bash
cd C:\Users\manis\Documents\NEXORA    # or wherever you cloned it
python -m venv venv
```

**Step 2 — Activate the virtual environment:**

```bash
# Windows (PowerShell):
.\venv\Scripts\Activate.ps1

# Windows (CMD):
venv\Scripts\activate.bat

# macOS/Linux:
source venv/bin/activate
```

**Step 3 — Install all Python packages:**

```bash
pip install -r requirements.txt
```

This installs PyTorch, HuggingFace Transformers, Ultralytics YOLOv8, Scikit-learn,
Statsmodels, FastAPI, and all other dependencies (~2-5 minutes depending on internet).

**Step 4 — Install dashboard dependencies:**

```bash
cd dashboard
npm install
cd ..
```

**Step 5 — Configure API key (optional but recommended):**

```bash
cp .env.example .env
```

Open `.env` in any text editor and paste **one** free LLM API key:

```env
# Pick ONE — get a free key from any of these:
OPENROUTER_API_KEY=sk-or-v1-your-key-here     # https://openrouter.ai/keys
# NVIDIA_API_KEY=nvapi-your-key-here           # https://build.nvidia.com
# GEMINI_API_KEY=your-key-here                 # https://aistudio.google.com
```

> Without any key, NEXORA still works using the rule engine + local ML models.
> The LLM key enables the conversational copilot and smarter reasoning.

**Step 6 — Start the system (3 separate terminals):**

**Terminal 1 — API Server:**
```bash
cd C:\Users\manis\Documents\NEXORA
venv\Scripts\uvicorn api.main:app --reload --port 8000
```
You should see: `Uvicorn running on http://0.0.0.0:8000`

**Terminal 2 — Agent Loop:**
```bash
cd C:\Users\manis\Documents\NEXORA
venv\Scripts\python -m agent.main
```
You should see: `NEXORA agent started — cycle every 60s`

**Terminal 3 — React Dashboard:**
```bash
cd C:\Users\manis\Documents\NEXORA\dashboard
npm start
```
Browser opens automatically at http://localhost:3000

**Step 7 — Open in browser:**

| Service | URL | What you'll see |
|---------|-----|-----------------|
| **Dashboard** | http://localhost:3000 | Full operations console with 10 tabs |
| **API Docs** | http://localhost:8000/docs | Interactive Swagger UI — test any endpoint |
| **ML Status** | http://localhost:8000/api/ml-status | JSON showing all ML module readiness |

### What to expect on first run

1. **First 30 seconds:** The API starts, ML models begin loading into memory.
   The first request to AI endpoints (`/api/ai-insights`, `/api/anomaly-scores`) may take
   10-30 seconds as models initialize. After that, responses are fast (~1-2 seconds).

2. **Agent loop kicks in at 60 seconds:** You'll see the agent print its first cycle result
   in Terminal 2. This broadcasts live to the dashboard via WebSocket.

3. **Dashboard auto-refreshes:** All tabs poll the API every 30 seconds. The Overview tab
   shows fleet health immediately. Click **✦ AI Engine** to see transformer analysis,
   anomaly detection, ML forecasts, YOLO vision, and dataset info.

4. **Copilot chat:** The right panel has NEXORA's conversational copilot. Ask it anything
   about your fleet — it answers from live asset state, grounded in real data.

### Stopping the system

Close the 3 terminal windows, or press `Ctrl+C` in each one.

---

## API Keys (all optional — pick ONE free provider)

NEXORA works with **zero keys** (rule-engine + local ML only). For LLM reasoning, add
**one** to `.env`:

| Provider | Free? | Get a key | Env var |
|----------|-------|-----------|---------|
| **OpenRouter** | yes | https://openrouter.ai/keys | `OPENROUTER_API_KEY` |
| **NVIDIA NIM** | yes | https://build.nvidia.com | `NVIDIA_API_KEY` |
| Google Gemini | free tier | https://aistudio.google.com | `GEMINI_API_KEY` |
| Any OpenAI-compatible | varies | Groq / Together / vLLM / Ollama | `OPENAI_COMPAT_API_KEY` + `_BASE_URL` + `_MODEL` |

Auto-detection priority: `local SLM → openrouter → nvidia → gemini → openai_compatible → ollama → rule engine`.
(The local fine-tuned SLM is only used when `NEXORA_USE_SLM=true` and an adapter has been trained.)

---

## Local SLM — Offline Reasoning

NEXORA can run its reasoning on a **locally fine-tuned Small Language Model**, with no cloud
dependency at all. Two runtimes, fully documented in **[docs/SLM.md](docs/SLM.md)**:

1. **LoRA fine-tune** — a model specialised on NEXORA's *own* decision policy. The training
   data is generated automatically from the deterministic risk engine (`compute_risk` +
   `decide_action`), so the SLM learns the exact policy and JSON format with **zero cloud
   labelling**. Every generated decision is re-validated against that math (strict mode), so
   a small model can drive the agent safely.
2. **Ollama** — serve a ready-made model (e.g. `qwen2.5:1.5b`) locally; NEXORA already speaks
   the Ollama wire format.

Everything runs on **CPU** — no GPU required.

```bash
# 1. Build the training set from NEXORA's policy  -> data/slm/
venv\Scripts\python scripts\slm_dataset.py --n 4000

# 2. LoRA fine-tune (CPU)                         -> models/nexora-slm-lora/
venv\Scripts\python scripts\slm_train.py --max-steps 100 --max-len 512

# 3. Evaluate the adapter vs ground truth
venv\Scripts\python scripts\slm_infer.py

# 4. Make it the agent's brain — set in .env, then run normally:
#    NEXORA_USE_SLM=true
```

| Env var | Default | Meaning |
|---------|---------|---------|
| `NEXORA_USE_SLM` | `false` | Use the fine-tuned SLM as the primary reasoning brain |
| `NEXORA_SLM_STRICT` | `true` | Snap any out-of-policy SLM action to deterministic policy (keeps the SLM's explanation) |
| `NEXORA_SLM_MAX_ASSETS` | `24` | CPU budget: assets SLM-reasoned per cycle (rest filled deterministically) |

> A small base + short CPU fine-tune is a proof-of-pipeline; raise `--max-steps` or use
> `--base Qwen/Qwen2.5-1.5B-Instruct` for higher raw accuracy. The strict guardrail keeps
> actions correct regardless.

---

## ML/AI Engine

All ML modules are **optional** — if a package isn't installed, that module gracefully
falls back to statistical or keyword methods. The system never crashes from a missing dependency.

| Module | File | What It Does | Packages Used |
|--------|------|--------------|---------------|
| **Transformer Engine** | `agent/reasoning/transformer_engine.py` | Log severity classification, anomaly scoring, semantic incident search | `transformers`, `sentence-transformers`, `torch` |
| **LSTM Anomaly Detector** | `agent/reasoning/anomaly_detector.py` | Time-series anomaly detection on backup cadence | `torch` (LSTM autoencoder) |
| **Time-Series Forecaster** | `agent/ml/time_series.py` | RPO breach prediction with multi-step horizon | `torch` (Transformer), `statsmodels`, `scipy` |
| **YOLO Visual Monitor** | `agent/vision/yolo_monitor.py` | Visual infrastructure analysis from synthetic frames | `ultralytics` (YOLOv8), `opencv-python`, `Pillow` |
| **HF Dataset Loader** | `agent/ingestion/hf_dataset_loader.py` | Load datasets from HuggingFace Hub + local LogHub | `datasets`, `huggingface-hub` |
| **Local SLM Reasoner** | `agent/reasoning/slm_local.py` | Offline agent reasoning via a LoRA fine-tuned SLM | `transformers`, `peft`, `torch` |

### Models Used

| Model | Source | Purpose |
|-------|--------|---------|
| `distilbert-base-uncased` | HuggingFace | Log line sentiment / anomaly scoring |
| `facebook/bart-large-mnli` | HuggingFace | Zero-shot log severity classification |
| `all-MiniLM-L6-v2` | SentenceTransformers | Semantic embeddings for incident similarity |
| `yolov8n.pt` | Ultralytics | Visual object detection on dashboard frames |
| Custom LSTM Autoencoder | Trained on-the-fly | Backup cadence anomaly detection |
| Custom Transformer | Trained on-the-fly | RPO time-series forecasting |
| `Qwen2.5-0.5B/1.5B-Instruct` + NEXORA LoRA | HuggingFace + fine-tuned locally | Offline agent reasoning (see `docs/SLM.md`) |
| `qwen2.5:1.5b` (Ollama) | Ollama | Optional local LLM reasoning runtime |

---

## Architecture

```
NEXORA/
├── agent/
│   ├── main.py                        # Autonomous loop (Perceive→Reason→Predict→Act)
│   ├── config.py                      # Central env config + provider resolution
│   ├── ingestion/
│   │   ├── loghub_engine.py           # 16 LogHub datasets, deterministic simulation
│   │   ├── hf_dataset_loader.py       # HuggingFace Hub + local dataset loader
│   │   ├── hdfs_log_source.py         # HDFS log parsing
│   │   ├── apache_log_source.py       # Apache log parsing
│   │   ├── windows_event_source.py    # Windows event log parsing
│   │   └── ...                        # More log sources
│   ├── reasoning/
│   │   ├── llm_providers.py           # Multi-provider LLM (OpenRouter/NVIDIA/Gemini/...)
│   │   ├── reasoning_core.py          # Risk scoring + action planning (LLM + rule fallback)
│   │   ├── predictive_engine.py       # Statistical RPO-breach forecasting
│   │   ├── transformer_engine.py      # HuggingFace NLP: severity, anomaly, embeddings
│   │   ├── anomaly_detector.py        # PyTorch LSTM autoencoder
│   │   └── slm_local.py               # Offline LoRA fine-tuned SLM reasoner
│   ├── ml/
│   │   └── time_series.py             # Transformer/HW/Linear RPO forecasting
│   ├── vision/
│   │   └── yolo_monitor.py            # YOLOv8 visual infrastructure monitoring
│   ├── memory/
│   │   └── audit_logger.py            # HMAC-signed audit trail (Supabase + local)
│   ├── actions/
│   │   └── telegram_client.py         # Telegram alert integration
│   └── prompts/                       # System + chat prompt templates
├── api/
│   └── main.py                        # FastAPI backend (v3.1.0)
├── dashboard/
│   └── src/
│       ├── App.js                     # React app — 10 tabs
│       └── components/
│           ├── Dashboard.js            # Fleet overview
│           ├── AIInsights.js           # ✦ AI Engine tab (5 sub-sections)
│           └── ...                     # Assets, Risk, Forecast, Audit, etc.
├── scripts/
│   ├── slm_dataset.py                 # Build SLM training data from NEXORA's policy
│   ├── slm_train.py                   # LoRA fine-tune (CPU) → models/nexora-slm-lora/
│   └── slm_infer.py                   # Evaluate the fine-tuned adapter
├── models/
│   ├── nexora-slm-lora/               # Trained LoRA adapter (after slm_train.py)
│   └── Modelfile.nexora               # Ollama export template
├── docs/SLM.md                        # Local SLM guide (LoRA + Ollama)
├── loghub/                            # 16 real LogHub CSV datasets
├── tests/scenarios/                   # 73 curated incident scenarios
├── requirements.txt                   # Full ML stack (PyTorch, Transformers, PEFT, YOLO, etc.)
├── start.bat                          # One-click launch (Windows)
└── .env.example                       # Template for API keys
```

---

## API Endpoints

### Core Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/health` | Fleet metrics + active LLM provider |
| GET | `/api/assets?dataset=` | Current asset state (100 assets) |
| GET | `/api/datasets` | Available datasets + real error rates |
| GET | `/api/risk-summary` | Risk breakdown by tier |
| GET | `/api/predictions` | Statistical RPO-breach forecasts |
| GET | `/api/restore-tests` | Overdue restore-drill backlog |
| GET | `/api/audit` | Tamper-evident decision log |
| POST | `/api/simulate/trigger` | Run a cycle on a specific scenario/dataset |
| POST | `/api/agent/cycle` | Agent publishes a completed cycle → WebSocket broadcast |
| POST | `/api/chat` | Conversational copilot grounded in live fleet state |
| WS | `/ws` | Live cycle stream for the dashboard |

### AI Engine Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/ml-status` | Status of all ML modules (incl. `slm_local`) |
| GET | `/api/ai-insights` | Transformer-based fleet-wide log analysis |
| GET | `/api/anomaly-scores` | LSTM anomaly scores for all assets |
| GET | `/api/ml-predictions` | Deep-learning RPO breach forecasts |
| GET | `/api/visual-analysis` | YOLO visual fleet analysis |
| POST | `/api/visual-analysis/upload` | Analyze an uploaded screenshot |
| GET | `/api/hf-datasets` | Available HuggingFace + local datasets |
| GET | `/api/hf-datasets/{key}` | Load a specific dataset |
| POST | `/api/ai-insights/classify` | Classify a log line in real-time |

---

## Dashboard Tabs

| Tab | What It Shows |
|-----|---------------|
| Overview | Fleet health, metrics, active provider badge |
| Assets | All 100 assets with status, RPO, last backup |
| Risk Matrix | Risk heatmap by tier and criticality |
| Predictions | RPO breach forecasts with confidence |
| **✦ AI Engine** | **Transformer analysis, LSTM anomalies, ML forecasts, YOLO vision, datasets** |
| Forecast | Statistical trend projections |
| Reasoning | LLM reasoning traces + rule engine decisions |
| Audit Trail | HMAC-signed decision log |
| Scenarios | 73 curated incident simulations |
| Chat | Conversational copilot with fleet context |

---

## Risk Model

```
risk_score = RPO_consumed_% × (criticality / 100) × tier_multiplier

Tier multipliers:  T1 (Critical) = 2.0
                   T2 (Important) = 1.5
                   T3 (Standard)  = 1.0
                   T4 (Archive)   = 0.5
```

Escalation ladder:
```
NONE → WARN → RETRY_BACKUP → SCHEDULE_RESTORE_TEST → ESCALATE_P2 → ESCALATE_P1 → MANUAL_REVIEW
```

The same math backs both the LLM and the rule engine — decisions are always consistent
and explainable regardless of which provider is active.

---

## Graceful Degradation

NEXORA is designed to **never fail** — every layer has a fallback:

| Component | Primary | Fallback |
|-----------|---------|----------|
| Reasoning brain | Local SLM (LoRA) → Cloud LLM → Ollama | Deterministic rule engine |
| SLM action safety | Fine-tuned SLM decision | Snapped to deterministic policy (strict mode) |
| Log Classification | BART zero-shot (Transformer) | Keyword matching |
| Anomaly Detection | LSTM Autoencoder (PyTorch) | Z-score + IQR statistical |
| RPO Forecasting | Transformer model (PyTorch) | Holt-Winters → Linear regression |
| Visual Analysis | YOLOv8 detection | Pixel-level color analysis |
| Incident Search | Sentence embeddings (cosine) | Keyword overlap scoring |
| Datasets | HuggingFace Hub (remote) | Local LogHub CSVs |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Agent runtime | Python 3.11+ |
| API | FastAPI + Uvicorn + WebSocket |
| Dashboard | React 18 + react-scripts |
| Deep learning | PyTorch 2.x |
| NLP | HuggingFace Transformers 5.x, Sentence-Transformers |
| Local SLM | PEFT/LoRA fine-tuning, Ollama runtime |
| Vision | Ultralytics YOLOv8, OpenCV, Pillow |
| Time series | Statsmodels (Holt-Winters), SciPy |
| Data science | NumPy, Pandas, Scikit-learn |
| Datasets | HuggingFace Datasets, LogHub |
| LLM integration | OpenAI-compatible API (any provider) |
| Persistence | Supabase (optional) + local JSONL |
| Alerting | Telegram Bot API (optional) |

---

## Notes

- The audit trail is mirrored to `data/audit_log.jsonl` — survives with no database.
- `NEXORA_WORLD_TICK_SECONDS` controls simulation speed (default 300s).
- ML models are trained on-the-fly at first request, then cached in memory.
- First calls to `/api/ai-insights`, `/api/anomaly-scores`, `/api/ml-predictions` may
  take 10-30s as models initialize. Subsequent calls are cached and fast.
- Never commit your real `.env`.
