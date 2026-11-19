# First live gateway experiment

Can a gateway deliver urgent readings within two seconds while it is receiving a burst of ordinary readings and running a small language model?

## Setup

```text
5 Cooja sensor motes → CoAP / UDP / IPv6 / RPL-Lite → Cooja collector
                                                       │
                                             loopback TCP event stream
                                                       ↓
                                               Python gateway
                                                       │
                                         binary datagrams over IPv6 UDP
                                                       ↓
                                          independent receiver process
```

Cooja streams each collector event to the gateway as it occurs. The script also sends source-generation events so the experiment can match readings and record a generation timestamp. Gateway decisions use only readings accepted by the collector. This first connection uses a TCP bridge for collector events. The sensor radio and routing are simulated. The gateway, Qwen inference, and the UDP receiver run as processes on the WSL2 host.

- Five transmitters generate 48 samples each at four samples per second per mote. Six readings are 33 °C, including a reading forced to traverse a relay. Other temperatures and humidity values form warm, ambiguous windows.
- Cooja advances routing setup as fast as possible, then caps simulation speed at 1× before sampling starts. The harness checks that the simulated generation span and elapsed wall time agree within 0.5 s.
- The gateway closes two-second simulation windows with a 100 ms late-arrival allowance. For each zone it can retain ordinary readings locally, send a summary, or send them raw. A hard alert at 32 °C or above goes to the receiver immediately. The gateway uses a bounded TTL cache. Each separately forwarded alert is omitted from its window's summary.
- Fixed aggregation, heuristic JEV, and gated JEV share the same inputs. Gated JEV uses the existing Qwen model for ambiguous windows. One worker handles inference outside the gateway's input loop. Its queue allows four waiting windows and uses the heuristic if full. Inference errors also use the heuristic.

## Pass criteria and measurements

- The trace must contain 240 generated readings and delivery from beyond direct root range. Within each repeat the policies must have identical simulator trace hashes.
- The receiver must capture the same datagram payloads the gateway actually sent. Decoded summaries must match the original eligible readings. Summaries must arrive within two seconds of window closure.
- The receiver must get all six hard alerts within two seconds of generation. For each gated trial, the receiver must record at least one alert after its gateway ingress and before the active Qwen request has finished. At least one ordinary reading must enter the gateway during inference, and the gated trial must complete without model or queue fallback.

The two-second alert deadline uses same-host wall timestamps from the source event and the receiver. The source wall stamp has millisecond resolution. Gateway ingress, the Qwen HTTP request, and the receiver also share the host monotonic clock, which is used for measuring overlap and gateway-to-receiver time. Radio delay is recorded separately in Cooja's simulation clock. The runtime validates successful HTTP inference and captures the request bounds. UDP payload bytes come from the receiver. The optional byte total with headers adds 48 bytes per datagram for IPv6 and UDP.

## Results

Three repeats of each policy ran the same fixed-seed mesh workload. All nine simulator traces matched byte-for-byte. Every trial delivered 240 readings to the gateway, including six hard alerts. The busiest two-second window carried 40 readings. The receiver represented all 234 ordinary readings per trial through summaries or individual records. Every decoded summary matched its eligible inputs.

| Policy | Alerts within 2 s across three runs | Slowest source-to-receiver alert | Slowest summary after window close | Datagrams per run | UDP payload per run |
|---|---:|---:|---:|---:|---:|
| Fixed aggregation | 18/18 | 157.9 ms | 1.6 ms | 20 | 646 B |
| Heuristic JEV | 18/18 | 157.8 ms | 10.5 ms | 21 | 646 B |
| Gated JEV with Qwen | 18/18 | 160.3 ms | 1,274.8 ms | 27 | 741 B |

In the gated trials, **13 of 18 hard alerts reached the receiver while a Qwen HTTP request was still in flight**. The gateway received 145 ordinary readings during those requests. The 27 requests all succeeded, with 323.4 ms median latency and no fallback. The slowest gated summary still arrived within two seconds of window closure. Fixed and heuristic decisions had lower summary latency. Qwen scored all 27 requested windows as critical, causing more individual forwards at the default JEV cost weight. Gated JEV sent 14.7% more UDP payload bytes than fixed aggregation on this workload.

This establishes that the live alert path can keep moving while Qwen is busy on the tested host. The workload is a single synthetic, loss-free Cooja scenario, and both host processes use loopback. WAN behavior, physical radio behavior, and energy use remain outside these measurements. [Per-run metrics](../results/live/summary.json) · [Captured events, UDP receipts, and model request timings](../results/live/)

## Reproduce

From the repository root in Linux or WSL, use the pinned Contiki-NG/Cooja checkout and Java runtime described in the [README](../README.md). Download the pinned model and runtime if they are missing. Use a fresh output directory for each run.

```bash
bash fetch-inputs.sh
python3 -m unittest discover -s tests -v
python3 live_experiment.py --out .cache/live-repeat --repeats 3
```

For a quick model-free run, pass `--policies fixed`. Each trial writes the simulator events, matched trace, gateway decisions, UDP receipts, and metrics. Gated trials also record model outputs and request timing. The receiver binds to IPv6 loopback and the Cooja event bridge binds to IPv4 loopback.
