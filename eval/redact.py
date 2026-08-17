"""评测 PII 脱敏与检测（阶段 4 评估集治理）。

规则（覆盖姓名/证件/联系方式/金额/律所案号等中文语料常见 PII）：
- 中文人名占位（张三/李四/王五/赵六/孙七/周八/吴九/郑十）→【当事人】
- 手机号 / 座机（区号）→【联系电话】
- 身份证号（18 位）→【证件号】
- 银行卡号（16-19 位数字）→【卡号】
- 金额（¥/￥/元/万 等上下文）→【金额】
- 邮箱 →【邮箱】
- QQ / 微信（可选）→【联系方式】
- 律所名/案号（（20xx）xx号 / （20xx）民初 * 号 案号模式）→【案号】

校验：``detect_pii`` 返回命中列表，供「脱敏前后校验」脚本与测试断言。
"""

from __future__ import annotations

import re

CN_NAME = re.compile(r"(?<=[\u4e00-\u9fff])(张三|李四|王五|赵六|孙七|周八|吴九|郑十)(?=[\u4e00-\u9fff，。；、\s]|$)")
PHONE_CN = re.compile(r"(?<!\d)(?:\+?86[- ]?)?(?:1[3-9]\d{9}|0\d{2,3}[- ]?\d{7,8})(?!\d)")
ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
BANK_CARD = re.compile(r"(?<!\d)\d{16,19}(?!\d)")
AMOUNT = re.compile(r"(?:[¥￥]\s?\d[\d,.]*|\d[\d,.]*\s*(?:元|人民币|万|亿))")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CASE_NO = re.compile(r"\((?:19|20)\d{2}\)[^，。；、\s]{1,12}\d{1,6}号|\d{1,6}号")
LAW_FIRM = re.compile(r"[\u4e00-\u9fff]{2,12}(?:律师事务所|律所)")
TAX_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z0-9]{15,18}(?![A-Za-z0-9])")


def redact_pii(text: str) -> str:
    """按规则脱敏；未命中规则的中文名保留（仅替换预设占位名，避免误伤正文）。"""
    if not text:
        return text
    out = text
    # 金额先于卡号（防止金额正则吞掉卡号上下文）
    out = AMOUNT.sub("【金额】", out)
    out = ID_CARD.sub("【证件号】", out)
    out = BANK_CARD.sub("【卡号】", out)
    out = PHONE_CN.sub("【联系电话】", out)
    out = EMAIL.sub("【邮箱】", out)
    out = CASE_NO.sub("【案号】", out)
    out = LAW_FIRM.sub("【律师事务所】", out)
    out = CN_NAME.sub("【当事人】", out)
    return out


def detect_pii(text: str) -> list[str]:
    """检测文本中残留的 PII（脱敏校验用）。返回命中规则名列表。"""
    hits: list[str] = []
    if not text:
        return hits
    probes = (
        ("phone", PHONE_CN),
        ("id_card", ID_CARD),
        ("bank_card", BANK_CARD),
        ("amount", AMOUNT),
        ("email", EMAIL),
        ("case_no", CASE_NO),
    )
    for name, pattern in probes:
        if pattern.search(text):
            hits.append(name)
    return hits
