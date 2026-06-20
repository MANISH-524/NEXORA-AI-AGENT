"""
NEXORA — SLM Training Dataset Builder
=====================================
Generates a supervised fine-tuning (SFT) dataset that teaches a Small Language
Model to reproduce NEXORA's recovery-risk reasoning *exactly*.

Why this works without any cloud labelling:
    NEXORA already has a deterministic source of truth for every decision —
    `reasoning_core.compute_risk()` + `reasoning_core.decide_action()`. We
    sample thousands of realistic asset states, compute the ground-truth
    decision + a natural-language rationale, and emit chat-format examples.
    The SLM learns NEXORA's risk policy AND its strict JSON output format.

Output: data/slm/train.jsonl, data/slm/val.jsonl
    Each line: {"messages": [system, user, assistant]} in OpenAI chat format,
    which both TRL/transformers chat templates and Ollama understand.

Run:
    venv\\Scripts\\python scripts\\slm_dataset.py --n 4000
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agent.reasoning.reasoning_core import compute_risk, decide_action  # ground truth

OUT_DIR = ROOT / "data" / "slm"

# Compact policy prompt the SLM is trained against. Far shorter than the full
# operator prompt so a 0.5-1.5B model can attend to it on CPU, but it carries
# the exact rules the deterministic engine enforces.
SLM_SYSTEM_PROMPT = (
    "You are NEXORA, an autonomous IT backup-recovery agent. For the given asset "
    "you output ONE JSON object and nothing else:\n"
    '{"asset_id","action","risk_score","rpo_consumed_pct","explanation","confidence"}\n'
    "action is one of: NONE, WARN, RETRY_BACKUP, SCHEDULE_RESTORE_TEST, "
    "ESCALATE_P2, ESCALATE_P1, MANUAL_REVIEW.\n"
    "Risk policy:\n"
    "- risk_score = rpo_consumed_pct * (criticality/100) * tier_multiplier "
    "(tier1=2.0, tier2=1.5, tier3=1.0, tier4=0.5); "
    "rpo_consumed_pct = hours_since_last_backup / rpo_target_hours * 100.\n"
    "- score>=501 => ESCALATE_P1; >=200 => ESCALATE_P2; >=50 => WARN; else NONE.\n"
    "- 3+ consecutive failures: tier1 => ESCALATE_P1, tier2 => ESCALATE_P2.\n"
    "- If action would be NONE/WARN and a restore test is overdue => SCHEDULE_RESTORE_TEST.\n"
    "- Tier 4 with score<300 => NONE.\n"
    "Explanation: one clear sentence citing the real numbers."
)

ASSET_POOL = [
    ("SAP ERP Production", "database"), ("CRM Database Primary", "database"),
    ("Oracle Finance DB", "database"), ("Payroll Database", "database"),
    ("Exchange Mail Server", "vm"), ("HR SQL Server", "database"),
    ("K8s Production Cluster", "container"), ("NAS File Share", "nas"),
    ("MongoDB Analytics", "database"), ("Dev Server 01", "vm"),
    ("Redis Cache Node", "cache"), ("Jenkins CI Host", "vm"),
    ("Data Lake Bronze", "storage"), ("Auth Service DB", "database"),
    ("Billing Service", "vm"), ("Logging Pipeline", "container"),
]

EVIDENCE_BY_ACTION = {
    "ESCALATE_P1": [
        "FATAL backup agent unreachable; snapshot aborted after 3 retries",
        "ERROR repository offline — last good restore point exceeds RPO target",
        "CRITICAL checksum verification failed on full backup set",
    ],
    "ESCALATE_P2": [
        "ERROR incremental backup timed out; window compliance breached",
        "WARN replication lag growing, last successful job delayed",
    ],
    "WARN": [
        "WARN backup completed late, RPO window partially consumed",
        "INFO transient network reset during transfer, job recovered",
    ],
    "SCHEDULE_RESTORE_TEST": [
        "INFO restore-test interval exceeded; integrity proof stale",
    ],
    "NONE": [
        "INFO nightly incremental completed successfully, checksum verified",
        "INFO snapshot OK, RPO well within target",
    ],
}


def _explanation(asset: dict, risk: dict, action: str) -> str:
    name = asset["asset_name"]
    tier = risk["tier"]
    score = risk["risk_score"]
    pct = risk["rpo_consumed_pct"]
    consec = asset.get("consecutive_failures", 0)
    overdue = asset.get("restore_test_days_overdue", 0)
    if action == "ESCALATE_P1":
        if consec >= 3:
            return (f"{name} hit {consec} consecutive backup failures on a Tier {tier} "
                    f"asset — immediate P1 escalation required.")
        return (f"{name} is a Tier {tier} asset at risk score {score} "
                f"({pct:.0f}% of its RPO window consumed) — P1 escalation.")
    if action == "ESCALATE_P2":
        if consec >= 3:
            return (f"{name} reached {consec} consecutive failures on a Tier {tier} asset — P2 escalation.")
        return (f"{name} scored {score} ({pct:.0f}% RPO consumed) on a Tier {tier} asset — P2 escalation.")
    if action == "WARN":
        return (f"{name} is at {pct:.0f}% of its RPO window (score {score}); warn and keep monitoring.")
    if action == "SCHEDULE_RESTORE_TEST":
        return (f"{name} is healthy on RPO but its restore test is {overdue} day(s) overdue — schedule one.")
    if action == "MANUAL_REVIEW":
        return f"{name} needs manual review — integrity signal is ambiguous."
    return (f"{name} is healthy — only {pct:.0f}% of the RPO window consumed and no failures.")


def _random_asset(i: int) -> dict:
    name, atype = random.choice(ASSET_POOL)
    tier = random.choices([1, 2, 3, 4], weights=[0.25, 0.3, 0.25, 0.2])[0]
    crit = {1: (80, 99), 2: (55, 80), 3: (30, 55), 4: (5, 30)}[tier]
    rpo_target = random.choice({1: [2, 4], 2: [8, 12], 3: [24], 4: [48, 72]}[tier])
    # Spread hours so we hit every decision branch.
    roll = random.random()
    if roll < 0.45:
        hours = rpo_target * random.uniform(0.05, 0.7)      # healthy
    elif roll < 0.7:
        hours = rpo_target * random.uniform(0.7, 1.8)       # warn-ish
    elif roll < 0.9:
        hours = rpo_target * random.uniform(1.8, 5.0)       # P2/P1
    else:
        hours = rpo_target * random.uniform(5.0, 12.0)      # severe
    consec = random.choices([0, 1, 2, 3, 4], weights=[0.6, 0.15, 0.1, 0.1, 0.05])[0]
    overdue = random.choices([0, random.randint(1, 40)], weights=[0.7, 0.3])[0]
    return {
        "asset_id": f"ASSET-{i:04d}",
        "asset_name": name,
        "asset_type": atype,
        "tier": tier,
        "criticality_score": random.randint(*crit),
        "rpo_target_hours": rpo_target,
        "hours_since_last_backup": round(hours, 2),
        "consecutive_failures": consec,
        "restore_test_days_overdue": overdue,
    }


def _example(asset: dict) -> dict:
    risk = compute_risk(asset)
    action = decide_action(asset, risk)
    # Attach realistic evidence so explanations learn to cite signatures.
    asset_for_user = dict(asset)
    asset_for_user["log_evidence"] = random.choice(EVIDENCE_BY_ACTION.get(action, EVIDENCE_BY_ACTION["NONE"]))
    target = {
        "asset_id": asset["asset_id"],
        "action": action,
        "risk_score": risk["risk_score"],
        "rpo_consumed_pct": risk["rpo_consumed_pct"],
        "explanation": _explanation(asset, risk, action),
        "confidence": round(random.uniform(0.82, 0.97), 2),
    }
    user = json.dumps({k: asset_for_user[k] for k in (
        "asset_id", "asset_name", "tier", "criticality_score", "rpo_target_hours",
        "hours_since_last_backup", "consecutive_failures", "restore_test_days_overdue",
        "log_evidence")}, separators=(",", ":"))
    return {
        "messages": [
            {"role": "system", "content": SLM_SYSTEM_PROMPT},
            {"role": "user", "content": user},
            {"role": "assistant", "content": json.dumps(target, separators=(",", ":"))},
        ],
        "action": action,  # kept for stratified stats; trainer ignores extra keys
    }


def main(n: int, val_frac: float, seed: int):
    random.seed(seed)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    examples = [_example(_random_asset(i)) for i in range(n)]
    random.shuffle(examples)
    n_val = max(1, int(n * val_frac))
    val, train = examples[:n_val], examples[n_val:]

    dist = {}
    for e in examples:
        dist[e["action"]] = dist.get(e["action"], 0) + 1

    for split, rows in (("train", train), ("val", val)):
        path = OUT_DIR / f"{split}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps({"messages": r["messages"]}) + "\n")
        print(f"  wrote {len(rows):5d} -> {path.relative_to(ROOT)}")

    print("\nAction distribution:")
    for k in sorted(dist):
        print(f"  {k:24} {dist[k]:5d}  ({dist[k]/n*100:.1f}%)")
    print(f"\nDone. {len(train)} train / {len(val)} val examples.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build NEXORA SLM SFT dataset")
    ap.add_argument("--n", type=int, default=4000, help="total examples")
    ap.add_argument("--val-frac", type=float, default=0.1)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()
    main(args.n, args.val_frac, args.seed)
