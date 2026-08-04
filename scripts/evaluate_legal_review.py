"""冻结合同审查集的离线评测：输出分层 Precision / Recall / F1 JSON 报告。"""
import argparse, json
from pathlib import Path

def score(expected, predicted):
    e, p = set(expected), set(predicted)
    tp = len(e & p); precision = tp / len(p) if p else 0; recall = tp / len(e) if e else 0
    return precision, recall, 2 * precision * recall / (precision + recall) if precision + recall else 0

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--predictions", required=True); parser.add_argument("--output", default="artifacts/legal-review-eval.json")
    args = parser.parse_args(); fixture = Path("tests/fixtures/legal_review_eval.jsonl")
    cases = {x["id"]: x for x in map(json.loads, fixture.read_text(encoding="utf8").splitlines()) if x}
    predictions = {x["id"]: x for x in map(json.loads, Path(args.predictions).read_text(encoding="utf8").splitlines()) if x}
    report = {"cases": len(cases), "tiers": {}}
    for tier in sorted({x["tier"] for x in cases.values()}):
        rows = [score(c["expected_risk_levels"], predictions.get(c["id"], {}).get("risk_levels", [])) for c in cases.values() if c["tier"] == tier]
        report["tiers"][tier] = dict(zip(("precision", "recall", "f1"), [round(sum(x[i] for x in rows) / len(rows), 4) for i in range(3)]))
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(report, ensure_ascii=False))
if __name__ == "__main__": main()
