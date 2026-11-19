from io import StringIO
import unittest
from unittest.mock import patch

from edge import Reading, window_features
from live_gateway import LiveGateway, Window
from operator_context import Profile, keyword_profile, parse_profile


class ProfileTests(unittest.TestCase):
    def test_rejects_unsupported_or_mixed_profile(self):
        for data in (
            {"zone": "lab", "action": "raw", "seconds": 60},
            {"zone": "none", "action": "raw", "seconds": 0},
            {"zone": "lab", "action": "raw", "seconds": 6.0},
            {"zone": "lab", "action": "raw", "seconds": 6, "extra": True},
        ):
            with self.subTest(data=data), self.assertRaises(ValueError):
                parse_profile(data)
        self.assertIsNone(parse_profile({"zone": "none", "action": "none", "seconds": 0}))
        self.assertIsNone(keyword_profile("Please keep the hall's individual readings."))

    def test_profile_applies_only_to_its_zone_and_before_expiry(self):
        with patch("live_gateway.socket.socket"):
            gateway = LiveGateway("structured-context", 9001, StringIO(),
                                  profile=Profile("lab", "raw", 6), effective_ms=80_000)
        for start, zone, source in (
            (78_000, "lab", "heuristic"), (80_000, "hall", "heuristic"),
            (80_000, "lab", "structured-context"), (84_000, "lab", "structured-context"),
            (86_000, "lab", "heuristic"),
        ):
            with self.subTest(start=start, zone=zone):
                readings = [Reading(2, 4, start / 1000, start / 1000, 27, 56, zone)]
                window = Window(start, zone, 0, 0, readings)
                result = gateway.decide(window, window_features(readings, zone), gateway.policy)
                self.assertEqual(result.source, source)
                if source == "structured-context":
                    self.assertEqual(result.action, "forward")
