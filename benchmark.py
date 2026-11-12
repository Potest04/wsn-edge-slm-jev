"""Run the paired Cooja and Intel trace comparisons and retain exact observations."""

import argparse
from collections import defaultdict
from datetime import datetime
import gzip
from hashlib import file_digest
import json
from pathlib import Path
import os
import random
from statistics import fmean
import subprocess
import time
from urllib.error import URLError
from urllib.request import urlopen
import csv

from edge import Reading, load_trace, replay
from semantic import LocalScorer


POLICIES = ("raw", "fixed", "rule", "jev-heuristic", "jev-reference", "jev-all", "jev-gated")
METRICS = (
    "uplink_bytes", "uplink_messages", "uplink_proxy_mj", "alert_on_time",
    "review_raw_coverage", "cloud_query_coverage", "cloud_count_coverage", "cloud_temp_mae_c",
    "cloud_mean_age_s", "local_fresh_raw_hit_rate", "raw_cache_evictions", "cache_only_windows",
    "slm_calls", "slm_fallbacks", "slm_mean_latency_ms", "slm_accuracy", "decision_score_accuracy",
)
INTEL_START = datetime.fromisoformat("2004-03-01T12:00:00")
INTEL_STOP = datetime.fromisoformat("2004-03-01T12:10:00")


def intel_trace(path):
    """Select a declared interval, eight node IDs, and valid environmental values."""
    records = {}
    with gzip.open(path, "rt", encoding="ascii") as handle:
        for line in handle:
            fields = line.split()
            if len(fields) != 8 or fields[0] != "2004-03-01" or not fields[1].startswith("12:0"):
                continue
            try:
                node, seq = int(fields[3]), int(fields[2])
                temperature, humidity = float(fields[4]), float(fields[5])
                timestamp = datetime.fromisoformat(fields[0] + "T" + fields[1])
            except ValueError:
                continue
            if not (1 <= node <= 8 and 0 <= temperature <= 45 and 0 <= humidity <= 100
                    and INTEL_START <= timestamp < INTEL_STOP):
                continue
            offset = (timestamp - INTEL_START).total_seconds()
            record = Reading(node, seq, offset, offset + 0.05, round(temperature, 2), round(humidity, 2),
                             "lab" if node % 2 == 0 else "hall")
            records.setdefault(record.key, record)
    if not records:
        raise ValueError("Intel trace slice is empty")
    return sorted(records.values(), key=lambda r: (r.sample_s, r.key))


def digest(path):
    with path.open("rb") as handle:
        return file_digest(handle, "sha256").hexdigest()


def wait_server(process, port):
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"llama-server exited ({process.returncode}). Inspect its startup log.")
        try:
            with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                if json.load(response)["status"] == "ok":
                    return
        except (OSError, URLError, ValueError, KeyError):
            pass
        time.sleep(0.25)
    raise TimeoutError("llama-server did not become ready")


def peak_rss_kib(pid):
    path = Path(f"/proc/{pid}/status")
    if path.exists():
        return next((int(line.split()[1]) for line in path.read_text().splitlines()
                     if line.startswith("VmHWM:")), None)
    return None


def measure_energy(path, before):
    if path is None:
        return None
    after = int(path.read_text())
    if after < before:
        after += int(path.with_name("max_energy_range_uj").read_text())
    return (after - before) / 1e6


def run(cooja, intel, scorer, out):
    metrics, decisions, network = [], [], []
    traces = {}
    for folder in sorted(cooja.iterdir()):
        if not (folder / "network.json").is_file():
            continue
        metadata = json.loads((folder / "network.json").read_text())
        with (folder / "energy.csv").open(newline="") as handle:
            states = list(csv.DictReader(handle))
        # Compare durations directly. Cooja motes have no calibrated physical current.
        for state in ("tx", "listen", "cpu", "lpm", "deep_lpm"):
            metadata[f"sensor_{state}_s"] = sum(int(row[state]) / int(row["ticks_per_s"])
                                                 for row in states if int(row["node"]) != 1)
        metadata["trace_sha256"] = digest(folder / "trace.csv")
        metadata["ingress_pdr"] = metadata["received"] / metadata["attempted"]
        metadata["retained_sample_fraction"] = metadata["received"] / metadata["generated"]
        network.append(metadata)
        traces[folder.name] = load_trace(folder / "trace.csv")
    baseline = [key for key in traces if key.endswith("-s1")]
    if not baseline:
        raise ValueError("No Cooja baseline trace")

    def compare(scenario, dataset, rows, selected, window_s, capacity, weight, fresh_s, ttl):
        for policy in selected:
            m, d = replay(rows, policy, window_s=window_s, capacity=capacity, ttl=ttl,
                          freshness_s=fresh_s, energy_weight=weight, scorer=scorer, scenario=dataset)
            metrics.append({"scenario": scenario, "dataset": dataset, "capacity": capacity,
                            "energy_weight": weight, **m})
            decisions.extend({"scenario": scenario, "dataset": dataset, "policy": policy,
                              "capacity": capacity, "energy_weight": weight, **x} for x in d)

    for key in baseline:
        print(f"Replaying {key}", flush=True)
        compare("paired", key, traces[key], POLICIES, 20, 20, 0.18, 12, 75)
        compare("cache-pressure", key, traces[key], POLICIES, 20, 6, 0.18, 12, 75)
        for weight in (0.06, 0.35):
            compare("cost-sweep", key, traces[key], ("jev-heuristic", "jev-gated"),
                    20, 20, weight, 12, 75)
    for key in traces:
        if key.endswith("-s3"):
            compare("report-stride", key, traces[key], ("raw", "jev-heuristic"), 20, 20, 0.18, 12, 75)
    print("Replaying the Intel Lab slice", flush=True)
    compare("intel", "intel-2004-03-01", intel_trace(intel), POLICIES, 60, 20, 0.18, 45, 180)

    def write_csv(name, rows):
        with (out / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)

    write_csv("edge-metrics.csv", metrics)
    write_csv("edge-decisions.csv", decisions)
    (out / "network-metrics.json").write_text(json.dumps(network, indent=2) + "\n")
    return metrics, network


def paired_interval(pairs):
    """Descriptive 95% percentile interval, resampling whole trace pairs."""
    if not pairs:
        return None
    rng = random.Random(718)
    means = sorted(fmean(rng.choices(pairs, k=len(pairs))) for _ in range(4000))
    return {"n": len(pairs), "mean": fmean(pairs), "lo": means[100], "hi": means[3899]}


def summarize(metrics, network):
    groups = defaultdict(list)
    for row in metrics:
        groups[(row["scenario"], row["policy"], row["energy_weight"])].append(row)
    groups_out = []
    for (scenario, policy, weight), rows in sorted(groups.items()):
        means = {}
        for key in METRICS:
            values = [r[key] for r in rows if r[key] is not None]
            means[key] = fmean(values) if values else None
        groups_out.append({"scenario": scenario, "policy": policy, "weight": weight,
                           "n": len(rows), **means})
    paired = {r["dataset"]: r for r in metrics if r["scenario"] == "paired" and r["policy"] == "raw"}
    delta = {}
    for policy in POLICIES[1:]:
        rows = [r for r in metrics if r["scenario"] == "paired" and r["policy"] == policy]
        delta[policy] = {
            "uplink_savings_pct": paired_interval([
                100 * (1 - r["uplink_bytes"] / paired[r["dataset"]]["uplink_bytes"]) for r in rows]),
            "count_coverage_delta": paired_interval([
                r["cloud_count_coverage"] - paired[r["dataset"]]["cloud_count_coverage"] for r in rows]),
        }
    return {"groups": groups_out, "paired_vs_raw": delta, "network": network}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cooja", type=Path, default=Path("results/cooja"))
    parser.add_argument("--intel", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--server", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--energy-counter", type=Path, help="Optional Linux powercap energy_uj counter")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    audit_path = args.out / "slm-audit.jsonl"
    if audit_path.exists():
        parser.error("Choose a fresh --out directory or archive its existing slm-audit.jsonl before a new benchmark")
    metadata = {
        "model_sha256": digest(args.model), "intel_sha256": digest(args.intel),
        "intel_slice": [INTEL_START.isoformat(), INTEL_STOP.isoformat()],
        "runtime": subprocess.check_output([str(args.server.resolve()), "--version"],
                                           stderr=subprocess.STDOUT, text=True).strip(),
        "threads": args.threads, "host": os.uname().machine if hasattr(os, "uname") else os.name,
    }
    log_path = args.out / "llama-server.log"
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen([
            str(args.server.resolve()), "-m", str(args.model.resolve()), "-c", "1024", "-t", str(args.threads),
            "-ngl", "0", "--host", "127.0.0.1", "--port", str(args.port), "--parallel", "1",
        ], stdout=log, stderr=subprocess.STDOUT)
        try:
            wait_server(process, args.port)
            before = int(args.energy_counter.read_text()) if args.energy_counter else None
            started = time.perf_counter()
            scorer = LocalScorer(args.port, audit_path)
            metrics, network = run(args.cooja, args.intel, scorer, args.out)
            metadata.update({
                "replay_wall_s": time.perf_counter() - started,
                "model_process_peak_rss_kib": peak_rss_kib(process.pid),
                "unique_inference_requests": len(scorer.records),
                "unique_inference_failures": sum(r["status"] != "model" for r in scorer.records.values()),
                "measured_package_energy_j": measure_energy(args.energy_counter, before),
            })
        finally:
            process.terminate()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    (args.out / "provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    (args.out / "summary.json").write_text(json.dumps(summarize(metrics, network), indent=2) + "\n")


if __name__ == "__main__":
    main()
