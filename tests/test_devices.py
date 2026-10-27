import unittest

from devices.light import Light


class LightTests(unittest.TestCase):
    def test_starts_off(self):
        self.assertEqual(Light("light_001").state(), {"power": False})

    def test_set_power(self):
        light = Light("light_001")
        self.assertTrue(light.execute("set_power", True))
        self.assertEqual(light.state(), {"power": True})
        self.assertTrue(light.execute("set_power", False))
        self.assertEqual(light.state(), {"power": False})

    def test_rejects_unknown_action_or_bad_value(self):
        light = Light("light_001")
        self.assertFalse(light.execute("set_locked", True))
        self.assertFalse(light.execute("set_power", 1))
        self.assertFalse(light.execute("set_power", "on"))
        self.assertEqual(light.state(), {"power": False})
