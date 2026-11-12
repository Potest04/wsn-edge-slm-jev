from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from edge import RAW, SUMMARY, Reading, RawCache, jev_action, pack_summary, replay
from semantic import LocalScorer


def row(seq, t, temperature=23, arrival=None, node=2):
    return Reading(node, seq, t, t + 0.1 if arrival is None else arrival,
                   temperature, 57, "lab")


class EdgeTests(unittest.TestCase):
    def test_cache_expiry_and_hard_alert_priority(self):
        cache = RawCache(capacity=1, ttl=5, priority=True)
        cache.add(row(1, 1, 34), 1.1)
        cache.add(row(2, 2, 23), 2.1)
        self.assertEqual(set(cache.records), {(2, 1)})
        self.assertEqual(cache.peak, 1)
        cache.expire(6.1)
        self.assertFalse(cache.records)

    def test_summary_keeps_weighted_statistics(self):
        data = [row(1, 3, 20), row(2, 5, 23), row(3, 6, 27)]
        fields = SUMMARY.unpack(pack_summary(data, "lab", 0, 1))
        self.assertEqual(fields[5:10], (6_000_000, 3, 7000, 2000, 2700))

    def test_fixed_preserves_alert_separately_and_reports_exact_mean(self):
        values = [row(1, 8), row(2, 10, 34), row(3, 15)]
        fixed, _ = replay(values, "fixed")
        self.assertEqual(fixed["alert_on_time"], 1)
        self.assertEqual(fixed["uplink_bytes"], RAW.size + SUMMARY.size + 2 * 48)
        self.assertEqual(fixed["cloud_temp_mae_c"], 0)
        self.assertEqual(fixed["cloud_count_coverage"], 1)

    def test_late_normal_record_excluded_and_late_alert_still_forwarded(self):
        values = [row(1, 4), row(2, 6, arrival=30), row(3, 10, 34, 28)]
        fixed, _ = replay(values, "fixed")
        self.assertEqual(fixed["late_ingress"], 2)
        self.assertEqual(fixed["alert_received"], 1)
        self.assertEqual(fixed["alert_on_time"], 0)
        self.assertEqual(fixed["cloud_count_coverage"], 1 / 3)

    def test_jev_cache_summary_and_forward_are_reachable(self):
        self.assertEqual(jev_action(0, 1, 1), "cache")
        self.assertEqual(jev_action(0, 12, 12), "summary")
        self.assertEqual(jev_action(2, 6, 7), "forward")

    def test_model_failure_reuses_heuristic_and_does_not_hold_hard_alert(self):
        class OfflineScorer:
            def score(self, case_id, features):
                return {"status": "fallback", "latency_ms": 4000}

        rows = [row(1, 11, 33), row(2, 15)]
        actual, decisions = replay(rows, "jev-all", scorer=OfflineScorer())
        numeric, expected = replay(rows, "jev-heuristic")
        self.assertEqual(decisions[0]["value"], expected[0]["value"])
        self.assertEqual(decisions[0]["action"], expected[0]["action"])
        self.assertEqual(actual["alert_on_time"], numeric["alert_on_time"])
        self.assertEqual(actual["slm_fallbacks"], 1)

    def test_local_scorer_audits_transport_failure(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as folder:
            audit = Path(folder) / "audit.jsonl"
            with patch("semantic.urlopen", side_effect=URLError("offline")) as mock:
                score = LocalScorer(8091, audit)
                result = score.score("sample", {"zone": "lab"})
                self.assertEqual(result["status"], "fallback")
                self.assertEqual(score.score("sample", {"zone": "lab"}), result)
                self.assertEqual(mock.call_count, 1)
            self.assertTrue(audit.read_text().endswith("\n"))


if __name__ == "__main__":
    unittest.main()
