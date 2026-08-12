import unittest

from app.services.agent_harness_service import get_harness_profile
from app.services.agent_skill_registry import get_agent_skill, list_agent_skills, resolve_agent_skill


class AgentSkillRegistryTests(unittest.TestCase):
    def test_legal_contract_goal_selects_contract_review_skill(self):
        skill = resolve_agent_skill("请审查这份技术服务合同的付款和违约条款")
        self.assertIsNotNone(skill)
        self.assertEqual(skill["skill_id"], "contract_review")
        self.assertEqual(skill["worker_plan"], ("legal_compliance_agent",))
        self.assertTrue(skill["evidence_required"])

    def test_registry_returns_copies_and_known_skill(self):
        skills = list_agent_skills()
        self.assertEqual(len(skills), 4)
        skills[0]["name"] = "changed"
        self.assertNotEqual(get_agent_skill("legal_consultation")["name"], "changed")

    def test_harness_profile_declares_enforced_controls(self):
        profile = get_harness_profile()
        self.assertEqual(profile["harness_id"], "controlled_agent_harness")
        self.assertIn("role_scoped_mcp_acl", profile["controls"])
        self.assertIn("approval_for_side_effects", profile["controls"])


if __name__ == "__main__":
    unittest.main()
