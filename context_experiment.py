"""Compare operator-note interpretation and measure its live gateway consequences."""

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess

from benchmark import digest
from edge import ZONE_ID, load_trace
from live_experiment import ROOT, model_server, read_events, run_trial, validate
from live_gateway import CONTEXT_POLICIES
from operator_context import keyword_profile, parse_profile, slm_profile


EFFECTIVE_MS = 80_000
BASELINES = ("raw", "fixed", "rule")


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def scope_metrics(directory, expected):
    trace = load_trace(directory / "trace.csv")
    wire = read_events(directory / "receiver.jsonl")
    received_raw = {(row["node"], row["seq"]) for row in wire if row["kind"] == "raw"}
    start = EFFECTIVE_MS / 1000
    stop = start + expected.duration_s
    target = {r.key for r in trace if not r.hard_alert and start <= r.sample_s < stop
              and r.zone == expected.zone}
    unrelated = {r.key for r in trace if not r.hard_alert and r.key not in target}
    summary_count = sum(
        row["count"] for row in wire if row["kind"] == "summary" and row["zone_id"] == ZONE_ID[expected.zone]
        and start <= row["start_us"] / 1e6 < stop
    )
    return {
        "target_samples": len(target), "target_raw_at_receiver": len(target & received_raw),
        "target_samples_in_summaries": summary_count,
        "other_raw_at_receiver": len(unrelated & received_raw),
    }


def evaluate_notes(notes, model_port, out):
    result = []
    profiles = {}
    for item in notes:
        expected = parse_profile(item["expected"])
        keyword = keyword_profile(item["text"])
        slm, audit = slm_profile(item["text"], model_port)
        profiles[item["id"]] = {"structured-context": expected, "keyword-context": keyword,
                                "slm-context": slm}
        result.append({
            "id": item["id"], "expected": asdict(expected) if expected else None,
            "keyword": asdict(keyword) if keyword else None, "slm": asdict(slm) if slm else None,
            "keyword_exact": expected == keyword, "slm_exact": expected == slm and audit["error"] is None,
            **audit,
        })
    write_json(out / "note-results.json", result)
    return profiles


async def run_eval(args, notes):
    profiles = evaluate_notes(notes, args.model_port, args.out)
    rows = []
    baseline = {}
    for policy in BASELINES:
        if policy in args.baseline:
            folder = args.baseline[policy]
            metrics = validate(folder, policy)
        else:
            folder = args.out / policy
            print(f"Live baseline: {policy}", flush=True)
            metrics = await run_trial(folder, args.contiki, args.seed, policy, None, args.java_home)
        baseline[policy] = folder, metrics
    traces = {row["trace_sha256"] for _, row in baseline.values()}
    if len(traces) != 1:
        raise ValueError("Baselines received different simulated input traces")
    for item in (item for item in notes if item.get("live")):
        case = item["id"]
        expected = profiles[case]["structured-context"]
        for policy in BASELINES:
            folder, metrics = baseline[policy]
            rows.append({"case": case, **metrics, **scope_metrics(folder, expected)})
        for policy in CONTEXT_POLICIES:
            profile = profiles[case][policy]
            folder = args.out / f"{case}-{policy}"
            print(f"Live context: {case}, {policy}, profile={profile}", flush=True)
            metrics = await run_trial(folder, args.contiki, args.seed, policy, None, args.java_home,
                                      profile=profile, effective_ms=EFFECTIVE_MS)
            if metrics["trace_sha256"] not in traces:
                raise ValueError(f"Context changed the simulated trace in {folder.name}")
            scope = scope_metrics(folder, expected)
            if policy == "structured-context":
                key = "target_raw_at_receiver" if expected.action == "raw" else "target_samples_in_summaries"
                if scope[key] != scope["target_samples"] or not scope["target_samples"]:
                    raise ValueError(f"Structured context failed to fulfil the request in {case}")
            rows.append({"case": case, **metrics, **scope,
                         "profile": asdict(profile) if profile else None})
    write_json(args.out / "summary.json", rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contiki", type=Path, default=Path(os.getenv("CONTIKI", Path.home() / "contiki-ng")))
    parser.add_argument("--java-home", type=Path, default=Path(os.getenv(
        "JAVA_HOME", "/usr/lib/jvm/java-25-openjdk-amd64")))
    parser.add_argument("--model", type=Path, default=ROOT / ".cache/qwen.gguf")
    parser.add_argument("--server", type=Path, default=ROOT / ".cache/llama/llama-b12318/llama-server")
    parser.add_argument("--model-port", type=int, default=8091)
    parser.add_argument("--notes", type=Path, default=ROOT / "tests/operator-notes.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--reuse-baseline", nargs=2, action="append", default=[], metavar=("POLICY", "DIR"))
    args = parser.parse_args()
    args.policies = CONTEXT_POLICIES
    args.baseline = {policy: Path(path).resolve() for policy, path in args.reuse_baseline}
    if not args.baseline.keys() <= set(BASELINES) or len(args.baseline) != len(args.reuse_baseline):
        parser.error("Baseline policies must be unique raw, fixed, or rule")
    notes = json.loads(args.notes.read_text(encoding="utf-8"))
    ids = [item["id"] for item in notes]
    if len(ids) != len(set(ids)) or any(not key.isascii() or not key.replace("-", "").isalnum() for key in ids):
        parser.error("Note IDs must be unique ASCII names")
    if not any(item.get("live") for item in notes) or any(
            not parse_profile(item["expected"]) for item in notes if item.get("live")):
        parser.error("Expected an actionable profile for each live note")
    args.contiki = args.contiki.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / "provenance.json", {
        "seed": args.seed, "contiki": subprocess.check_output(
            ["git", "-C", args.contiki, "rev-parse", "HEAD"], text=True).strip(),
        "cooja": subprocess.check_output(
            ["git", "-C", args.contiki / "tools/cooja", "rev-parse", "HEAD"], text=True).strip(),
        "model": digest(args.model), "notes": digest(args.notes),
        "model_runtime": subprocess.check_output(
            [str(args.server.resolve()), "--version"], stderr=subprocess.STDOUT, text=True).strip(),
        "model_threads": 4, "profile_effective_sim_ms": EFFECTIVE_MS,
    })
    with model_server(args):
        asyncio.run(run_eval(args, notes))


if __name__ == "__main__":
    main()
