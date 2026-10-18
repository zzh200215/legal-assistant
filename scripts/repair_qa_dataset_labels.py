"""修复 eval/qa_dataset.json 与 eval/fixtures/ 的标注漂移（一次性数据修复，可重复执行）。

背景：9002 的答案集散落在旧样例「AI办公助手实施方案」上（题面 / 参考答 / 关键词），
而 fixture 正文早已是「律智检平台实施方案」；9001/9003 另有 3 处措辞漂移；三个
fixture 的 document_name 也都还是旧样例名字。本脚本按 fixture 正文重写这些标注，
并在 9002 上按章节补齐覆盖（目标 / 交付范围 / 里程碑 / 资源 / 风险 / 培训与验收）。

用法：python -B scripts/repair_qa_dataset_labels.py [--dataset eval/qa_dataset.json] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOC_TITLES: dict[int, str] = {
    9001: "技术服务合同审查样本",
    9002: "律智检平台实施方案",
    9003: "法律服务供应商尽调报告",
}

# 9002 的答案集：按 fixture 正文逐条重写（关键词都是正文原串）
NEW_9002: list[dict] = [
    {
        "name": "plan_target_users",
        "document_id": 9002,
        "category": "project_goal",
        "question": "这个平台主要面向哪类用户？",
        "reference_answer": "主要面向个人用户与小型律所。",
        "expected_chunk_keywords": ["个人用户与小型律所"],
        "should_refuse": False,
    },
    {
        "name": "plan_goal_capabilities",
        "document_id": 9002,
        "category": "project_goal",
        "question": "平台重点提升哪三方面的效率？",
        "reference_answer": "重点提升法律依据定位、合同风险识别和文书初稿效率。",
        "expected_chunk_keywords": ["法律依据定位", "合同风险识别", "文书初稿效率"],
        "should_refuse": False,
    },
    {
        "name": "plan_scope_modules",
        "document_id": 9002,
        "category": "scope",
        "question": "本次交付范围包括哪五个模块？",
        "reference_answer": "包括法律知识库、法律咨询辅助、合同智能审查、法律文书草稿生成、律师审核与版本留痕。",
        "expected_chunk_keywords": ["法律知识库", "合同智能审查", "律师审核与版本留痕"],
        "should_refuse": False,
    },
    {
        "name": "plan_milestone_core_features",
        "document_id": 9002,
        "category": "timeline",
        "question": "合同审查与法律咨询核心功能的计划完成时间是什么时候？",
        "reference_answer": "计划于 2026 年 7 月 05 日完成合同审查与法律咨询核心功能。",
        "expected_chunk_keywords": ["2026 年 7 月 05 日", "合同审查与法律咨询核心功能"],
        "should_refuse": False,
    },
    {
        "name": "plan_risk_stale_statutes",
        "document_id": 9002,
        "category": "risk",
        "question": "法规版本更新不及时会带来什么风险，如何应对？",
        "reference_answer": "若法规版本更新不及时，检索结果可能引用失效条文，对策是增加版本治理。",
        "expected_chunk_keywords": ["引用失效条文", "增加版本治理"],
        "should_refuse": False,
    },
    {
        "name": "plan_risk_hallucination",
        "document_id": 9002,
        "category": "risk",
        "question": "模型幻觉带来的风险是什么，如何应对？",
        "reference_answer": "若模型幻觉导致编造法律依据，需要证据校验Agent拦截。",
        "expected_chunk_keywords": ["编造法律依据", "证据校验Agent"],
        "should_refuse": False,
    },
    {
        "name": "plan_risk_incomplete_facts",
        "document_id": 9002,
        "category": "risk",
        "question": "用户提交的事实不完整会导致什么问题？",
        "reference_answer": "文书草稿会出现大量待补充字段，对策是强制事实字段必填校验。",
        "expected_chunk_keywords": ["大量待补充字段", "强制事实字段必填校验"],
        "should_refuse": False,
    },
    {
        "name": "plan_acceptance_materials",
        "document_id": 9002,
        "category": "acceptance",
        "question": "项目验收需要提交哪些材料？",
        "reference_answer": "验收材料包括操作手册、接口清单、测试报告和问题关闭记录。",
        "expected_chunk_keywords": ["操作手册", "问题关闭记录"],
        "should_refuse": False,
    },
]

# 从现有数据集原样保留的 9002 题（关键词已核对存在于正文）
KEEP_9002 = ("plan_milestone_go_live", "plan_team_composition", "plan_training_schedule")

# 9002 需要改成的拒答题：正文没有「不在交付范围内」的清单
NEW_REFUSAL = {
    "name": "refusal_plan_scope_exclusion",
    "document_id": 9002,
    "category": "refusal",
    "question": "哪些内容不在本期交付范围内？",
    "reference_answer": "",
    "expected_chunk_keywords": [],
    "should_refuse": True,
}

REWRITE_9002 = {
    "plan_target_users",
    "plan_scope_modules",
    "plan_milestone_prototype",
    "plan_risk_latency",
    "plan_risk_permission",
    "plan_scope_exclusion",
}

# 措辞漂移：标签串与正文不一致（正文原串见注释）
KEYWORD_FIXES: dict[str, dict] = {
    "contract_scope_modules": {
        "question": "合同服务范围包括哪些内容？",
        "reference_answer": "包括合同文档解析与条款结构化抽取、法律法规检索与引用展示、合同条款风险识别与修改建议、法律文书草稿生成与事实校验。",
        "expected_chunk_keywords": [
            "合同文档解析与条款结构化抽取",
            "法律法规检索与引用展示",
            "法律文书草稿生成与事实校验",
        ],
    },
    "vendor_delivery_history": {
        "question": "近两年累计交付了多少个法律AI应用项目，其中多少个按期上线？",
        "reference_answer": "累计交付 12 个法律AI应用项目，其中 9 个按期上线。",
        "expected_chunk_keywords": ["12 个法律AI应用项目", "9 个按期上线"],
    },
    "vendor_overall_conclusion": {
        "question": "报告的总体结论是什么？",
        "reference_answer": "总体看，该供应商具备法律AI项目交付经验，但现金流稳定性和测试环境安全治理仍需重点关注。",
        "expected_chunk_keywords": ["具备法律AI项目交付经验", "现金流稳定性", "测试环境安全治理"],
    },
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="eval/qa_dataset.json")
    parser.add_argument("--manifest", default="eval/corpus_manifest.json")
    parser.add_argument("--dataset-out", default=None, help="写入路径（默认原地；子进程写不进 eval/ 时指到工作区根）")
    parser.add_argument("--manifest-out", default=None, help="同上")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    dataset_path = ROOT / args.dataset
    cases = json.loads(dataset_path.read_text(encoding="utf-8"))

    answerable_9001 = [c for c in cases if c["document_id"] == 9001 and not c["should_refuse"]]
    kept = [c for c in cases if c["document_id"] == 9002 and c["name"] in KEEP_9002]
    answerable_9003 = [c for c in cases if c["document_id"] == 9003 and not c["should_refuse"]]
    refusals = [c for c in cases if c["should_refuse"]]

    assert len(kept) == len(KEEP_9002), f"保留题缺失: {[c['name'] for c in kept]}"
    kept_by_name = {c["name"]: c for c in kept}

    new_9002: list[dict] = list(NEW_9002)
    new_9002.insert(3, kept_by_name["plan_milestone_go_live"])
    new_9002.append(kept_by_name["plan_team_composition"])
    new_9002.append(kept_by_name["plan_training_schedule"])

    if NEW_REFUSAL["name"] not in {c["name"] for c in refusals}:
        refusals.append(NEW_REFUSAL)

    merged = answerable_9001 + new_9002 + answerable_9003 + refusals
    for case in merged:
        case["document_name"] = DOC_TITLES[int(case["document_id"])]
        fix = KEYWORD_FIXES.get(case["name"])
        if fix:
            case.update(fix)

    answerable = sum(1 for c in merged if not c["should_refuse"])
    print(
        f"cases {len(cases)} -> {len(merged)}; 可答 {answerable}; 拒答 {len(merged) - answerable}; "
        f"9002 题数 {sum(1 for c in cases if c['document_id'] == 9002)} -> {len(new_9002)}"
    )

    if args.dry_run:
        return 0

    dataset_out = ROOT / (args.dataset_out or args.dataset)
    dataset_out.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest_path = ROOT / args.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in manifest:
        entry["document_name"] = DOC_TITLES[int(entry["document_id"])]
    manifest_out = ROOT / (args.manifest_out or args.manifest)
    manifest_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"written {dataset_out.relative_to(ROOT)} and {manifest_out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
