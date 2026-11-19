"""Run and verify the paced Cooja-to-gateway-to-UDP experiment in Linux/WSL."""

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
from statistics import median
import subprocess
import sys
import tempfile

from benchmark import digest, wait_server
from cooja import config, parse
from edge import ALERT_DEADLINE_S, IPV6_UDP_BYTES, ZONE_ID, load_trace
from live_gateway import LiveGateway, MAX_LINE_BYTES, POLICIES, WINDOW_MS
from semantic import LocalScorer


ROOT = Path(__file__).resolve().parent


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def validate(trial, policy):
    trace = load_trace(trial / "trace.csv")
    gateway = read_events(trial / "gateway.jsonl")
    wire = read_events(trial / "receiver.jsonl")
    network = json.loads((trial / "network.json").read_text())
    sent = [row for row in gateway if row["event"] == "send"]
    if Counter(row["payload"] for row in sent) != Counter(row["payload"] for row in wire):
        raise ValueError("Receiver bytes differ from the gateway's actual UDP sends")
    ingress = {(r["node"], r["seq"]): r for r in gateway if r["event"] == "ingress"}
    if ingress.keys() != {r.key for r in trace if r.arrival_s is not None}:
        raise ValueError("Live ingress differs from the simulated collector")
    generated = [r for r in gateway if r["event"] == "generated"]
    if len(trace) != 240 or len(generated) != len(trace):
        raise ValueError("Expected 48 samples from each of the five sensors")
    first, last = generated[0], generated[-1]
    wall_span = (last["source_wall_ms"] - first["source_wall_ms"]) / 1000
    sim_span = (last["sim_us"] - first["sim_us"]) / 1e6
    if abs(wall_span - sim_span) > 0.5:
        raise ValueError(f"Workload was not paced at ~1× speed ({wall_span=}, {sim_span=})")
    intervals = [r for r in read_events(trial / "slm-audit.jsonl") if r["status"] == "model"
                 ] if policy == "jev-gated" else []
    raw = {(r["node"], r["seq"]): r for r in wire if r["kind"] == "raw"}
    alerts = {r.key: r for r in trace if r.hard_alert}
    if len(alerts) != 6 or not alerts.keys() <= raw.keys():
        raise ValueError(f"Missing hard alerts: {sorted(alerts.keys() - raw.keys())}")
    if len(raw) != sum(row["kind"] == "raw" for row in wire):
        raise ValueError("Duplicate raw delivery")

    alert_results = []
    for key, reading in sorted(alerts.items()):
        received = raw[key]
        source = ingress[key]
        if received["temp_centi"] != round(reading.temperature_c * 100):
            raise ValueError(f"Incorrect alert payload for {key}")
        gateway_to_receiver = (received["receipt_ns"] - source["ingress_ns"]) / 1e6
        wall_end_to_end = (received["receipt_wall_ns"] / 1e6) - source["source_wall_ms"]
        if not (0 <= gateway_to_receiver <= ALERT_DEADLINE_S * 1000
                and 0 <= wall_end_to_end <= ALERT_DEADLINE_S * 1000):
            raise ValueError(f"End-to-end hard alert missed its deadline: {key}")
        overlap = any(i["started_ns"] <= source["ingress_ns"] <= received["receipt_ns"] < i["finished_ns"]
                      for i in intervals)
        alert_results.append({
            "node": key[0], "seq": key[1], "gateway_to_receiver_ms": round(gateway_to_receiver, 3),
            "source_to_receiver_ms": round(wall_end_to_end, 3),
            "simulated_radio_ms": round((reading.arrival_s - reading.sample_s) * 1000, 3),
            "received_during_inference": overlap,
        })
    summaries = [r for r in wire if r["kind"] == "summary"]
    summary_sends = {row["payload"]: row for row in sent if row["kind"] == "summary"}
    windows = [r for r in gateway if r["event"] == "window"]
    if not summaries or not windows:
        raise ValueError("No ordinary readings arrived in an upstream summary")
    if len(summary_sends) != len(summaries):
        raise ValueError("Duplicate summary delivery")
    summary_delays = []
    for row in summaries:
        origin = summary_sends[row["payload"]]
        ordinary = [
            r for r in trace if r.arrival_s is not None and not r.hard_alert and
            r.zone == origin["zone"] and
            int(round(r.sample_s * 1e6)) // (WINDOW_MS * 1000) * WINDOW_MS == origin["start_ms"] and
            round(r.arrival_s * 1e6) <= origin["cutoff_us"]
        ]
        temps = [round(r.temperature_c * 100) for r in ordinary]
        humidity = [round(r.humidity_pct * 100) for r in ordinary]
        expected = (len(ordinary), sum(temps), min(temps), max(temps),
                    sum(humidity), min(humidity), max(humidity))
        fields = ("count", "temp_sum", "temp_min", "temp_max", "humidity_sum", "humidity_min", "humidity_max")
        if (tuple(row[k] for k in fields) != expected or row["zone_id"] != ZONE_ID[origin["zone"]]
                or row["newest_us"] != round(max(r.sample_s for r in ordinary) * 1e6)):
            raise ValueError(f"Receiver decoded an incorrect summary for {origin['start_ms']}/{origin['zone']}")
        summary_delays.append((row["receipt_ns"] - origin["closed_ns"]) / 1e6)
    if not all(0 <= delay <= 2000 for delay in summary_delays):
        raise ValueError("Routine summary missed its two-second deadline after window close")
    routine_during_inference = sum(not r["hard"] and any(
        i["started_ns"] <= r["ingress_ns"] < i["finished_ns"] for i in intervals
    ) for r in ingress.values())
    fallback_count = sum(
        r["event"] == "queue_fallback" or r["event"] == "inference" and r["source"] != "model"
        for r in gateway
    )
    if policy == "jev-gated" and (
        fallback_count or not any(r["received_during_inference"] for r in alert_results)
        or not routine_during_inference
    ):
        raise ValueError(f"Gated run did not demonstrate useful inference overlap ({fallback_count=}, "
                         f"{routine_during_inference=}, {alert_results=})")
    return {
        "policy": policy, "seed": network["seed"], "trace_sha256": digest(trial / "trace.csv"),
        "generated": len(generated), "attempted": network["attempted"], "ingress": len(ingress),
        "multihop_source_ids": network["multihop_source_ids"],
        "generation_wall_span_s": round(wall_span, 3), "generation_sim_span_s": round(sim_span, 3),
        "datagrams": len(wire), "udp_payload_bytes": sum(r["bytes"] for r in wire),
        "ipv6_udp_bytes": sum(r["bytes"] + IPV6_UDP_BYTES for r in wire),
        "summaries": len(summaries), "window_count": len(windows),
        "routine_samples_at_receiver": sum(r["count"] for r in summaries) + len(raw) - len(alerts),
        "peak_readings_per_2s_window": max(Counter(r["start_ms"] for r in ingress.values()).values()),
        "summary_max_delay_ms": round(max(summary_delays), 3),
        "alerts_on_time": len(alert_results), "alerts": alert_results,
        "alert_gateway_to_receiver_median_ms": round(median(r["gateway_to_receiver_ms"] for r in alert_results), 3),
        "alert_source_to_receiver_max_ms": max(r["source_to_receiver_ms"] for r in alert_results),
        "inference_requests": len(intervals), "inference_fallbacks": fallback_count,
        "inference_median_ms": round(median(
            (r["finished_ns"] - r["started_ns"]) / 1e6 for r in intervals
        ), 3) if intervals else None,
        "routine_arrivals_during_inference": routine_during_inference,
        "late_arrivals": sum(r["event"] == "late" for r in gateway),
    }


async def stop(process):
    if process is not None and process.returncode is None:
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), 5)
        except TimeoutError:
            process.kill()
            await process.wait()


async def run_trial(trial, contiki, seed, policy, scorer, java_home):
    trial.mkdir()
    with tempfile.TemporaryDirectory(prefix="wsn-live-") as temp, (
        trial / "gateway.jsonl"
    ).open("x", encoding="utf-8", buffering=1) as audit, (trial / "cooja.log").open("wb") as cooja_log:
        stage = Path(temp)
        shutil.copytree(ROOT / "firmware", stage / "firmware")
        (stage / "contiki-ng").symlink_to(contiki, target_is_directory=True)
        connected = asyncio.get_running_loop().create_future()

        def accept(reader, writer):
            if connected.done():
                writer.close()
            else:
                connected.set_result((reader, writer))

        bridge = await asyncio.start_server(accept, "127.0.0.1", 0, limit=MAX_LINE_BYTES)
        receiver = simulator = None
        try:
            receiver = await asyncio.create_subprocess_exec(
                sys.executable, str(ROOT / "live_gateway.py"), str(trial / "receiver.jsonl"),
                stdout=asyncio.subprocess.PIPE,
            )
            receiver_port = int(await asyncio.wait_for(receiver.stdout.readline(), 8))
            bridge_port = bridge.sockets[0].getsockname()[1]
            script = (ROOT / "live-cooja.js").read_text().replace("BRIDGE_PORT", str(bridge_port))
            scenario = stage / "live.csc"
            config(scenario, "mesh", seed, 1, 1, script=script, live_burst=True)
            env = {**os.environ, "JAVA_HOME": str(java_home),
                   "PATH": f"{java_home / 'bin'}:{os.environ['PATH']}"}
            simulator = await asyncio.create_subprocess_exec(
                "./gradlew", "--console=plain", "--no-watch-fs", "run",
                f"--args=--no-gui --contiki={contiki} --logdir={stage} {scenario}",
                cwd=contiki / "tools" / "cooja", env=env, stdout=cooja_log, stderr=cooja_log,
            )
            exit_task = asyncio.create_task(simulator.wait())
            try:
                await asyncio.wait_for(
                    asyncio.wait({connected, exit_task}, return_when=asyncio.FIRST_COMPLETED), 65,
                )
                if not connected.done():
                    raise RuntimeError(f"Cooja exited before connection. Inspect {trial / 'cooja.log'}")
                reader, writer = connected.result()
                try:
                    gateway = LiveGateway(policy, receiver_port, audit, scorer)
                    await asyncio.wait_for(gateway.run(reader), 40)
                finally:
                    writer.close()
                    await writer.wait_closed()
                if await asyncio.wait_for(exit_task, 10) != 0 or await asyncio.wait_for(receiver.wait(), 5) != 0:
                    raise RuntimeError(f"Cooja or the UDP receiver failed for {trial.name}")
            finally:
                exit_task.cancel()
                await asyncio.gather(exit_task, return_exceptions=True)
            shutil.copyfile(stage / "COOJA.testlog", trial / "events.log")
        finally:
            bridge.close()
            await bridge.wait_closed()
            await stop(simulator)
            await stop(receiver)
    parse(trial / "events.log", "mesh", seed, 1.0, 1, trial)
    metrics = validate(trial, policy)
    (trial / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return metrics


@contextmanager
def model_server(args):
    if "jev-gated" not in args.policies:
        yield
        return
    with (args.out / "llama-server.log").open("wb") as log, subprocess.Popen([
        str(args.server.resolve()), "-m", str(args.model.resolve()), "-c", "1024", "-t", "4",
        "-ngl", "0", "--host", "127.0.0.1", "--port", str(args.model_port), "--parallel", "1",
    ], stdout=log, stderr=log) as process:
        try:
            wait_server(process, args.model_port)
            yield
        finally:
            process.terminate()
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


async def main(args):
    results = []
    for repeat in range(1, args.repeats + 1):
        paired = []
        for policy in args.policies:
            trial = args.out / f"{repeat:02}-{policy}"
            scorer = (LocalScorer(args.model_port, trial / "slm-audit.jsonl", capture_timing=True)
                      if policy == "jev-gated" else None)
            print(f"Running pair {repeat}/{args.repeats}, {policy}", flush=True)
            row = await run_trial(trial, args.contiki, args.seed, policy, scorer, args.java_home)
            paired.append(row)
            print(f"{row['alerts_on_time']}/6 alerts on time, "
                  f"{sum(r['received_during_inference'] for r in row['alerts'])} during real inference, "
                  f"worst source-to-receiver {row['alert_source_to_receiver_max_ms']:.1f} ms", flush=True)
        if len({row["trace_sha256"] for row in paired}) != 1:
            raise ValueError("Compared policies received different simulated input traces")
        results.extend(paired)
    (args.out / "summary.json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contiki", type=Path, default=Path(os.getenv("CONTIKI", Path.home() / "contiki-ng")))
    parser.add_argument("--java-home", type=Path, default=Path(os.getenv(
        "JAVA_HOME", "/usr/lib/jvm/java-25-openjdk-amd64")))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=ROOT / ".cache/qwen.gguf")
    parser.add_argument("--server", type=Path, default=ROOT / ".cache/llama/llama-b12318/llama-server")
    parser.add_argument("--model-port", type=int, default=8091)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--policies", choices=POLICIES, nargs="+", default=POLICIES)
    args = parser.parse_args()
    if args.repeats < 1 or len(set(args.policies)) != len(args.policies):
        parser.error("Use positive repeats and unique policies")
    args.out = args.out.resolve()
    args.contiki = args.contiki.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    provenance = {
        "contiki": subprocess.check_output(["git", "-C", args.contiki, "rev-parse", "HEAD"], text=True).strip(),
        "cooja": subprocess.check_output(["git", "-C", args.contiki / "tools/cooja", "rev-parse", "HEAD"],
                                         text=True).strip(),
        "seed": args.seed, "repeats": args.repeats, "policies": args.policies,
    }
    if "jev-gated" in args.policies:
        provenance["model_sha256"] = digest(args.model)
        provenance["model_runtime"] = subprocess.check_output(
            [str(args.server.resolve()), "--version"], stderr=subprocess.STDOUT, text=True,
        ).strip()
        provenance["model_threads"] = 4
    (args.out / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    with model_server(args):
        asyncio.run(main(args))
