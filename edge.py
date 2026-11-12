"""Deterministic replay of CoAP telemetry through the fog cache and uplink."""

from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import math
from statistics import fmean
import struct


RAW = struct.Struct("!BHIQhh")
SUMMARY = struct.Struct("!BBBBQQHihhihh")
IPV6_UDP_BYTES = 48
ZONE_ID = {"lab": 1, "hall": 2}
ALERT_C = 32.0
ALERT_DEADLINE_S = 2.0
GRACE_S = 2.0


@dataclass(frozen=True)
class Reading:
    node: int
    seq: int
    sample_s: float
    arrival_s: float | None
    temperature_c: float
    humidity_pct: float
    zone: str

    @property
    def key(self):
        return self.node, self.seq

    @property
    def hard_alert(self):
        return self.temperature_c >= ALERT_C

    def encode(self):
        return RAW.pack(1, self.node, self.seq, round(self.sample_s * 1e6),
                        round(self.temperature_c * 100), round(self.humidity_pct * 100))


@dataclass(frozen=True)
class Decision:
    action: str
    value: int
    source: str
    latency_ms: float


def load_trace(path):
    readings = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            readings.append(Reading(
                int(row["node"]), int(row["seq"]), float(row["sample_s"]),
                float(row["arrival_s"]) if row["arrival_s"] else None,
                float(row["temperature_c"]), float(row["humidity_pct"]), row["zone"],
            ))
    if len({row.key for row in readings}) != len(readings):
        raise ValueError(f"Duplicate source identities in {path}")
    for r in readings:
        if r.zone not in ZONE_ID or not all(map(math.isfinite, (r.sample_s, r.temperature_c, r.humidity_pct))):
            raise ValueError(f"Invalid telemetry in {path}: {r}")
        if r.sample_s < 0 or r.arrival_s is not None and (
            not math.isfinite(r.arrival_s) or r.arrival_s < r.sample_s
        ):
            raise ValueError(f"Invalid sample or arrival time in {path}: {r}")
    return readings


def window_features(readings, zone):
    temperatures = [r.temperature_c for r in readings]
    humidity = [r.humidity_pct for r in readings]
    return {
        "zone": zone, "count": len(readings), "sensors": len({r.node for r in readings}),
        "temp_min_c": round(min(temperatures), 2), "temp_max_c": round(max(temperatures), 2),
        "temp_mean_c": round(fmean(temperatures), 2),
        "rh_mean_pct": round(fmean(humidity), 2), "rh_max_pct": round(max(humidity), 2),
    }


def reference_value(readings, zone):
    """Application review labels from generated data, independent of transport."""
    if any(r.hard_alert for r in readings):
        return 2
    if zone == "lab":
        implicated = {r.node for r in readings if r.temperature_c >= 26 and r.humidity_pct >= 54}
        if len(implicated) >= 2 or any(r.temperature_c >= 29.5 for r in readings):
            return 1
    return int(any(r.temperature_c >= 29 and r.humidity_pct >= 58 for r in readings))


def heuristic_value(features):
    if features["temp_max_c"] >= ALERT_C:
        return 2
    return int(features["temp_max_c"] >= 28 or
               (features["zone"] == "lab" and features["temp_max_c"] >= 25.5
                and features["rh_mean_pct"] >= 52))


def ambiguous(features):
    return 25 <= features["temp_max_c"] < ALERT_C


def proxy_energy_mj(payload_bytes):
    # Nominal incremental backhaul term. No measured fog coefficient is assumed.
    return 0.16 + 0.004 * (IPV6_UDP_BYTES + payload_bytes)


def jev_action(value, routine_count, total_count, energy_weight=0.18):
    if routine_count == 0:
        return "cache"
    v = value / 2
    scores = {
        "cache": 0.36 + 0.06 * v - 0.018 * min(total_count, 20),
        "summary": 0.32 + 0.32 * v + 0.14 * min(1, total_count / 10)
        - energy_weight * proxy_energy_mj(SUMMARY.size) - 0.025,
        "forward": 0.36 + 0.92 * v
        - energy_weight * routine_count * proxy_energy_mj(RAW.size),
    }
    return max(scores, key=scores.get)


class RawCache:
    def __init__(self, capacity, ttl, priority):
        if capacity < 1 or ttl <= 0:
            raise ValueError("Cache capacity and TTL must be positive")
        self.capacity, self.ttl, self.priority = capacity, ttl, priority
        self.records = {}
        self.peak = 0
        self.evictions = 0

    def expire(self, now):
        for key, reading in list(self.records.items()):
            if now - reading.sample_s > self.ttl:
                del self.records[key]

    def add(self, reading, now):
        self.expire(now)
        if now - reading.sample_s > self.ttl:
            return
        self.records[reading.key] = reading
        if len(self.records) > self.capacity:
            victim = min(self.records.values(), key=lambda r: (
                (2 if r.hard_alert else int(r.temperature_c >= 27)) if self.priority else 0,
                r.sample_s, r.key,
            ))
            del self.records[victim.key]
            self.evictions += 1
        self.peak = max(self.peak, len(self.records))


def pack_summary(observed, zone, start_s, value):
    temp = [round(r.temperature_c * 100) for r in observed]
    humid = [round(r.humidity_pct * 100) for r in observed]
    return SUMMARY.pack(2, ZONE_ID[zone], value, 0, round(start_s * 1e6),
                        round(max(r.sample_s for r in observed) * 1e6), len(observed),
                        sum(temp), min(temp), max(temp), sum(humid), min(humid), max(humid))


def choose(policy, features, truth, routine_count, scorer, case_id, energy_weight):
    numeric = heuristic_value(features)
    value, source, latency_ms = numeric, "heuristic", 0.0
    if policy == "fixed":
        return Decision("summary" if routine_count else "cache", value, "fixed", 0.0)
    if policy == "rule":
        action = "cache" if not routine_count else ("forward" if value else "summary")
        return Decision(action, value, "rule", 0.0)
    if policy == "jev-reference":
        value, source = truth, "reference"
    if policy == "jev-all" or policy == "jev-gated" and ambiguous(features):
        if scorer is None:
            raise ValueError("SLM policy requires a scorer")
        prediction = scorer.score(case_id, features)
        source, latency_ms = prediction["status"], prediction["latency_ms"]
        if source == "model":
            value = prediction["value"]
    return Decision(jev_action(value, routine_count, features["count"], energy_weight),
                    value, source, latency_ms)


def mean_or_none(values):
    return fmean(values) if values else None


def replay(readings, policy, *, window_s=20, capacity=20, ttl=75, freshness_s=12,
           energy_weight=0.18, scorer=None, scenario="trace"):
    truth_by_window = defaultdict(list)
    arrivals = sorted((r for r in readings if r.arrival_s is not None),
                      key=lambda r: (r.arrival_s, r.key))
    for reading in readings:
        truth_by_window[(int(reading.sample_s // window_s), reading.zone)].append(reading)
    cache = RawCache(capacity, ttl, policy.startswith("jev-"))
    cache_hits = 0
    cursor = 0
    uplink_payloads = Counter()
    actions = Counter()
    sent_raw = {}
    aggregate_errors, freshness, count_coverage = [], [], []
    decisions = []
    review_needed = review_raw = covered_queries = late_samples = 0
    for (window, zone), truth_rows in sorted(truth_by_window.items()):
        start = window * window_s
        closes = start + window_s + GRACE_S
        query_time = closes + 2
        while cursor < len(arrivals) and arrivals[cursor].arrival_s <= query_time:
            arrival = arrivals[cursor]
            cache.add(arrival, arrival.arrival_s)
            cursor += 1
        cache.expire(query_time)
        latest = max(truth_rows, key=lambda r: (r.sample_s, r.key))
        cache_hits += int(latest.key in cache.records and query_time - latest.sample_s <= freshness_s)

        seen = [r for r in truth_rows if r.arrival_s is not None]
        observed = [r for r in seen if r.arrival_s <= closes]
        late_samples += len(seen) - len(observed)
        immediate = [r for r in seen if policy == "raw" or r.hard_alert]
        for reading in immediate:
            sent_raw[reading.key] = reading.arrival_s
            uplink_payloads[len(reading.encode())] += 1

        reference = reference_value(truth_rows, zone)
        available = [r for r in immediate if r.arrival_s <= query_time]
        if observed and policy != "raw":
            routine = [r for r in observed if not r.hard_alert]
            features = window_features(observed, zone)
            result = choose(policy, features, reference, len(routine), scorer,
                            f"{scenario}/{window}/{zone}", energy_weight)
            actions[result.action] += 1
            decisions.append({
                "window": window, "zone": zone, "reference": reference,
                "value": result.value, "source": result.source, "action": result.action,
                "latency_ms": result.latency_ms, **features,
            })
            ready = closes + result.latency_ms / 1000
            if result.action == "summary":
                # Hard alerts have a separate raw path, so they are excluded from the aggregate.
                uplink_payloads[len(pack_summary(routine, zone, start, result.value))] += 1
                if ready <= query_time:
                    available.extend(routine)
            if result.action == "forward":
                for reading in routine:
                    sent_raw[reading.key] = ready
                    uplink_payloads[len(reading.encode())] += 1
                if ready <= query_time:
                    available.extend(routine)
        if reference:
            review_needed += len(truth_rows)
            review_raw += sum(sent_raw.get(r.key, math.inf) <= query_time for r in truth_rows)
        count_coverage.append(len(available) / len(truth_rows))
        if available:
            covered_queries += 1
            aggregate_errors.append(abs(fmean(r.temperature_c for r in available) -
                                        fmean(r.temperature_c for r in truth_rows)))
            freshness.append(query_time - max(r.sample_s for r in available))

    alerts = [r for r in readings if r.hard_alert]
    alert_delays = [sent_raw[r.key] - r.sample_s for r in alerts if r.key in sent_raw]
    model_calls = [d for d in decisions if d["source"] in ("model", "fallback")]
    scored = [d for d in decisions if d["source"] == "model"]
    return {
        "policy": policy, "generated": len(readings), "ingress": len(arrivals),
        "windows": len(truth_by_window), "uplink_messages": sum(uplink_payloads.values()),
        "uplink_bytes": sum((size + IPV6_UDP_BYTES) * count for size, count in uplink_payloads.items()),
        "uplink_proxy_mj": sum(proxy_energy_mj(size) * count for size, count in uplink_payloads.items()),
        "alert_generated": len(alerts), "alert_received": len(alert_delays),
        "alert_on_time": sum(delay <= ALERT_DEADLINE_S for delay in alert_delays),
        "alert_mean_delay_s": mean_or_none(alert_delays),
        "review_raw_coverage": review_raw / review_needed if review_needed else None,
        "cloud_query_coverage": covered_queries / len(truth_by_window) if truth_by_window else None,
        "cloud_count_coverage": mean_or_none(count_coverage),
        "cloud_temp_mae_c": mean_or_none(aggregate_errors),
        "cloud_mean_age_s": mean_or_none(freshness),
        "local_fresh_raw_hit_rate": cache_hits / len(truth_by_window) if truth_by_window else None,
        "raw_cache_peak": cache.peak, "raw_cache_evictions": cache.evictions,
        "late_ingress": late_samples, "forward_windows": actions["forward"],
        "summary_windows": actions["summary"], "cache_only_windows": actions["cache"],
        "slm_calls": len(model_calls), "slm_fallbacks": len(model_calls) - len(scored),
        "slm_mean_latency_ms": mean_or_none([d["latency_ms"] for d in model_calls]),
        "slm_accuracy": mean_or_none([d["value"] == d["reference"] for d in scored]),
        "decision_score_accuracy": mean_or_none([d["value"] == d["reference"] for d in decisions]),
    }, decisions
