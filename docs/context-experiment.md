# Context-aware live gateway comparison

Can an operator note direct the gateway to send raw records for an investigation while identical telemetry can be summarized during routine work? How accurately can a small local model recover that instruction from plain language?

## Prior work and overlap

Targeted searches through 20 November 2026 covered WSN data aggregation and suppression, language-to-policy for IoT, live policy adaptation, and measured inference costs. The comparison below identifies overlap within the papers reviewed.

| Prior work | Result relevant to this project | What this experiment evaluates |
|---|---|---|
| Madden et al., TAG | In-network aggregation can compute compact summaries in low-power sensor networks. | Fixed aggregation is a control. The experiment asks when an operator's need for individual samples makes summary-only delivery inadequate. |
| Raza et al., model-based sensing, 2016 | Prediction-based reporting and wake-up receivers were evaluated on real WSN case studies, including Intel Lab data. | This comparison keeps mote reporting constant. Changes in bytes reflect gateway forwarding decisions. |
| Alajramy et al., on-device derivation of IoT usage control policies, 2026 | A fine-tuned model translates commands to JSON before deterministic conversion to U-XACML. It reports 92.86% accuracy under its key-value metric. | JSON policy extraction is established. Here the output selects raw or summarized telemetry for one zone and a limited interval. We score exact profiles and measure their effect at the receiver. |
| Li et al., HomeBench, ACL 2025 | The benchmark evaluates 13 models on valid and invalid smart-home instructions. | The local test includes unsupported, ambiguous, and descriptive notes. Exact profile match and false activation are reported separately. |
| Kim et al., LLM-assisted policy-adaptive monitoring, 2026 | Their feedback loop uses LLM interpretations of anomaly logs to adapt thresholds, windows, and check intervals in a oneM2M system. | The closest monitoring overlap. The change here comes from an operator note and affects the raw records available for review and the bytes delivered, with a matched telemetry control. |
| Benchmarking Large Language Model Inference on Limited-Resource Edge Systems, 2026 | Jetson experiments pair internal rail readings with external whole-board power measurements. | A future hardware comparison needs equal telemetry service and includes the complete gateway workload. Published inference power is specific to the measured setup. |

[TAG](https://db.csail.mit.edu/madden/html/madden_tag_html/) · [Raza et al.](https://arxiv.org/abs/1601.06664) · [U-XACML policy derivation](https://doi.org/10.1016/j.future.2025.108067) · [HomeBench](https://aclanthology.org/2025.acl-long.597/) · [Policy-adaptive monitoring](https://doi.org/10.7840/kics.2026.51.7.1328) · [Edge inference power benchmark](https://doi.org/10.3390/electronics15112451)

The intended contribution is the controlled comparison of **operator intent, investigation fidelity, alert timing, and backhaul cost** in a live WSN gateway. This is a targeted literature review and a pilot, with novelty limited to that experimental question and subject to further review.

## Method

The live mesh and sensor generator are the same as the earlier burst experiment. Raw forwarding and the simple numeric forwarding rule run through the real Cooja-to-UDP path. Fixed aggregation reuses a previously captured run after byte-for-byte trace matching and receipt validation. Every case shares 240 generated readings and six hard alerts. The receiver compares the real UDP payloads with the ground-truth trace. All hard alerts keep the same immediate path.

The two live notes describe a lab warm-up where a summary suffices and a post-maintenance inspection that needs individual samples. Both apply from simulated 80 s through 86 s, so they cover the same readings. Three methods supply the context.

- **Structured context** receives the correct explicit zone, action, and duration. This is the control for what the gateway can achieve when the instruction is supplied correctly.
- **Keyword context** extracts a single explicit zone and duration and requires a recognizable raw or summary phrase. It declines when both action vocabularies are present or the scope is missing.
- **SLM context** calls local Qwen once per note and uses a constrained JSON response. The model receives the note and three different examples in the prompt. It does not receive fixture labels or expected profiles. The note instructions and labels were authored for this experiment, and the prompt was frozen before the 16-note evaluation.

The profile can specify one zone, raw or summary, and an even duration from 2 to 12 seconds so it aligns with the existing two-second windows. Within its scope the operator request directly determines the action. Outside its scope the gateway uses the existing heuristic JEV policy. The test loads the note before the stream and activates it on the simulated clock. Readings preserved for review are measured at the upstream receiver. The short synthetic note set measures this bounded task and does not represent a field study.

## Results

The same 68 ordinary lab readings were in scope for both notes. The receiver captured all 54 hard alerts within the two-second deadline across the nine distinct runs. The slowest was 157.9 ms. All simulator trace hashes matched.

| Policy | Warm-up raw lab samples | Warm-up payload | Inspection raw lab samples | Inspection payload |
|---|---:|---:|---:|---:|
| Raw forwarding | 68/68 | 4,560 B | 68/68 | 4,560 B |
| Simple numeric forwarding rule | 68/68 | 4,560 B | 68/68 | 4,560 B |
| Fixed aggregation | 0/68 | 646 B | 0/68 | 646 B |
| Structured context | 0/68 | 646 B | 68/68 | 1,824 B |
| Keyword context | 0/68 | 646 B | 68/68 | 1,824 B |
| Qwen context | 68/68 | 1,824 B | 68/68 | 1,824 B |

Fixed aggregation represented all 68 target readings as summaries in both cases. The numeric rule forwarded every reading under the deliberately warm workload. Structured and keyword context delivered every requested inspection record using **60% fewer payload bytes than raw forwarding**. Qwen handled the inspection correctly and unnecessarily sent raw data during the warm-up, adding 1,178 B over the structured and keyword controls in that case.

| Note interpretation | Exact profiles on 9 actionable notes | Correctly declined on 7 unsupported notes | Overall exact profiles |
|---|---:|---:|---:|
| Keyword | 6/9 | 7/7 | 13/16 |
| Qwen | 8/9 | 0/7 | 8/16 |

Qwen correctly handled three actionable paraphrases missed by the keyword parser. It also produced an active profile for every note that should have been declined. One example was a request for 60 seconds, which produced a valid-looking six-second profile. The JSON grammar verifies permitted output fields and values. Whether those fields follow the note remains an evaluation outcome. The median note translation took 449.8 ms on the WSL2 host.

For this prototype, structured input is the reliable baseline. Natural-language input benefits from handling paraphrases only when ambiguous or unsupported instructions can be declined reliably. The two live notes represent a single network and observation period. No general population accuracy or energy estimate follows from these sample sizes.

## Reproduce

Run from the repository root in Linux or WSL with the pinned simulator, model, and llama.cpp runtime available.

```bash
python3 -m unittest discover -s tests -v
python3 context_experiment.py --out .cache/context-repeat
```

Pass `--reuse-baseline POLICY DIRECTORY` to use an earlier live run on the same trace. The runner revalidates its packets and matches the simulator trace hash before comparing policies. The recorded fixed baseline is [the first fixed live run](../results/live/01-fixed/). The committed evidence includes [the live comparison](../results/context/summary.json), [the note results](../results/context/note-results.json), [the test notes](../tests/operator-notes.json), and [the versions and hashes](../results/context/provenance.json).

## Hardware power measurement

The available environment is a WSL2 laptop with AC and battery sensors. A separate gateway board and external meter were unavailable for this run. The recorded model times and UDP byte counts cannot establish the energy cost.

For a board experiment, place the gateway and model server on the board under test. Feed the same event stream from a second host and receive the UDP output on that host, so the board meter covers the gateway and its network interface. For each policy log the input voltage and current or watts throughout a fixed-duration run, plus an idle baseline. Test both cold model loading and the steady-state cost of processing notes at a documented frequency. Integrate watts over the run to obtain joules. Repeat the runs in alternating policy order at the same governor and thermal conditions. Report whole-board joules, elapsed time, profile correctness, alert deadline compliance, and target raw-record coverage together. Compare energy at matched service levels before drawing conclusions about efficiency. [Example of external board power measurement](https://doi.org/10.3390/electronics15112451)
