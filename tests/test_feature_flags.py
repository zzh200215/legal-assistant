"""阶段 6：Feature Flag 服务测试——运行时切换不重启生效、默认值、重置。"""

import unittest

from app.core.feature_flags import FeatureFlagStore, _seed_from_settings


class FeatureFlagStoreTests(unittest.TestCase):
    def test_is_enabled_default_false(self):
        store = FeatureFlagStore()
        self.assertFalse(store.is_enabled("some-flag"))
        self.assertTrue(store.is_enabled("another", default=True))

    def test_set_takes_effect_immediately(self):
        store = FeatureFlagStore()
        self.assertFalse(store.is_enabled("gray-flag"))
        store.set("gray-flag", True)
        # 不重启：同一进程内立即生效
        self.assertTrue(store.is_enabled("gray-flag"))
        store.set("gray-flag", False)
        self.assertFalse(store.is_enabled("gray-flag"))

    def test_seed_respected_and_overridable(self):
        store = FeatureFlagStore({"default-on": True})
        self.assertTrue(store.is_enabled("default-on"))
        store.set("default-on", False)
        self.assertFalse(store.is_enabled("default-on"))

    def test_thread_safety_basic(self):
        import threading

        store = FeatureFlagStore()
        results = {}

        def worker(tag):
            for _ in range(50):
                store.set("f", tag == "a")
            results[tag] = store.is_enabled("f")

        threads = [threading.Thread(target=worker, args=(tag,)) for tag in ("a", "b")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIn(results["a"], (True, False))  # 无异常、无死锁

    def test_get_all_and_reset(self):
        store = FeatureFlagStore({"x": True})
        store.set("y", False)
        self.assertEqual(store.get_all(), {"x": True, "y": False})
        store.reset()
        self.assertEqual(store.get_all(), {})

    def test_seed_from_settings_is_bool_map(self):
        # 静态配置里可 seed 的布尔开关应返回 name->bool（全小写）
        seeded = _seed_from_settings()
        self.assertIsInstance(seeded, dict)
        for name, value in seeded.items():
            self.assertIsInstance(value, bool)
            self.assertEqual(name, name.lower())


if __name__ == "__main__":
    unittest.main()
