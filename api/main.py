"""
NEXORA — Dashboard & Control API
--------------------------------
FastAPI backend the React dashboard talks to. Responsibilities:
  - serve current fleet state, risk summary, datasets (all LogHub-grounded)
  - accept completed agent cycles and fan them out live over WebSocket
  - run on-demand simulations and predictions
  - back the conversational ChatBox with a real, context-grounded LLM call
  - expose provider/health status so the UI can show what's actually running
  - AI Engine: transformer log analysis, LSTM anomaly detection, YOLO vision,
    time-series ML forecasting, HuggingFace dataset browsing

Key fix vs the old version: the agent now POSTs cycles to /api/agent/cycle,
which broadcasts to every connected dashboard. Previously the agent pushed a
websocket message type the server never handled, so the live feed stayed empty.
"""

import asyncio
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent import config
from agent.ingestion import loghub_engine
from agent.reasoning.reasoning_core import reason_with_fallback, rule_engine_fallback, compute_risk, decide_action
from agent.reasoning import predictive_engine, llm_providers
from agent.memory import audit_logger

# --- AI Engine modules (lazy-import so missing deps never break the API) ---
def _import_transformer_engine():
    try:
        from agent.reasoning import transformer_engine
        return transformer_engine
    except Exception:
        return None

def _import_anomaly_detector():
    try:
        from agent.reasoning import anomaly_detector
        return anomaly_detector
    except Exception:
        return None

def _import_yolo_monitor():
    try:
        from agent.vision import yolo_monitor
        return yolo_monitor
    except Exception:
        return None

def _import_time_series():
    try:
        from agent.ml import time_series
        return time_series
    except Exception:
        return None

def _import_hf_loader():
    try:
        from agent.ingestion import hf_dataset_loader
        return hf_dataset_loader
    except Exception:
        return None

app = FastAPI(title="NEXORA API", description="Next-gen Ops Recovery Agent — Dashboard API", version="3.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS or ["*"],
    allow_methods=["*"], allow_headers=["*"],
)

cycle_history = []
CYCLE_HISTORY_MAX = 200


@app.on_event("startup")
def _startup():
    loghub_engine.warm_cache()


class ConnectionManager:
    def __init__(self):
        self.active = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def count(self):
        return len(self.active)


manager = ConnectionManager()


def add_cycle(entry: dict):
    cycle_history.insert(0, entry)
    del cycle_history[CYCLE_HISTORY_MAX:]


def normalize_assessments(assessments: list) -> list:
    out = []
    for a in assessments:
        out.append({
            "asset_id": a.get("asset_id", ""),
            "asset_name": a.get("asset_name", a.get("asset_id", "")),
            "risk_score": round(float(a.get("risk_score", 0) or 0), 1),
            "rpo_percentage": round(float(a.get("rpo_consumed_pct", a.get("rpo_percentage", 0)) or 0), 1),
            "action": a.get("action", "NONE"),
            "explanation": a.get("explanation", ""),
            "evidence": a.get("evidence"),
            "mode": "rule" if a.get("fallback_mode") else "llm",
            "tier": a.get("tier", 3),
            "dataset": a.get("dataset", "core"),
            "confidence": a.get("confidence", 0.75),
        })
    return out


def _cycle_summary_record(result: dict, assets: list, kind: str, scenario_id=None, dataset="all") -> dict:
    decisions = normalize_assessments(result.get("assessments", []))
    return {
        "cycle_id": result.get("cycle_id", f"sim-{uuid.uuid4().hex[:8]}"),
        "type": kind,
        "scenario_id": scenario_id,
        "dataset": dataset,
        "timestamp": datetime.utcnow().isoformat(),
        "fallback_mode": result.get("fallback_mode", False),
        "provider": result.get("provider", "unknown"),
        "model": result.get("model", ""),
        "critical_count": result.get("critical_count", 0),
        "healthy_count": result.get("healthy_count", 0),
        "summary": result.get("summary", ""),
        "asset_count": len(assets),
        "decisions": decisions,
    }


# --------------------------------------------------------------------------- #
# Core / health
# --------------------------------------------------------------------------- #
@app.get("/")
def root():
    return {"agent": "NEXORA", "version": "3.0.0", "status": "running", "websocket_clients": manager.count}


@app.get("/api/health")
def health():
    try:
        assets = loghub_engine.get_all_assets()
        total = len(assets)
        healthy = sum(1 for a in assets if a.get("consecutive_failures", 0) == 0)
        critical = sum(1 for a in assets if a.get("consecutive_failures", 0) >= 3)
        success_rate = (healthy / max(total, 1)) * 100
    except Exception:
        total = success_rate = critical = 0
    return {
        "total_assets": total,
        "backup_success_rate": round(success_rate, 1),
        "active_alerts": critical,
        "cycles_recorded": len(cycle_history),
        "websocket_clients": manager.count,
        "provider": llm_providers.provider_status(),
        "last_updated": datetime.utcnow().isoformat(),
    }


@app.get("/api/provider")
def provider_status():
    """What LLM backend is configured and what last answered — for the UI badge."""
    return llm_providers.provider_status()


# --------------------------------------------------------------------------- #
# Assets / datasets / risk
# --------------------------------------------------------------------------- #
@app.get("/api/assets")
def get_assets(dataset: str = "all"):
    try:
        assets = loghub_engine.get_assets_for_dataset(dataset)
        return {"count": len(assets), "dataset": dataset, "assets": assets}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/datasets")
def list_datasets():
    try:
        return loghub_engine.dataset_registry()
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/datasets/{dataset_id}/assets")
def dataset_assets(dataset_id: str):
    try:
        assets = loghub_engine.get_assets_for_dataset(dataset_id)
        return {"dataset": dataset_id, "count": len(assets), "assets": assets}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/risk-summary")
def risk_summary():
    try:
        assets = loghub_engine.get_all_assets()
    except Exception:
        assets = []
    tiers = {t: {"total": 0, "healthy": 0, "critical": 0} for t in (1, 2, 3, 4)}
    for a in assets:
        t = a.get("tier", 3)
        t = t if t in tiers else 3
        tiers[t]["total"] += 1
        consec = a.get("consecutive_failures", 0)
        if consec == 0:
            tiers[t]["healthy"] += 1
        if consec >= 3:
            tiers[t]["critical"] += 1
    return {
        "tier_1": tiers[1], "tier_2": tiers[2], "tier_3": tiers[3], "tier_4": tiers[4],
        "total_assets": sum(t["total"] for t in tiers.values()),
        "total_critical": sum(t["critical"] for t in tiers.values()),
    }


# --------------------------------------------------------------------------- #
# Predictions
# --------------------------------------------------------------------------- #
@app.get("/api/predictions")
def predictions(dataset: str = "all"):
    try:
        assets = loghub_engine.get_assets_for_dataset(dataset)
        forecasts = predictive_engine.predict_fleet(assets, only_at_risk=False)
        at_risk = [f for f in forecasts if f["risk"] in ("high", "medium")]
        return {
            "count": len(forecasts),
            "high": sum(1 for f in forecasts if f["risk"] == "high"),
            "medium": sum(1 for f in forecasts if f["risk"] == "medium"),
            "forecasts": forecasts,
            "at_risk": at_risk,
        }
    except Exception as e:
        return {"error": str(e)}


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
@app.get("/api/simulate/scenarios")
def list_scenarios():
    scenarios_dir = ROOT / "tests" / "scenarios"
    all_scenarios = []
    if scenarios_dir.exists():
        for f in sorted(scenarios_dir.glob("*scenarios*.json")):
            try:
                data = json.loads(f.read_text())
                for s in data:
                    s["file"] = f.name
                    all_scenarios.append(s)
            except Exception:
                pass
    return {"count": len(all_scenarios), "scenarios": all_scenarios}


@app.post("/api/simulate/trigger")
async def trigger_simulation(body: dict = Body(...)):
    scenario_id = body.get("scenario_id")
    dataset = body.get("dataset", "all")
    use_fallback = body.get("use_fallback", False)
    try:
        if scenario_id:
            assets = _assets_from_scenario(scenario_id)
            if assets is None:
                return {"error": f"Scenario '{scenario_id}' not found"}
        else:
            assets = loghub_engine.get_assets_for_dataset(dataset)

        result = rule_engine_fallback(assets) if use_fallback else reason_with_fallback(assets)
        result["assessments"] = normalize_assessments(result.get("assessments", []))
        result["simulation"] = {
            "scenario_id": scenario_id, "dataset": dataset, "use_fallback": use_fallback,
            "asset_count": len(assets), "timestamp": datetime.utcnow().isoformat(),
        }
        entry = _cycle_summary_record(result, assets, "simulation", scenario_id, dataset)
        add_cycle(entry)
        await manager.broadcast({"type": "cycle_update", "cycle": entry})
        return result
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}


def _assets_from_scenario(scenario_id: str):
    scenarios_dir = ROOT / "tests" / "scenarios"
    universe = loghub_engine._build_asset_universe()
    for f in scenarios_dir.glob("*.json"):
        try:
            scenarios = json.loads(f.read_text())
        except Exception:
            continue
        for sc in scenarios:
            sid = sc.get("id") or sc.get("scenario_id")
            if sid != scenario_id:
                continue
            asset_map = sc.get("assets") or {}
            if not asset_map and sc.get("asset_state"):
                asset_map = {sc["asset_state"]["asset_id"]: sc["asset_state"]}
            assets = []
            for aid, override in asset_map.items():
                base = universe.get(aid, {
                    "asset_id": aid, "asset_name": aid, "tier": 2,
                    "criticality_score": 50, "rpo_target_hours": 8, "dataset": sc.get("category", "core"),
                })
                merged = {**base, **(override or {})}
                merged.setdefault("hours_since_last_backup", 0)
                merged.setdefault("consecutive_failures", 0)
                merged.setdefault("restore_test_days_overdue", 0)
                assets.append(merged)
            return assets
    return None


# --------------------------------------------------------------------------- #
# Agent cycle ingest (called by the agent loop) + cycle history
# --------------------------------------------------------------------------- #
@app.post("/api/agent/cycle")
async def ingest_agent_cycle(cycle: dict = Body(...)):
    """The autonomous agent POSTs completed cycles here; we normalize, store,
    and fan out to every connected dashboard. THIS is the live pipeline."""
    decisions = normalize_assessments(cycle.get("decisions", []))
    entry = {
        "cycle_id": cycle.get("cycle_id", f"cycle-{uuid.uuid4().hex[:8]}"),
        "type": "agent_cycle",
        "cycle_number": cycle.get("cycle_number"),
        "dataset": cycle.get("dataset", "all"),
        "timestamp": cycle.get("timestamp", datetime.utcnow().isoformat()),
        "fallback_mode": cycle.get("fallback_mode", False),
        "provider": cycle.get("provider", "unknown"),
        "model": cycle.get("model", ""),
        "critical_count": cycle.get("critical_count", 0),
        "healthy_count": cycle.get("healthy_count", 0),
        "summary": cycle.get("summary", ""),
        "asset_count": cycle.get("asset_count", len(decisions)),
        "action_count": cycle.get("action_count", 0),
        "decisions": decisions,
        "forecasts": cycle.get("forecasts", []),
    }
    add_cycle(entry)
    await manager.broadcast({"type": "cycle_update", "cycle": entry})
    return {"ok": True, "cycle_id": entry["cycle_id"], "broadcast_to": manager.count}


@app.get("/api/cycles")
def get_cycles(limit: int = 20):
    return {"cycles": cycle_history[:limit]}


@app.get("/api/cycles/{cycle_id}")
def get_cycle_detail(cycle_id: str):
    for entry in cycle_history:
        if entry.get("cycle_id") == cycle_id:
            return entry
    return {"error": "Cycle not found"}


# --------------------------------------------------------------------------- #
# Audit & restore tests
# --------------------------------------------------------------------------- #
@app.get("/api/audit")
def get_audit_log(limit: int = 50):
    records = audit_logger.read_recent(limit)
    if records:
        return records
    return cycle_history[:limit]


@app.get("/api/restore-tests")
def get_restore_tests():
    """Derive restore-test evidence from current fleet state (assets whose
    restore drill is overdue), so this panel works with zero external DB."""
    try:
        assets = loghub_engine.get_all_assets()
    except Exception:
        return []
    records = []
    for a in assets:
        overdue = a.get("restore_test_days_overdue", 0)
        if overdue > 0:
            records.append({
                "asset_id": a["asset_id"],
                "asset_name": a["asset_name"],
                "tier": a["tier"],
                "status": "overdue",
                "days_overdue": overdue,
                "cadence_days": a.get("cadence_days"),
                "created_at": datetime.utcnow().isoformat(),
            })
    records.sort(key=lambda r: r["days_overdue"], reverse=True)
    return records[:25]


# --------------------------------------------------------------------------- #
# Conversational copilot
# --------------------------------------------------------------------------- #
_CHAT_PROMPT_PATH = ROOT / "agent" / "prompts" / "chat_prompt.txt"
_CHAT_SYSTEM = _CHAT_PROMPT_PATH.read_text(encoding="utf-8") if _CHAT_PROMPT_PATH.exists() else \
    "You are NEXORA, an IT recovery readiness copilot. Answer from the provided context."


def _build_chat_context() -> dict:
    assets = loghub_engine.get_all_assets()
    # Compute current risk + action for each so chat answers match the dashboard.
    enriched = []
    for a in assets:
        risk = compute_risk(a)
        enriched.append({
            "asset_id": a["asset_id"], "asset_name": a["asset_name"], "tier": a["tier"],
            "dataset": a["dataset"], "rpo_consumed_pct": risk["rpo_consumed_pct"],
            "risk_score": risk["risk_score"], "action": decide_action(a, risk),
            "consecutive_failures": a.get("consecutive_failures", 0),
            "hours_since_last_backup": a.get("hours_since_last_backup"),
            "evidence": a.get("evidence"),
            "note": (f"{a.get('consecutive_failures', 0)} consecutive failures, "
                     f"{risk['rpo_consumed_pct']:.0f}% of RPO window consumed"),
        })
    escalations = [e for e in enriched if "ESCALATE" in e["action"]]
    forecasts = predictive_engine.predict_fleet(assets, only_at_risk=True)
    return {
        "fleet_size": len(enriched),
        "escalations": escalations[:20],
        "at_risk_forecasts": forecasts[:15],
        "healthy_count": sum(1 for e in enriched if e["action"] == "NONE"),
        "top_risk_assets": sorted(enriched, key=lambda e: e["risk_score"], reverse=True)[:15],
    }


@app.post("/api/chat")
async def chat(body: dict = Body(...)):
    """Conversational endpoint backing the dashboard ChatBox. Grounds every
    answer in the real current fleet state, then routes through the same
    multi-provider LLM chain. Degrades to a deterministic summary if no LLM
    provider is available."""
    user_message = (body.get("message") or "").strip()
    history = body.get("history", [])
    if not user_message:
        return {"reply": "Ask me about fleet health, risks, or what to prioritize.", "provider": "none"}

    context = _build_chat_context()
    history_text = ""
    for turn in history[-6:]:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        history_text += f"\n{role.upper()}: {content}"

    prompt = (
        _CHAT_SYSTEM
        + "\n\nLIVE FLEET CONTEXT (JSON):\n" + json.dumps(context, indent=2)
        + (f"\n\nRECENT CONVERSATION:{history_text}" if history_text else "")
        + f"\n\nUSER: {user_message}\nNEXORA:"
    )

    try:
        result = llm_providers.call_llm(prompt)
        reply = result["text"].strip()
        if reply.startswith("{") and reply.endswith("}"):
            try:
                parsed = json.loads(reply)
                reply = parsed.get("message") or parsed.get("reply") or parsed.get("summary") or parsed.get("text") or reply
            except (json.JSONDecodeError, AttributeError):
                pass
        if reply.startswith("```") and reply.endswith("```"):
            reply = reply.strip("`").strip()
            if reply.startswith("json"):
                reply = reply[4:].strip()
        return {"reply": reply, "provider": result["provider"], "model": result["model"]}
    except llm_providers.LLMAllProvidersFailedError:
        return {"reply": _deterministic_chat_reply(user_message, context),
                "provider": "rule_engine", "model": "deterministic"}
    except Exception as e:
        return {"reply": _deterministic_chat_reply(user_message, context),
                "provider": "rule_engine", "model": f"deterministic (llm error: {e})"}


def _deterministic_chat_reply(message: str, context: dict) -> str:
    """A useful answer with no LLM available — keeps the ChatBox functional."""
    esc = context["escalations"]
    forecasts = context["at_risk_forecasts"]
    lines = [
        f"Fleet status: {context['fleet_size']} assets, {context['healthy_count']} healthy, "
        f"{len(esc)} currently escalated.",
    ]
    if esc:
        lines.append("Active escalations (most urgent first):")
        for e in esc[:5]:
            lines.append(f"  - {e['asset_id']} (tier {e['tier']}, {e['action']}, risk {e['risk_score']}): {e.get('note','')}")
    if forecasts:
        lines.append("Predicted to breach RPO soon:")
        for f in forecasts[:4]:
            lines.append(f"  - {f['asset_id']} [{f['risk']}]: {f['reason']}")
    if not esc and not forecasts:
        lines.append("No active escalations or near-term breach forecasts. Fleet looks stable.")
    lines.append("(LLM provider unavailable — this is a deterministic summary. Add a free OpenRouter or NVIDIA key to enable full chat.)")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# AI Engine endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/ml-status")
def ml_status():
    """Overall status of all AI/ML modules — transformer, anomaly, YOLO, time-series."""
    te = _import_transformer_engine()
    ad = _import_anomaly_detector()
    ym = _import_yolo_monitor()
    ts = _import_time_series()
    hf = _import_hf_loader()
    try:
        from agent.reasoning import slm_local
        slm_status = slm_local.status()
        slm_status["enabled"] = config.USE_SLM
    except Exception as e:
        slm_status = {"available": False, "error": str(e)}
    return {
        "transformer_engine": te.ml_status() if te else {"available": False, "error": "import failed"},
        "anomaly_detector": ad.detector_status() if ad else {"available": False, "error": "import failed"},
        "yolo_monitor": ym.monitor_status() if ym else {"available": False, "error": "import failed"},
        "time_series_forecaster": ts.forecaster_status() if ts else {"available": False, "error": "import failed"},
        "hf_dataset_loader": hf.loader_status() if hf else {"available": False, "error": "import failed"},
        "slm_local": slm_status,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.get("/api/ai-insights")
def ai_insights(dataset: str = "all"):
    """
    Transformer-based log analysis across the fleet.
    Uses DistilBERT + zero-shot classification to score each asset's log evidence.
    """
    te = _import_transformer_engine()
    try:
        assets = loghub_engine.get_assets_for_dataset(dataset)
    except Exception as e:
        return {"error": str(e)}

    if te is None:
        return {
            "ok": False,
            "error": "transformer_engine module unavailable",
            "analyzed": 0,
            "critical_signals": [],
            "fleet_anomaly_score": 0.0,
            "method": "unavailable",
        }

    try:
        result = te.analyze_fleet_logs(assets)
        result["dataset"] = dataset
        result["asset_count"] = len(assets)
        result["timestamp"] = datetime.utcnow().isoformat()
        result["model_status"] = te.ml_status()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e), "method": "error"}


@app.get("/api/anomaly-scores")
def anomaly_scores(dataset: str = "all"):
    """
    LSTM + statistical anomaly detection across the fleet time series.
    Returns per-asset anomaly scores and top anomalies.
    """
    ad = _import_anomaly_detector()
    try:
        assets = loghub_engine.get_assets_for_dataset(dataset)
    except Exception as e:
        return {"error": str(e)}

    if ad is None:
        return {"ok": False, "error": "anomaly_detector module unavailable"}

    try:
        result = ad.score_fleet(assets)
        result["dataset"] = dataset
        result["timestamp"] = datetime.utcnow().isoformat()
        result["detector_status"] = ad.detector_status()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


_predictions_cache: dict = {}

@app.get("/api/ml-predictions")
def ml_predictions(dataset: str = "all", horizon: int = 6, max_assets: int = 20):
    """
    Deep-learning enhanced RPO breach forecasts.
    Uses Transformer → Exp.Smoothing → Linear cascade for each asset.
    Capped at max_assets (default 20) for response-time; increase for deeper runs.
    Results cached for 120s to keep the dashboard fast.
    """
    import time
    cache_key = f"{dataset}:{horizon}:{max_assets}"
    cached = _predictions_cache.get(cache_key)
    if cached and (time.time() - cached["_ts"]) < 120:
        return cached["data"]

    ts = _import_time_series()
    try:
        assets = loghub_engine.get_assets_for_dataset(dataset)
    except Exception as e:
        return {"error": str(e)}

    if ts is None:
        return {"ok": False, "error": "time_series module unavailable"}

    try:
        assets_subset = assets[:max_assets]
        result = ts.forecast_fleet(assets_subset, horizon=min(horizon, 12))
        result["dataset"] = dataset
        result["total_fleet_size"] = len(assets)
        result["assets_sampled"] = len(assets_subset)
        result["timestamp"] = datetime.utcnow().isoformat()
        result["forecaster_status"] = ts.forecaster_status()
        _predictions_cache[cache_key] = {"data": result, "_ts": time.time()}
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/visual-analysis")
def visual_analysis():
    """
    YOLO-based visual fleet analysis.
    Generates a synthetic dashboard frame from current asset state and runs
    object detection on it. Upload a real screenshot via POST /api/visual-analysis/upload.
    """
    ym = _import_yolo_monitor()
    try:
        assets = loghub_engine.get_all_assets()
    except Exception as e:
        return {"error": str(e)}

    if ym is None:
        return {"ok": False, "error": "yolo_monitor module unavailable"}

    try:
        result = ym.analyze_fleet_frames(assets)
        result["asset_count"] = len(assets)
        result["timestamp"] = datetime.utcnow().isoformat()
        result["monitor_status"] = ym.monitor_status()
        # Don't return full base64 in the list endpoint — too large
        result.pop("frame_base64", None)
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/visual-analysis/upload")
async def visual_upload(body: dict = Body(...)):
    """
    Analyze a user-provided base64-encoded PNG/JPG screenshot.
    Body: { "image_base64": "...", "filename": "screenshot.png" }
    """
    import base64, tempfile
    ym = _import_yolo_monitor()
    if ym is None:
        return {"ok": False, "error": "yolo_monitor module unavailable"}

    b64 = body.get("image_base64", "")
    filename = body.get("filename", "upload.png")
    if not b64:
        return {"ok": False, "error": "image_base64 required"}

    try:
        img_bytes = base64.b64decode(b64)
        tmp = Path(ROOT) / "data" / "sample" / f"_upload_{uuid.uuid4().hex[:8]}.png"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(img_bytes)
        result = ym.analyze_screenshot(str(tmp))
        tmp.unlink(missing_ok=True)
        result["filename"] = filename
        result["timestamp"] = datetime.utcnow().isoformat()
        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/hf-datasets")
def hf_datasets_info():
    """Browse available HuggingFace + local LogHub datasets."""
    hf = _import_hf_loader()
    if hf is None:
        return {"ok": False, "error": "hf_dataset_loader module unavailable"}
    try:
        return {"ok": True, **hf.available_datasets(), "timestamp": datetime.utcnow().isoformat()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/api/hf-datasets/{dataset_key}")
def hf_dataset_load(dataset_key: str, source: str = "local", max_samples: int = 200):
    """
    Load a specific dataset.
    source=local  → use bundled LogHub CSVs (instant, no network)
    source=hf     → download from HuggingFace Hub (requires 'datasets' package)
    """
    hf = _import_hf_loader()
    if hf is None:
        return {"ok": False, "error": "hf_dataset_loader module unavailable"}
    try:
        if source == "hf":
            return hf.load_hf_dataset(dataset_key, max_samples=max_samples)
        return hf.load_local_loghub_as_hf(dataset_key, max_samples=max_samples)
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/ai-insights/classify")
async def classify_log(body: dict = Body(...)):
    """
    Classify a single log line via zero-shot transformer.
    Body: { "log_line": "...", "find_similar": true }
    """
    te = _import_transformer_engine()
    if te is None:
        return {"ok": False, "error": "transformer_engine unavailable"}

    log_line = body.get("log_line", "").strip()
    if not log_line:
        return {"ok": False, "error": "log_line required"}

    try:
        severity = te.classify_log_severity(log_line)
        anomaly_score = te.anomaly_score_from_text(log_line)
        result = {
            "ok": True,
            "log_line": log_line[:200],
            "anomaly_score": anomaly_score,
            **severity,
        }

        if body.get("find_similar"):
            try:
                assets = loghub_engine.get_all_assets()
                pool = [a["evidence"] for a in assets if a.get("evidence")][:50]
                similar = te.find_similar_incidents(log_line, pool, top_k=5)
                result["similar_incidents"] = similar
            except Exception:
                pass

        return result
    except Exception as e:
        return {"ok": False, "error": str(e)}


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Send current history immediately so a freshly-opened dashboard isn't blank.
        await ws.send_json({"type": "cycle_history", "cycles": cycle_history[:20]})
        while True:
            data = await ws.receive_text()
            try:
                msg = json.loads(data)
            except Exception:
                continue
            if msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
            elif msg.get("type") == "subscribe_cycles":
                await ws.send_json({"type": "cycle_history", "cycles": cycle_history[:20]})
    except WebSocketDisconnect:
        manager.disconnect(ws)
    except Exception:
        manager.disconnect(ws)
