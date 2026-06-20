"""
NEXORA — Autonomous Agent Loop
Perceive -> Reason -> Predict -> Act -> Publish

Every cycle the agent:
  1. Perceives current asset state (LogHub-grounded, deterministic per tick)
  2. Reasons over it (multi-provider LLM, deterministic rule-engine fallback)
  3. Forecasts near-term RPO breaches (predictive engine)
  4. Acts (alerts, restore-test scheduling, audit logging)
  5. Publishes the cycle to the API, which fans it out to dashboard clients

The publish step POSTs to the API's /api/agent/cycle endpoint. The previous
version pushed an 'agent_cycle' message over a websocket the API never
processed, so live cycles never reached the dashboard — fixed here.
"""

import asyncio
import sys
from datetime import datetime

import httpx

from agent import config
from agent.ingestion import loghub_engine
from agent.ingestion.sample_data_source import get_current_asset_states, get_asset_states_by_dataset
from agent.reasoning.reasoning_core import reason_with_fallback
from agent.reasoning.predictive_engine import predict_fleet
from agent.memory.audit_logger import write_audit_log
from agent.actions.telegram_client import send_alert

API_BASE = config.API_WS_URL.replace("ws://", "http://").replace("wss://", "https://").replace("/ws", "")


def log(level: str, message: str):
    ts = datetime.utcnow().strftime("%H:%M:%SZ")
    icons = {"INFO": "->", "OK": "[ok]", "WARN": "[warn]", "ERROR": "[err]", "P1": "[P1]", "P2": "[P2]"}
    print(f"[{ts}] {icons.get(level, '->')} {message}")


async def publish_cycle(cycle_entry: dict):
    """POST the completed cycle to the API so every connected dashboard sees
    it live. Best-effort — a missing API must never stall the agent loop."""
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{API_BASE}/api/agent/cycle", json=cycle_entry)
    except Exception as e:
        log("WARN", f"Publish to API skipped ({e}) — agent continues")


async def dispatch_action(assessment: dict, cycle_id: str):
    action = assessment.get("action", "NONE")
    asset_id = assessment.get("asset_id", "unknown")
    asset_name = assessment.get("asset_name", asset_id)
    reason_text = assessment.get("explanation", "")
    severity = "info"
    delivered = None

    if action == "ESCALATE_P1":
        severity = "p1"
        log("P1", f"{asset_name}: {reason_text}")
        delivered = await send_alert(asset_name, "P1", reason_text)
    elif action == "ESCALATE_P2":
        severity = "p2"
        log("P2", f"{asset_name}: {reason_text}")
        delivered = await send_alert(asset_name, "P2", reason_text)
    elif action == "WARN":
        severity = "warning"
        log("WARN", f"{asset_name}: {reason_text}")
    elif action == "SCHEDULE_RESTORE_TEST":
        log("INFO", f"Restore test scheduled — {asset_name}")
    elif action == "RETRY_BACKUP":
        log("INFO", f"Retrying backup — {asset_name}")
    elif action == "MANUAL_REVIEW":
        severity = "warning"
        log("WARN", f"Manual review needed — {asset_name}: {reason_text}")

    result_note = None
    if delivered is True:
        result_note = "alert_delivered"
    elif delivered is False:
        result_note = "alert_not_delivered"

    try:
        await write_audit_log(
            cycle_id=cycle_id, event_type="action", asset_id=asset_id, severity=severity,
            input_summary=f"{action} on {asset_name}", reasoning_output=reason_text,
            action_taken=action, action_result=result_note,
        )
    except Exception as e:
        log("WARN", f"Audit write failed: {e}")


async def run_cycle(cycle_number: int, dataset: str = None):
    log("INFO", f"Cycle {cycle_number} starting")
    try:
        if dataset and dataset != "all":
            assets = get_asset_states_by_dataset(dataset)
            log("INFO", f"Dataset '{dataset}' — {len(assets)} assets")
        else:
            assets = get_current_asset_states()
            log("INFO", f"Perceived {len(assets)} assets across all datasets")

        result = reason_with_fallback(assets)
        cycle_id = result.get("cycle_id", f"cycle-{cycle_number}")
        critical = result.get("critical_count", 0)
        healthy = result.get("healthy_count", 0)
        summary = result.get("summary", "")
        fallback = result.get("fallback_mode", False)
        provider = result.get("provider", "unknown")

        mode = "rule-engine" if fallback else f"LLM:{provider}"
        log("INFO", f"Reasoning [{mode}] — {critical} critical, {healthy} healthy")

        assessments = result.get("assessments", [])
        forecasts = predict_fleet(assets, only_at_risk=True)
        high_forecasts = [f for f in forecasts if f["risk"] == "high"]
        if high_forecasts:
            log("WARN", f"Predictive engine: {len(high_forecasts)} asset(s) forecast to breach RPO soon")

        action_count = 0
        for a in assessments:
            if a.get("action", "NONE") != "NONE":
                await dispatch_action(a, cycle_id)
                action_count += 1
        log("OK", f"Cycle {cycle_number} complete — {action_count} actions dispatched")

        cycle_entry = {
            "cycle_id": cycle_id,
            "type": "agent_cycle",
            "cycle_number": cycle_number,
            "dataset": dataset or "all",
            "timestamp": datetime.utcnow().isoformat(),
            "fallback_mode": fallback,
            "provider": provider,
            "model": result.get("model", ""),
            "critical_count": critical,
            "healthy_count": healthy,
            "summary": summary,
            "asset_count": len(assets),
            "action_count": action_count,
            "decisions": assessments,
            "forecasts": forecasts,
        }
        await publish_cycle(cycle_entry)

        try:
            await write_audit_log(
                cycle_id=cycle_id, event_type="perception", asset_id="SYSTEM", severity="info",
                input_summary=f"Cycle {cycle_number}: {len(assets)} assets, mode={mode}",
                reasoning_output=summary, action_taken="cycle_complete",
            )
        except Exception as e:
            log("WARN", f"Audit write failed: {e}")
        return result
    except Exception as e:
        import traceback
        log("ERROR", f"Cycle {cycle_number} failed: {e}")
        log("ERROR", traceback.format_exc())
        return None


async def nexora_main():
    print("")
    print("  N E X O R A  —  Next-gen Ops Recovery Agent")
    print("  Recover faster. Risk smarter.")
    print("  " + "-" * 44)
    reg = loghub_engine.warm_cache()
    grounded = sum(1 for v in reg.values() if v["data_grounded"])
    total_assets = sum(v["asset_count"] for v in reg.values())
    log("OK", f"Loaded {len(reg)} LogHub datasets ({grounded} data-grounded), {total_assets} simulated assets")

    chain = config.active_provider_names()
    if chain:
        log("OK", f"LLM provider chain: {' -> '.join(chain)}"
                  + (" -> ollama" if config.USE_LOCAL_FALLBACK else "") + " -> rule-engine")
    else:
        log("WARN", "No LLM provider configured — running on deterministic rule-engine only")
        log("INFO", "Add OPENROUTER_API_KEY or NVIDIA_API_KEY (both free) to .env to enable LLM reasoning")

    log("OK", f"Agent starting — cycle every {config.CYCLE_SECONDS}s. Ctrl+C to stop.")
    print("")

    cycle = 0
    dataset_arg = sys.argv[1] if len(sys.argv) > 1 else None
    while True:
        cycle += 1
        await run_cycle(cycle, dataset=dataset_arg)
        log("INFO", f"Sleeping {config.CYCLE_SECONDS}s...")
        await asyncio.sleep(config.CYCLE_SECONDS)


if __name__ == "__main__":
    try:
        asyncio.run(nexora_main())
    except KeyboardInterrupt:
        print("")
        log("INFO", "NEXORA stopped by operator")
