# Research protocol for the first iteration

## Question and hypotheses

Can a fog gateway reduce application backhaul traffic while maintaining alert and query service, and does selective SLM scoring offer a better quality and cost trade-off than heuristic scoring?

- H1 requires at least 50% fewer backhaul bytes than raw forwarding, unchanged hard-alert delivery by the 2 s deadline, and coverage of at least 90% of nonempty reference windows at the cloud query time.
- H2 requires gated JEV to use at most half the calls of always-on JEV and remain within five percentage points of its semantic decision accuracy on all scored windows. Compare both with the heuristic as a separate test of added value.
- H3 tests whether a distinct source-reporting policy changes radio transmit time. It keeps all hard readings eligible and must disclose any change in generated-sample retention and alert delivery. This intervention is applied in firmware and is independent of the gateway policy.

## Protocol

1. Verify one 240 s Cooja run using the pinned upstream revisions. Confirm output from a source outside the root's 48 m transmit range. The 65 s startup gives routes time to form. The traces preserve non-transmissions caused by routing readiness.
2. Run line, mesh, and shared-relay layouts, each with seeds 17, 29, and 41. Pair each run with source stride three. Assign UDGM receive ratios 1.00, 0.93, and 0.86 to those seeds respectively. Seed and loss therefore form a combined scenario factor. A future full factorial should vary the two independently.
3. Compare raw, fixed-window, numeric rule, JEV with heuristic, JEV with a generated-data reference score, JEV with the SLM on every window, and JEV with gated inference. For every comparison, reuse the exact generated records and collector arrivals. The reference variant has access to complete ground truth and serves as an oracle-style ablation.
4. Use 20 s event-time windows with a 2 s arrival grace period. Evaluate a cloud and latest-raw local lookup at end plus 4 s. Repeat with a 6-record cache instead of 20 and vary the energy weight. Replay the fixed Intel Lab slice as a secondary workload with 60 s windows.
5. Audit all inference requests. Use only observed features at the gateway. Derive independent review labels from the complete generated traces. Report class imbalance and separate missed packets from policy decisions. Resample whole paired traces for descriptive intervals.

## Research basis

TAG demonstrates distributive and algebraic statistics for in-network aggregation. The gateway uses its count-and-sum principle for exact weighted means over represented readings. Sample age at query time follows the timestamp definition used in Age of Information research. This experiment samples age at queries and reports missing answers separately. RPL-Lite carries traffic while the application layer controls cache and backhaul policy. [TAG](https://db.csail.mit.edu/madden/html/madden_tag_html/) · [Kaul, Yates and Gruteser, 2012](https://www.iiitd.edu.in/~skkaul/Papers/Infocom2012.pdf) · [RPL](https://www.rfc-editor.org/rfc/rfc6550)

## Follow-up for the course submission

1. Stream live from a Cooja border router into a gateway process and add a real UDP or MQTT upstream sink with acknowledgements and queue limits. Verify backpressure, deduplication across restarts, and overrun behavior under real inference concurrency.
2. Add an independently labeled contextual workload where a numeric threshold is expected to be insufficient. Build development and held-out workload sets, balance severity, cross loss with seeds, and add larger topologies. Compare a calibrated heuristic or small non-language classifier with the SLM on the same observed features.
3. Calibrate a gateway power model on Raspberry Pi or Jetson using an inline meter. Run idle, raw, fixed, heuristic JEV, always-on SLM, and gated SLM under matched traffic and steady thermal conditions. Record board voltage, integrated current, CPU governor, model settings, warm-up, energy per inference, and backhaul activity. Feed the measured coefficients back into JEV and rerun the QoS constraints.
4. Use Sky or a physical WSN testbed for device-level verification when available. Quantify radio listen versus TX energy and include downlink control cost if stride becomes adaptive. Preserve the Cooja and gateway evidence for the course's scientific PDF and 15–30 minute oral presentation. The assignment brief lists 31 January 2027 as its nominal submission date.

The course discussion recommends validating the methodology on Cooja motes first, testing heterogeneous topologies, and choosing the report template freely. [Motes and code organization](https://discord.com/channels/1555142398047158383/1555142816508674058/1566544375222112388) · [Topology guidance](https://discord.com/channels/1555142398047158383/1564741292527849543/1565104844044509257) · [Report format](https://discord.com/channels/1555142398047158383/1555142816508674058/1568392402908221484)
