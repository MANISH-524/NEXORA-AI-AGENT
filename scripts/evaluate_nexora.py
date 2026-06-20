"""
NEXORA Evaluation Script
Run NEXORA against test scenarios and score its accuracy.
Run: python scripts/evaluate_nexora.py
"""

import json
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import time



from agent.reasoning.reasoning_core import reason

def evaluate(scenarios_path: str = "tests/scenarios/hdfs_training_scenarios.json"):
    """
    Run NEXORA against all scenarios and report accuracy.
    """
    with open(scenarios_path) as f:
        scenarios = json.load(f)
    
    print(f"\nNEXORA Evaluation — {len(scenarios)} scenarios")
    print("=" * 60)
    
    correct = 0
    wrong = 0
    results = []
    
    for s in scenarios:
        asset_state = s["asset_state"]
        expected    = s["expected_action"]
        
        # Ask NEXORA to reason about this scenario
        result = reason([asset_state])
        assessments = result.get("assessments", [])
        
        if assessments:
            actual = assessments[0].get("action", "NONE")
            explanation = assessments[0].get("explanation", "")
        else:
            actual = "NONE"
            explanation = "No assessment returned"
        
        is_correct = actual == expected
        if is_correct:
            correct += 1
        else:
            wrong += 1
        
        results.append({
            "scenario": s["scenario_id"],
            "expected": expected,
            "actual": actual,
            "correct": is_correct,
            "explanation": explanation
        })
        
        status = "✓" if is_correct else "✗"
        print(f"  {status} {s['scenario_id']}: expected {expected}, got {actual}")
        if not is_correct:
            print(f"      NEXORA said: {explanation}")
    
    accuracy = (correct / len(scenarios)) * 100
    
    print("\n" + "=" * 60)
    print(f"NEXORA Accuracy Score: {accuracy:.1f}/100")
    print(f"  Correct: {correct}/{len(scenarios)}")
    print(f"  Wrong:   {wrong}/{len(scenarios)}")
    
    if accuracy >= 85:
        print(f"\n✅ NEXORA is performing well — ready for deployment")
    elif accuracy >= 70:
        print(f"\n⚠️  NEXORA needs improvement — review wrong answers and update system prompt")
    else:
        print(f"\n❌ NEXORA needs significant work — focus on the most common failure patterns")
    
    # Save results for review
    with open("tests/evaluation_results.json", "w") as f:
        json.dump({"accuracy": accuracy, "results": results}, f, indent=2)
    
    print(f"\nDetailed results saved to: tests/evaluation_results.json")
    return accuracy

if __name__ == "__main__":
    evaluate()