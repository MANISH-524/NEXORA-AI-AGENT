"""
NEXORA — Reasoning Core
-----------------------
Scores recovery risk and plans actions for a batch of assets.

Provider-agnostic: it asks `llm_providers.call_llm_json()` for a decision and
that module transparently tries OpenRouter / NVIDIA NIM / Gemini / any
OpenAI-compatible endpoint / local Ollama, in priority order. If every
provider is unavailable (no key configured, all rate-limited, network down),
the deterministic `rule_engine_fallback()` keeps the agent fully functional —
the agent never silently stops reasoning.

Both the LLM path and the rule path are validated against the same scoring
math, so the dashboard always receives consistent, well-formed assessments.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path

from agent.reasoning import llm_providers
from agent.ingestion import loghub_engine

PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

TIER_MULTIPLIER = {1: 2.0, 2: 1.5, 3: 1.0, 4: 0.5}
VALID_ACTIONS = {
    "NONE", "WARN", "RETRY_BACKUP", "SCHEDULE_RESTORE_TEST",
    "ESCALATE_P2", "ESCALATE_P1", "MANUAL_REVIEW",
}


# ---------------------------------------------------------------------------
# Shared math — the single source of truth for risk scoring, used by both
# the rule engine and to sanity-check / repair LLM output.
# ---------------------------------------------------------------------------
def compute_risk(asset: dict) -> dict:
    rpo_target = max(float(asset.get("rpo_target_hours", 1) or 1), 0.01)
    hours = float(asset.get("hours_since_last_backup", 0) or 0)
    rpo_pct = (hours / rpo_target) * 100.0
    tier = int(asset.get("tier", 3) or 3)
    crit = float(asset.get("criticality_score", 50) or 50)
    mult = TIER_MULTIPLIER.get(tier, 1.0)
    score = rpo_pct * (crit / 100.0) * mult
    return {"rpo_consumed_pct": round(rpo_pct, 1), "risk_score": round(score, 1), "tier": tier}


def decide_action(asset: dict, risk: dict) -> str:
    tier = risk["tier"]
    score = risk["risk_score"]
    consec = int(asset.get("consecutive_failures", 0) or 0)
    overdue = int(asset.get("restore_test_days_overdue", 0) or 0)

    if consec >= 3 and tier == 1:
        return "ESCALATE_P1"
    if consec >= 3 and tier == 2:
        return "ESCALATE_P2"

    if score >= 501:
        action = "ESCALATE_P1"
    elif score >= 200:
        action = "ESCALATE_P2"
    elif score >= 50:
        action = "WARN"
    else:
        action = "NONE"

    if action in ("NONE", "WARN") and overdue > 0:
        action = "SCHEDULE_RESTORE_TEST"

    if tier == 4 and score < 300:
        action = "NONE"

    return action


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------
def build_prompt(asset_batch: list, cycle_id: str) -> str:
    timestamp = datetime.utcnow().isoformat()

    # Slim each asset down to the fields the model needs, and surface real
    # log evidence so the explanation can cite genuine production signatures.
    compact_assets = []
    for a in asset_batch:
        compact = {
            "asset_id": a.get("asset_id"),
            "asset_name": a.get("asset_name"),
            "tier": a.get("tier"),
            "criticality_score": a.get("criticality_score"),
            "rpo_target_hours": a.get("rpo_target_hours"),
            "hours_since_last_backup": a.get("hours_since_last_backup"),
            "consecutive_failures": a.get("consecutive_failures"),
            "restore_test_days_overdue": a.get("restore_test_days_overdue"),
            "source": a.get("source_label"),
        }
        if a.get("evidence"):
            compact["log_evidence"] = a["evidence"]
        compact_assets.append(compact)

    context = {
        "cycle_id": cycle_id,
        "timestamp": timestamp,
        "total_assets": len(compact_assets),
        "assets": compact_assets,
    }

    digest = loghub_engine.failure_pattern_digest(max_datasets=6, lines_per_dataset=1)
    digest_block = ""
    if digest:
        digest_block = (
            "\n\nREAL LOG FAILURE SIGNATURES (sampled from the LogHub datasets that "
            "calibrate this environment):\n" + digest
        )

    return (
        SYSTEM_PROMPT
        + digest_block
        + "\n\nCURRENT CYCLE DATA:\n"
        + json.dumps(context, indent=2)
        + "\n\nRespond with the JSON object now:"
    )


# ---------------------------------------------------------------------------
# LLM-backed reasoning, with validation/repair against the shared math
# ---------------------------------------------------------------------------
def _validate_and_repair(result: dict, asset_batch: list, cycle_id: str, provider: str, model: str) -> dict:
    by_id = {a.get("asset_id"): a for a in asset_batch}
    raw_assessments = result.get("assessments") or result.get("decisions") or []

    repaired = []
    seen = set()
    for item in raw_assessments:
        asset_id = item.get("asset_id")
        asset = by_id.get(asset_id)
        if asset is None:
            # Model hallucinated an asset that isn't in this cycle — drop it.
            continue
        seen.add(asset_id)
        risk = compute_risk(asset)
        action = item.get("action")
        if action not in VALID_ACTIONS:
            action = decide_action(asset, risk)
        repaired.append({
            "asset_id": asset_id,
            "asset_name": asset.get("asset_name", asset_id),
            "tier": risk["tier"],
            "dataset": asset.get("dataset", "core"),
            "rpo_consumed_pct": risk["rpo_consumed_pct"],
            "risk_score": risk["risk_score"],
            "action": action,
            "explanation": (item.get("explanation") or "").strip()
                           or f"Risk score {risk['risk_score']} on tier {risk['tier']} asset.",
            "confidence": float(item.get("confidence", 0.8) or 0.8),
            "evidence": asset.get("evidence"),
            "fallback_mode": False,
        })

    # Any asset the model skipped gets a deterministic assessment so the
    # dashboard never shows a partial fleet.
    for asset in asset_batch:
        if asset.get("asset_id") in seen:
            continue
        risk = compute_risk(asset)
        repaired.append({
            "asset_id": asset.get("asset_id"),
            "asset_name": asset.get("asset_name", asset.get("asset_id")),
            "tier": risk["tier"],
            "dataset": asset.get("dataset", "core"),
            "rpo_consumed_pct": risk["rpo_consumed_pct"],
            "risk_score": risk["risk_score"],
            "action": decide_action(asset, risk),
            "explanation": "Filled deterministically (model omitted this asset).",
            "confidence": 0.7,
            "evidence": asset.get("evidence"),
            "fallback_mode": True,
        })

    critical = sum(1 for a in repaired if "ESCALATE" in a["action"])
    healthy = sum(1 for a in repaired if a["action"] == "NONE")
    summary = (result.get("summary") or "").strip() or _auto_summary(repaired)

    return {
        "cycle_id": cycle_id,
        "assessments": repaired,
        "summary": summary,
        "critical_count": critical,
        "healthy_count": healthy,
        "fallback_mode": False,
        "provider": provider,
        "model": model,
    }


def _auto_summary(assessments: list) -> str:
    total = len(assessments)
    p1 = sum(1 for a in assessments if a["action"] == "ESCALATE_P1")
    p2 = sum(1 for a in assessments if a["action"] == "ESCALATE_P2")
    healthy = sum(1 for a in assessments if a["action"] == "NONE")
    if p1:
        return f"{p1} P1 incident(s) and {p2} P2 across {total} assets — immediate attention required."
    if p2:
        return f"{p2} P2 escalation(s) across {total} assets; {healthy} healthy."
    return f"Fleet stable — {healthy}/{total} assets healthy, no escalations."


def reason(asset_batch: list) -> dict:
    """Try the configured LLM provider chain; on any failure drop cleanly to
    the deterministic rule engine. Always returns a well-formed result."""
    cycle_id = uuid.uuid4().hex[:8]

    if not asset_batch:
        return {
            "cycle_id": cycle_id, "assessments": [], "summary": "No assets to assess.",
            "critical_count": 0, "healthy_count": 0, "fallback_mode": False,
            "provider": "none", "model": "none",
        }

    # Locally fine-tuned SLM takes priority when enabled and trained — this is
    # the fully-offline "the model runs as the agent" path. Any failure inside
    # it drops cleanly through to the cloud provider chain below.
    from agent import config
    if config.USE_SLM:
        try:
            from agent.reasoning import slm_local
            if slm_local.is_available():
                return slm_local.reason_fleet(asset_batch, max_assets=config.SLM_MAX_ASSETS)
        except Exception as e:
            print(f"  [reasoning] local SLM unavailable ({e}); falling back to provider chain")

    prompt = build_prompt(asset_batch, cycle_id)
    try:
        parsed = llm_providers.call_llm_json(prompt)
        provider = parsed.pop("_provider", "unknown")
        model = parsed.pop("_model", "unknown")
        return _validate_and_repair(parsed, asset_batch, cycle_id, provider, model)
    except llm_providers.LLMAllProvidersFailedError as e:
        print(f"  [reasoning] all LLM providers unavailable ({e}); using rule engine")
    except Exception as e:
        print(f"  [reasoning] LLM path error ({e}); using rule engine")

    return rule_engine_fallback(asset_batch, cycle_id)


def rule_engine_fallback(asset_batch: list, cycle_id: str = None) -> dict:
    """Deterministic fallback — keeps NEXORA fully operational with zero
    external dependencies. Uses the exact same scoring math as the LLM path."""
    cycle_id = cycle_id or uuid.uuid4().hex[:8]
    assessments = []
    for asset in asset_batch:
        risk = compute_risk(asset)
        action = decide_action(asset, risk)
        evidence = asset.get("evidence")
        if action == "NONE":
            explanation = f"Healthy — {risk['rpo_consumed_pct']:.0f}% of RPO window consumed, no failures."
        elif evidence:
            explanation = f"Rule engine flagged {action} (score {risk['risk_score']}). Log evidence: {evidence}"
        else:
            explanation = f"Rule engine flagged {action} — risk score {risk['risk_score']} on tier {risk['tier']} asset."
        assessments.append({
            "asset_id": asset.get("asset_id"),
            "asset_name": asset.get("asset_name", asset.get("asset_id")),
            "tier": risk["tier"],
            "dataset": asset.get("dataset", "core"),
            "rpo_consumed_pct": risk["rpo_consumed_pct"],
            "risk_score": risk["risk_score"],
            "action": action,
            "explanation": explanation,
            "confidence": 0.75,
            "evidence": evidence,
            "fallback_mode": True,
        })

    critical = sum(1 for a in assessments if "ESCALATE" in a["action"])
    healthy = sum(1 for a in assessments if a["action"] == "NONE")
    return {
        "cycle_id": cycle_id,
        "assessments": assessments,
        "summary": _auto_summary(assessments),
        "critical_count": critical,
        "healthy_count": healthy,
        "fallback_mode": True,
        "provider": "rule_engine",
        "model": "deterministic",
    }


def reason_with_fallback(asset_batch: list) -> dict:
    """Public entry point used by the agent loop and the API."""
    return reason(asset_batch)


def test_connection():
    test_asset = [{
        "asset_id": "TEST-001", "asset_name": "Test Server", "dataset": "core",
        "tier": 2, "criticality_score": 50, "rpo_target_hours": 8,
        "hours_since_last_backup": 4, "consecutive_failures": 0,
        "restore_test_days_overdue": 0,
    }]
    result = reason(test_asset)
    if result.get("assessments") is not None:
        mode = "rule-engine" if result.get("fallback_mode") else f"LLM ({result.get('provider')}/{result.get('model')})"
        print(f"NEXORA reasoning core OK [{mode}] — {result['summary']}")
        return True
    print("NEXORA reasoning core FAILED")
    return False


if __name__ == "__main__":
    test_connection()
