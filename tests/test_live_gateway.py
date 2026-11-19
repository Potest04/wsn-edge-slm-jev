import asyncio
from io import StringIO
import json
from threading import Event
import unittest
from unittest.mock import patch

from edge import RAW, SUMMARY
from live_gateway import END, LiveGateway


class BlockingScorer:
    def __init__(self):
        self.started = Event()
        self.release = Event()

    def score(self, case, features):
        self.started.set()
        if not self.release.wait(4):
            raise TimeoutError("Test did not release inference")
        return {"status": "fallback", "latency_ms": 0}


class LiveGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_alert_passes_a_blocked_scorer_and_fallback_releases_summary(self):
        packets = asyncio.Queue()
        reader = asyncio.StreamReader()
        scorer = BlockingScorer()
        audit = StringIO()
        with patch("live_gateway.socket.socket") as udp:
            udp.return_value.sendto.side_effect = lambda data, address: packets.put_nowait(data)
            gateway = LiveGateway("jev-gated", 9001, audit, scorer)
        task = asyncio.create_task(gateway.run(reader))
        try:
            for seq in range(8):
                sample = 100_000 * seq
                reader.feed_data(
                    (f"EVENT,1000,{sample} ID:2 WSN,GEN,2,{seq},0,2730,5600\n"
                     f"EVENT,1001,{sample + 1000} ID:1 WSN,RECV,2,{seq},0,2730,5600\n").encode()
                )
            reader.feed_data(b"CLOSE,2000,2100000\n")
            self.assertTrue(await asyncio.to_thread(scorer.started.wait, 2))
            reader.feed_data(
                b"EVENT,3300,2300000 ID:2 WSN,GEN,2,8,2,3300,5600\n"
                b"EVENT,3301,2301000 ID:1 WSN,RECV,2,8,2,3300,5600\n"
                b"CLOSE,4000,4100000\nDONE\n"
            )
            payload = await asyncio.wait_for(packets.get(), 1)
            self.assertEqual(RAW.unpack(payload)[1:3], (2, 8))
            self.assertFalse(scorer.release.is_set())
            scorer.release.set()
            payload = await asyncio.wait_for(packets.get(), 1)
            self.assertEqual(SUMMARY.unpack(payload)[6], 8)
            self.assertEqual(await asyncio.wait_for(packets.get(), 1), END)
            await asyncio.wait_for(task, 1)
            decisions = [r for r in map(json.loads, audit.getvalue().splitlines()) if r["event"] == "decision"]
            self.assertIn("fallback", {r["source"] for r in decisions})
        finally:
            scorer.release.set()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
