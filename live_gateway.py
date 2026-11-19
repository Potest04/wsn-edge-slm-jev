"""Live collector ingress, bounded gateway decisions, and IPv6 UDP receiver."""

import argparse
import asyncio
from collections import defaultdict
from dataclasses import dataclass
import json
from pathlib import Path
import re
import socket
import time

from cooja import MARKER
from edge import RAW, SUMMARY, Decision, RawCache, Reading, ambiguous, choose, pack_summary, window_features


WINDOW_MS = 2000
MAX_LINE_BYTES = 512
END = b"\x00"
POLICIES = ("fixed", "jev-heuristic", "jev-gated")
CONTEXT_POLICIES = ("structured-context", "keyword-context", "slm-context")


@dataclass(frozen=True)
class Window:
    start_ms: int
    zone: str
    closed_ns: int
    cutoff_us: int
    readings: list[Reading]


def log(handle, event, **fields):
    handle.write(json.dumps({"event": event, "monotonic_ns": time.monotonic_ns(), **fields},
                            separators=(",", ":")) + "\n")


class LiveGateway:
    def __init__(self, policy, receiver_port, audit, scorer=None, profile=None, effective_ms=0):
        if policy == "jev-gated" and scorer is None:
            raise ValueError("Gated JEV requires a model scorer")
        self.policy = policy
        self.receiver_port = receiver_port
        self.audit = audit
        self.scorer = scorer
        self.profile = profile
        self.effective_ms = effective_ms
        self.udp = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        self.cache = RawCache(capacity=20, ttl=75, priority=policy.startswith("jev-") or policy in CONTEXT_POLICIES)
        self.windows = defaultdict(list)
        self.generated = {}
        self.received = set()
        self.closed_through = 0
        self.inference_jobs = asyncio.Queue(maxsize=4)

    def emit(self, payload, kind, **fields):
        self.udp.sendto(payload, ("::1", self.receiver_port))
        log(self.audit, "send", kind=kind, payload=payload.hex(), **fields)

    def read_event(self, line):
        _, wall_ms, marker = line.split(",", 2)
        match = MARKER.fullmatch(marker)
        if match is None:
            raise ValueError(f"Invalid collector event {marker!r}")
        sim_us, mote, event, fields = match.groups()
        node, seq, source_seconds, temperature, humidity = map(int, fields.split(","))
        key, sim_us, wall_ms = (node, seq), int(sim_us), int(wall_ms)
        reading_values = source_seconds, temperature, humidity
        if event == "GEN":
            if key in self.generated or int(mote) != node:
                raise ValueError(f"Inconsistent generation event for {key}")
            self.generated[key] = sim_us, wall_ms, reading_values
            log(self.audit, "generated", node=node, seq=seq, sim_us=sim_us,
                source_wall_ms=wall_ms, temp_centi=temperature)
            return
        if event != "RECV" or int(mote) != 1 or key not in self.generated:
            raise ValueError(f"Inconsistent receive event for {key}")
        sample_us, source_wall_ms, generated_values = self.generated[key]
        if reading_values != generated_values or sim_us < sample_us:
            raise ValueError(f"Collector payload or time differs for {key}")
        if key in self.received:
            log(self.audit, "duplicate", node=node, seq=seq)
            return
        self.received.add(key)
        zone = "lab" if node % 2 == 0 else "hall"
        reading = Reading(node, seq, sample_us / 1e6, sim_us / 1e6,
                          temperature / 100, humidity / 100, zone)
        start_ms = sample_us // (1000 * WINDOW_MS) * WINDOW_MS
        ingress_ns = time.monotonic_ns()
        log(self.audit, "ingress", node=node, seq=seq, hard=reading.hard_alert,
            sample_us=sample_us, collector_us=sim_us, source_wall_ms=source_wall_ms,
            collector_wall_ms=wall_ms, start_ms=start_ms, zone=zone, ingress_ns=ingress_ns)
        if reading.hard_alert:
            self.emit(reading.encode(), "alert", node=node, seq=seq)
        elif self.policy == "raw":
            self.emit(reading.encode(), "raw", node=node, seq=seq)
        if self.policy == "raw":
            return
        self.cache.add(reading, reading.arrival_s)
        if start_ms >= self.closed_through:
            self.windows[(start_ms, zone)].append(reading)
        else:
            log(self.audit, "late", node=node, seq=seq, hard=reading.hard_alert)

    def apply(self, window, decision):
        log(self.audit, "decision", start_ms=window.start_ms, zone=window.zone,
            closed_ns=window.closed_ns, action=decision.action, source=decision.source,
            value=decision.value, latency_ms=decision.latency_ms)
        routine = [r for r in window.readings if not r.hard_alert]
        if decision.action == "summary":
            payload = pack_summary(routine, window.zone, window.start_ms / 1000, decision.value)
            self.emit(payload, "summary", start_ms=window.start_ms, zone=window.zone,
                      count=len(routine), closed_ns=window.closed_ns, cutoff_us=window.cutoff_us)
        elif decision.action == "forward":
            for reading in routine:
                self.emit(reading.encode(), "raw", node=reading.node, seq=reading.seq,
                          start_ms=window.start_ms, zone=window.zone, closed_ns=window.closed_ns)

    def decide(self, window, features, policy):
        count = sum(not r.hard_alert for r in window.readings)
        case = f"live/{window.start_ms}/{window.zone}"
        if policy in CONTEXT_POLICIES:
            profile = self.profile
            if (count and profile and window.zone == profile.zone and
                    self.effective_ms <= window.start_ms < self.effective_ms + 1000 * profile.duration_s):
                # The operator's validated retention requirement constrains this window's action.
                action, value = ("forward", 1) if profile.action == "raw" else ("summary", 0)
                return Decision(action, value, policy, 0.0)
            policy = "jev-heuristic"
        return choose(policy, features, None, count, self.scorer, case, 0.18)

    async def inference_worker(self):
        while True:
            window, features = await self.inference_jobs.get()
            try:
                started_ns = time.monotonic_ns()
                result = await asyncio.to_thread(self.decide, window, features, self.policy)
                log(self.audit, "inference", start_ms=window.start_ms, zone=window.zone,
                    started_ns=started_ns, finished_ns=time.monotonic_ns(), source=result.source)
                self.apply(window, result)
            finally:
                self.inference_jobs.task_done()

    def close_windows(self, boundary, cutoff_us):
        if boundary <= self.closed_through or boundary % WINDOW_MS or cutoff_us < boundary * 1000:
            raise ValueError(f"Invalid window boundary {boundary}")
        self.closed_through = boundary
        for key in sorted(key for key in self.windows if key[0] < boundary):
            window = Window(*key, time.monotonic_ns(), cutoff_us, self.windows.pop(key))
            features = window_features(window.readings, window.zone)
            log(self.audit, "window", start_ms=window.start_ms, zone=window.zone,
                closed_ns=window.closed_ns, cutoff_us=cutoff_us,
                routine=sum(not r.hard_alert for r in window.readings))
            policy = self.policy
            if policy == "jev-gated" and ambiguous(features) and any(
                not r.hard_alert for r in window.readings
            ):
                try:
                    self.inference_jobs.put_nowait((window, features))
                except asyncio.QueueFull:
                    log(self.audit, "queue_fallback", start_ms=window.start_ms, zone=window.zone)
                    policy = "jev-heuristic"
                else:
                    continue
            self.apply(window, self.decide(window, features, policy))

    async def ingest(self, reader):
        while raw := await reader.readline():
            line = raw.decode("ascii").rstrip("\r\n")
            if line.startswith("EVENT,"):
                self.read_event(line)
            elif line.startswith("CLOSE,"):
                _, boundary, cutoff = line.split(",")
                self.close_windows(int(boundary), int(cutoff))
            elif line == "DONE" and not self.windows:
                return
            else:
                raise ValueError(f"Unexpected bridge message {line!r}")
        raise EOFError("Cooja disconnected before sending DONE")

    async def run(self, reader):
        """Propagate ingress or worker errors and drain completed windows before shutdown."""
        worker = asyncio.create_task(self.inference_worker())
        ingress = asyncio.create_task(self.ingest(reader))
        pending = {worker, ingress}
        try:
            await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            if worker.done():
                worker.result()
            ingress.result()
            drain = asyncio.create_task(self.inference_jobs.join())
            pending.add(drain)
            await asyncio.wait({worker, drain}, return_when=asyncio.FIRST_COMPLETED)
            if worker.done():
                worker.result()
            drain.result()
            self.udp.sendto(END, ("::1", self.receiver_port))
        finally:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            self.udp.close()


def receive(output):
    """Run the upstream receiver independently of Cooja and the gateway."""
    with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as udp, output.open(
        "x", encoding="utf-8", buffering=1
    ) as audit:
        udp.bind(("::1", 0))
        print(udp.getsockname()[1], flush=True)
        udp.settimeout(60)
        source = None
        while True:
            payload, sender = udp.recvfrom(256)
            receipt_ns, receipt_wall_ns = time.monotonic_ns(), time.time_ns()
            if source is None:
                source = sender
            if sender != source:
                raise ValueError("Receiver got an unexpected second sender")
            if payload == END:
                break
            if payload[:1] == b"\x01" and len(payload) == RAW.size:
                _, node, seq, sample_us, temp, humid = RAW.unpack(payload)
                record = dict(kind="raw", node=node, seq=seq, sample_us=sample_us,
                              temp_centi=temp, humidity_centi=humid)
            elif payload[:1] == b"\x02" and len(payload) == SUMMARY.size:
                _, zone, value, _, start_us, newest_us, count, tsum, tmin, tmax, hsum, hmin, hmax = SUMMARY.unpack(payload)
                record = dict(kind="summary", zone_id=zone, value=value, start_us=start_us,
                              newest_us=newest_us, count=count, temp_sum=tsum, temp_min=tmin,
                              temp_max=tmax, humidity_sum=hsum, humidity_min=hmin, humidity_max=hmax)
            else:
                raise ValueError(f"Unrecognized upstream packet {payload.hex()}")
            log(audit, "received", receipt_ns=receipt_ns, receipt_wall_ns=receipt_wall_ns,
                payload=payload.hex(), bytes=len(payload), **record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=receive.__doc__)
    parser.add_argument("output", type=Path)
    receive(parser.parse_args().output)
