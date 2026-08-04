# Phase 8 生成质量评测

## 文件说明

- **generation_eval_dataset.json** - 评测数据集（3个合同审查+2个文书草稿+3个法律咨询）
- **run_generation_eval.py** - 自动化评测脚本
- **outputs/generation_eval_report.json** - 最新评测报告

## 快速开始

```bash
# 运行完整评测
python eval/run_generation_eval.py --pretty --output eval/outputs/generation_eval_report.json

# 仅查看结果摘要
python eval/run_generation_eval.py 2>&1 | tail -6
```

## 评测指标

### 合同审查 (review_contract)
- **clause_detection_f1**: 条款类型识别F1分数（precision & recall平衡）
- **missing_clause_recall**: 缺失条款检测召回率（能否发现合同中缺少的关键条款）
- **high_risk_count_ok**: 是否正确标注了足够数量的高风险条款
- **summary_has_disclaimer**: 审查意见是否包含免责声明
- **fabrication_detected**: 是否虚构了原文不存在的实体（幻觉检测）

**通过标准**: F1≥0.6, missing_recall≥0.5, high_risk_count达标, 有免责声明, 无虚构

### 文书草稿 (draft_content)
- **required_presence_rate**: 必填字段覆盖率（字段名或字段值出现在输出中）
- **placeholder_correct_rate**: 缺失字段是否正确标记【待补充】而非虚构
- **no_fabrication**: 未虚构must_not_fabricate列表中的内容
- **has_disclaimer**: 是否包含免责声明

**通过标准**: required≥0.8, placeholder≥0.8, 无虚构, 有免责声明

### 法律咨询 (consultation_payload)
- **category_correct**: 问题分类准确性
- **citation_valid**: 引用格式有效性（refs或advice中包含关键法律术语）
- **no_winrate_claim**: 未预测胜诉率/结果
- **missing_facts_ok**: 对缺失事实的处理符合预期
- **risk_level_adequate**: 风险等级评估恰当

**通过标准**: 分类正确, 引用有效, 无胜诉率预测, missing_facts处理OK, 风险等级达标

## 当前基线结果（2026-07-24）

```
总通过率: 87.5% (7/8)
  合同审查: 100.0%  F1=0.90
  文书草稿: 50.0%  必填字段=75.0%
  法律咨询: 100.0%  分类准确=100.0%
```

### Badcase分析

**dg_002** (文书草稿-缺失申请人): `required_presence_rate=0.5`
- **原因**: LLM将简短字段值"支付欠薪"展开改写为"要求支付拖欠工资"，字面匹配失败
- **影响**: 不影响实际文书质量（核心信息仍在），但eval metric需改进为语义匹配
- **状态**: 已知限制，后续可通过更灵活的字段匹配策略改进

## 评测集设计原则

1. **合同审查**: 合成业务合同片段（非法律条文），覆盖完整/缺失/高风险/极简等场景
2. **文书草稿**: 4类文书×2场景（完整填写/缺失关键字段），测试字段覆盖和虚构检测
3. **法律咨询**: 覆盖5大分类（劳动/合同/借贷/消费/其他），测试分类、引用、风险评估

## 扩展评测集

要添加新的评测用例：

1. 编辑 `generation_eval_dataset.json`
2. 在对应的数组中添加新case（遵循现有schema）
3. 运行评测查看结果

**注意**: 合同文本应为合成内容，不要使用真实合同；法律咨询问题应脱敏。

## 后续改进方向

- [ ] 扩展评测集至30+用例（Phase 8 Week 1目标：30份合同+20份文书+50份咨询）
- [ ] 引入LLM-as-judge评估文书质量（补充字面匹配的不足）
- [ ] 添加hallucination detection（法条编号/案例引用真实性验证）
- [ ] 建立回归测试CI流程（每次修改prompt/模型后自动跑评测）
