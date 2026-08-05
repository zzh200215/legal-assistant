"""AI-1 v3 模拟真实语料冻结（不进库）：造 ≥30 条模拟试点语料，离线生成 v2.2 候选数据集。

模拟"试点第 2-3 周真实语料"扩充（对齐 export_real_corpus_eval 的 real_cases 结构），
作为 CI 换题集的 v3 候选。输出到 data/sim/，不污染正式 eval/generation_eval_dataset.json。

用法:
    python -B scripts/simulate_real_corpus_eval.py
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 模拟咨询（15 条，覆盖 5 类）─────────────────────────────────────────────
# (id, question, category, missing_facts, risk_min)
CONSULTATIONS = [
    ("sim-co-0001", "公司拖欠我三个月工资，且未签订劳动合同，现在提出离职，能要求公司支付经济补偿金和双倍工资差额吗？", "labor_dispute", True, "medium"),
    ("sim-co-0002", "公司以岗位调整为由调岗降薪，我不同意，公司说拒不服从安排视为旷工。这种情况我可以主张解除劳动合同并要补偿吗？", "labor_dispute", True, "medium"),
    ("sim-co-0003", "我因工受伤住院治疗，公司没有给我缴社保。工伤认定需要准备哪些材料？", "labor_dispute", False, "medium"),
    ("sim-co-0004", "怀孕期间公司以绩效不达标为由辞退我，合法吗？", "labor_dispute", False, "medium"),
    ("sim-co-0005", "我们与装修公司签了合同，约定工期90天，现已超期3个月，且装修质量存在多处问题。可以解除合同并要求赔偿吗？", "contract_dispute", True, "medium"),
    ("sim-co-0006", "供应商提供的货物与合同约定的规格不符，我拒收后对方要求我支付货款。如何应对？", "contract_dispute", False, "medium"),
    ("sim-co-0007", "房屋买卖合同签订后卖方反悔不卖，只愿意退定金。我可以主张继续履行吗？", "contract_dispute", False, "low"),
    ("sim-co-0008", "我们给客户做了软件开发，项目交付后客户以未验收为由拒付尾款。合同里验收条款没有写明。能追索尾款吗？", "contract_dispute", True, "medium"),
    ("sim-co-0009", "朋友向我借款10万元未按期归还，借条写明了借款日期和利率。起诉需要哪些证据？", "private_lending", False, "medium"),
    ("sim-co-0010", "他人以我的名义在网贷平台借款，我并未知情。是否需要承担还款责任？", "private_lending", False, "low"),
    ("sim-co-0011", "我出借了款项给同事，没有写借条，只有微信转账记录。现在对方不认账，能要回吗？", "private_lending", True, "low"),
    ("sim-co-0012", "网购的商品与宣传严重不符，商家拒绝退货。七日无理由退货是否适用？", "consumer_dispute", False, "low"),
    ("sim-co-0013", "我在健身房办了两年的消费年卡，办卡三个月后健身房关门停业。剩余费用能退回吗？", "consumer_dispute", True, "medium"),
    ("sim-co-0014", "邻居装修噪音扰民，多次沟通无效，可以起诉要求停止侵害吗？", "other", False, "low"),
    ("sim-co-0015", "我停车时车辆被旁边商户安装的地桩刮伤，监控拍不清车牌。可以向商户索赔吗？", "other", True, "medium"),
]

# ── 模拟合同审查（8 条，structural_only）───────────────────────────────────
# (id, description, contract_text)
CONTRACTS = [
    ("sim-cr-0001", "simulated pilot contract review #1 (房屋租赁合同)",
     "房屋租赁合同\n甲方将位于本市幸福路18号房屋出租给乙方居住，租期两年，月租金4800元，押一付三。"
     "合同未约定维修责任，仅写明房屋自然损耗由甲方负责。"
     "双方未约定违约金的计算方式，只写违约方应承担违约责任。"),
    ("sim-cr-0002", "simulated pilot contract review #2 (货物买卖合同)",
     "货物买卖合同\n甲方向乙方采购办公设备一批，总价32万元。签约后5日内甲方支付30%定金，货到验收合格后支付尾款。"
     "乙方应于合同签订后30日内交货，逾期每日按总价0.5%支付违约金。"
     "合同未约定验收标准与验收时限。"),
    ("sim-cr-0003", "simulated pilot contract review #3 (软件服务合同)",
     "软件服务合同\n乙方为甲方开发进销存管理系统，开发费用28万元，分三期支付。"
     "合同约定了上线时间，但未约定验收流程、上线标准及乙方延期交付的违约责任。"),
    ("sim-cr-0004", "simulated pilot contract review #4 (劳务分包合同)",
     "劳务分包合同\n发包方将某项目土建工程劳务分包给分包方，工程量按实结算。"
     "合同约定工期120天，逾期按日扣除工程款。未约定安全事故的责任划分与工伤处理方式。"),
    ("sim-cr-0005", "simulated pilot contract review #5 (民间借贷借条)",
     "借条\n今借到李某人民币壹拾伍万元整，约定年利率15%，借款期限一年，到期一次性还本付息。"
     "借款人以其名下车辆作抵押，未办理抵押登记。未约定逾期还款的违约责任。"),
    ("sim-cr-0006", "simulated pilot contract review #6 (经销授权合同)",
     "经销授权合同\n甲方授权乙方在华东区域独家经销其品牌产品，授权期两年。"
     "合同约定了年度销售指标，但未约定未达标的处理方式，也未约定竞业限制。"),
    ("sim-cr-0007", "simulated pilot contract review #7 (装饰装修合同)",
     "装饰装修合同\n装修公司承接家庭装修工程，合同总价18万元，约定工期75天。"
     "工程款按开工、水电完工、竣工验收分四期支付。合同未约定增项变更的计价方式与书面确认流程。"),
    ("sim-cr-0008", "simulated pilot contract review #8 (股权转让协议)",
     "股权转让协议\n转让方将其持有的某公司40%股权转让给受让方，转让价款200万元，分两期支付。"
     "协议约定了交割时间，但未约定股权转让涉及的税务承担与员工安置事宜。"),
]

# ── 模拟文书草稿（7 条）─────────────────────────────────────────────────────
DRAFT_REQUIRED = {
    "labor_arbitration_application": ["申请人", "被申请人", "仲裁请求", "事实与理由", "证据清单"],
    "private_lending_complaint": ["原告", "被告", "借款金额", "借款日期", "诉讼请求", "事实与理由", "证据清单"],
    "consumer_complaint": ["投诉人", "被投诉企业", "投诉请求", "事实与理由"],
    "supplementary_agreement": ["甲方", "乙方", "补充事项", "生效日期"],
}
# (id, document_type, fields)
DRAFTS = [
    ("sim-dg-0001", "labor_arbitration_application",
     {"申请人": "张三", "被申请人": "某科技有限公司", "劳动关系起止时间": "2023-03至2026-05",
      "仲裁请求": "支付拖欠工资3万元及经济补偿金1.5万元", "事实与理由": "公司自2026年3月起拖欠工资且违法解除劳动合同",
      "证据清单": "劳动合同、工资银行流水、解除通知"}),
    ("sim-dg-0002", "labor_arbitration_application",
     {"申请人": "李四", "被申请人": "某餐饮管理公司", "劳动关系起止时间": "2024-07至2026-06",
      "仲裁请求": "支付加班费2.4万元", "事实与理由": "在职期间长期超时加班，公司未支付加班费",
      "证据清单": "排班表、考勤记录、工资条"}),
    ("sim-dg-0003", "private_lending_complaint",
     {"原告": "王五", "被告": "赵六", "借款金额": "15万元", "借款日期": "2025-01-15",
      "诉讼请求": "判令被告偿还借款15万元及利息", "事实与理由": "借款到期后被告多次拖延拒不还款",
      "证据清单": "借条、银行转账凭证、催收记录"}),
    ("sim-dg-0004", "private_lending_complaint",
     {"原告": "钱七", "被告": "孙八", "借款金额": "8万元", "借款日期": "2025-06-20",
      "诉讼请求": "判令被告偿还借款8万元并支付逾期利息", "事实与理由": "双方口头约定三个月后还款，逾期未还",
      "证据清单": "微信转账记录、聊天记录"}),
    ("sim-dg-0005", "consumer_complaint",
     {"投诉人": "周九", "被投诉企业": "某健身俱乐部有限公司", "购买商品或服务": "两年期健身年卡",
      "消费金额与日期": "2025-09-01，金额6800元", "投诉请求": "退回剩余预付款",
      "事实与理由": "办卡三个月后门店停业，无法继续提供服务",
      "证据清单": "购卡合同、付款凭证、门店停业公告"}),
    ("sim-dg-0006", "supplementary_agreement",
     {"甲方": "某供应链有限公司", "乙方": "某贸易有限公司", "原协议名称": "《货物购销框架协议》",
      "补充事项": "将原协议付款方式由月结改为货到验收合格后30日内支付，逾期按日万分之三计息",
      "生效日期": "2026-08-01", "签署地点": "上海"}),
    ("sim-dg-0007", "supplementary_agreement",
     {"甲方": "某建设单位", "乙方": "某劳务分包公司", "原协议名称": "《劳务分包合同》",
      "补充事项": "增加工期顺延条件：因甲方原因停工连续超过7日，工期相应顺延且乙方可主张窝工损失",
      "生效日期": "2026-08-05", "签署地点": "杭州"}),
]


def build_cases() -> dict:
    consultation_cases = [
        {
            "id": cid,
            "question": q,
            "source": "simulated pilot corpus",
            "gold": {
                "expected_category": cat,
                "citation_must_match_patterns": [],
                "must_not_fabricate_winrate": True,
                "must_have_missing_facts": missing,
                "risk_level_min": risk,
            },
        }
        for cid, q, cat, missing, risk in CONSULTATIONS
    ]
    contract_review_cases = [
        {
            "id": cid,
            "category": "regression",
            "description": desc,
            "contract_text": text,
            "gold": {"structural_only": True, "must_not_fabricate_entities": []},
        }
        for cid, desc, text in CONTRACTS
    ]
    draft_generation_cases = [
        {
            "id": cid,
            "document_type": dtype,
            "category": "complete",
            "description": f"simulated pilot draft #{idx}",
            "fields": fields,
            "missing_fields": [],
            "gold": {
                "required_fields_must_appear": DRAFT_REQUIRED[dtype],
                "placeholder_fields": [],
                "must_not_fabricate": [],
                "must_contain_disclaimer": True,
            },
        }
        for idx, (cid, dtype, fields) in enumerate(DRAFTS, start=1)
    ]
    return {
        "consultation_cases": consultation_cases,
        "contract_review_cases": contract_review_cases,
        "draft_generation_cases": draft_generation_cases,
    }


def main() -> int:
    sim = build_cases()
    counts = {k: len(v) for k, v in sim.items()}

    dataset = json.loads((ROOT / "eval/generation_eval_dataset.json").read_text(encoding="utf-8"))
    dataset["version"] = "2.2-sim"
    dataset["description"] = (dataset.get("description") or "") + " [v3 模拟候选：追加 simulated pilot corpus 30 条]"
    dataset["real_corpus"] = {
        "source": "simulated pilot corpus (offline, not from DB)",
        "extracted_at": datetime.now().isoformat(timespec="seconds"),
        "counts": counts,
    }
    for section, cases in sim.items():
        existing_ids = {c["id"] for c in dataset.get(section, [])}
        dataset[section] = list(dataset.get(section, [])) + [c for c in cases if c["id"] not in existing_ids]

    out = ROOT / "data/sim/generation_eval_dataset_v2.2_sim.json"
    out.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "status": "ok", "mode": "simulated_offline", "output": str(out),
        "version": dataset["version"],
        "added": counts,
        "totals": {k: len(dataset[k]) for k in sim.keys()},
        "formal_dataset_untouched": True,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
