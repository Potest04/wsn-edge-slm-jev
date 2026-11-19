# WSN edge caching and aggregation with SLM and JEV

Computer Networks II, topic 3. **Σχεδιασμός και Υλοποίηση Πλαισίου Έξυπνης Ενδιάμεσης Αποθήκευσης και Συγκέντρωσης Δεδομένων WSN στην Άκρη του Δικτύου με Συνδυασμό Μικρών Γλωσσικών Μοντέλων (SLM) και Μοντέλων Αποφάσεων JEV (Joint Energy-Value).**

[First-iteration scientific report](output/pdf/first-iteration-report.pdf) · [Research protocol and next experiments](docs/research-plan.md) · [Result summary](results/summary.json)

The [first live experiment](docs/live-experiment.md) connects running Cooja motes to a Python gateway and a separate IPv6 UDP receiver on the same Linux host.

The [context-aware live comparison](docs/context-experiment.md) adds raw forwarding, the numeric rule, and three ways of supplying a time-limited operator instruction.

Five Contiki-NG environmental motes send CoAP NON POST telemetry over IPv6/RPL-Lite to a Cooja collector. The gateway replays the collector trace with a bounded TTL cache, window statistics, and an immediate hard-alert path. Qwen2.5-0.5B-Instruct assigns a semantic value to observed telemetry windows, and JEV selects local retention, an uplink summary, or raw uplink. Every gateway policy sees the same arrival timestamps and generated reference stream for a paired run.

Across nine paired Cooja traces, gated JEV reduced modeled backhaul bytes by 85.2% and delivered all 54 hard alerts by the two-second deadline. Fixed aggregation reduced bytes by 88.3% and provided higher cloud-window coverage. The small model overpredicted criticality and gated JEV made the same decisions as heuristic JEV at the default cost weight. The report includes the raw-detail and cache-pressure trade-offs.

## Reproduce the pilot

Use Ubuntu or WSL2 with Java 25, GCC, Make, Python 3.11+, and the pinned Contiki-NG checkout with submodules. The WSL machine used for the pilot has this checkout at `~/contiki-ng`.

```bash
git clone --branch release/v5.2 --recurse-submodules https://github.com/contiki-ng/contiki-ng.git ~/contiki-ng
git -C ~/contiki-ng rev-parse HEAD
git -C ~/contiki-ng/tools/cooja rev-parse HEAD
export CONTIKI="$HOME/contiki-ng"
python3 -m unittest discover -s tests -v
bash run-cooja.sh line 17 1 1 .cache/smoke
```

The expected revisions are Contiki-NG `4ccd1a3b9e767f168d12ec3df187fe6b7dc736b5` and Cooja `fabff3ee41eeeb6f3de7bd11f7c9cdf0a7d7dfc4`. Set `JAVA_HOME` if the Java 25 location differs. The smoke run must generate samples, accept CoAP at the root, show at least one successful source outside direct range, and record six Energest snapshots. The checked-in pilot for seed 17 logged 239 generated samples and 230 sent and received.

```bash
bash run-matrix.sh .cache/reproduction/cooja
bash fetch-inputs.sh
python3 benchmark.py \
  --cooja .cache/reproduction/cooja \
  --intel .cache/intel-lab-data.txt.gz \
  --model .cache/qwen.gguf \
  --server .cache/llama/llama-b12318/llama-server \
  --out .cache/reproduction
```

The input-fetch script verifies checksums for the Intel archive, Q4_K_M model, and Ubuntu x86-64 inference binary. An ARM host can use its own compatible llama.cpp `llama-server` with the same GGUF. Use a fresh benchmark output directory for each timing run. The runner binds the SLM endpoint to loopback, starts and stops it, and retains the raw inference audit. On compatible Linux hardware, `--energy-counter /sys/class/powercap/.../energy_uj` optionally records a package counter delta for the entire replay. Set up calibrated external metering for whole-board energy.

## Inspect the results

| Location | Contents |
|---|---|
| `firmware/` | Sensor, CoAP collector, explicit telemetry encoding, and Energest snapshots |
| `cooja.py`, `run-cooja.sh`, `run-matrix.sh` | Seeded scenario generation, headless execution, and validated trace export |
| `edge.py`, `semantic.py` | TTL cache, JEV action utility, bounded SLM output and heuristic fallback |
| `results/cooja/` | Per-run scenario XML, simulator events, energy state times, and trace |
| `results/edge-metrics.csv`, `results/edge-decisions.csv` | Per-replay metrics and per-window policy decisions |
| `results/slm-audit.jsonl`, `results/provenance.json` | Measured local inference responses, latency, model hash, and runtime |
| `docs/figures/`, `docs/make_report.py` | Figures and reproducible PDF source |

The collected Cooja logs are the transport into the replay in this iteration. Backhaul bytes are counted from binary records and a 48-byte IPv6/UDP envelope. No backhaul link is emulated. Mote Energest state times, host inference latency and memory, and the assumed backhaul energy coefficient are different measurements. The Intel Lab replay has a synthetic 50 ms arrival delay and proxy zones assigned by node ID.

Regenerate figures with `python3 docs/plot_results.py` using Matplotlib. Regenerate the report with `python3 docs/make_report.py` using ReportLab.
