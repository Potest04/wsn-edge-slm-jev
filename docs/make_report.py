"""Build the first-iteration manuscript from the checked-in experiment evidence."""

from collections import Counter
import csv
from hashlib import sha256
import json
from pathlib import Path
from statistics import median, quantiles
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
OUTPUT = ROOT / "output" / "pdf" / "first-iteration-report.pdf"
OUTPUT.parent.mkdir(parents=True, exist_ok=True)

INK = colors.HexColor("#16263a")
MUTED = colors.HexColor("#465e72")
TEAL = colors.HexColor("#18786f")
LINE = colors.HexColor("#ccd9e2")
PALE = colors.HexColor("#eef5f6")
WHITE = colors.white
WIDTH = A4[0] - 116

styles = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=30, leading=33,
                            spaceAfter=15, textColor=INK),
    "section": ParagraphStyle("section", fontName="Helvetica-Bold", fontSize=22, leading=25,
                              spaceAfter=13, textColor=INK),
    "heading": ParagraphStyle("heading", fontName="Helvetica-Bold", fontSize=12, leading=15,
                              spaceBefore=11, spaceAfter=5, textColor=INK),
    "eyebrow": ParagraphStyle("eyebrow", fontName="Helvetica-Bold", fontSize=9, leading=12,
                              textColor=TEAL, spaceAfter=12, tracking=1.1),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=10.3, leading=15,
                           textColor=INK, spaceAfter=9, alignment=TA_LEFT),
    "small": ParagraphStyle("small", fontName="Helvetica", fontSize=8.8, leading=12,
                            textColor=MUTED, spaceAfter=8),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK),
    "column": ParagraphStyle("column", fontName="Helvetica-Bold", fontSize=8.4, leading=11,
                             textColor=WHITE),
}

data = json.loads((RESULTS / "summary.json").read_text(encoding="utf-8"))
provenance = json.loads((RESULTS / "provenance.json").read_text(encoding="utf-8"))
audit = [json.loads(line) for line in (RESULTS / "slm-audit.jsonl").read_text(encoding="utf-8").splitlines()]
with (RESULTS / "edge-decisions.csv").open(newline="", encoding="utf-8") as handle:
    decisions = list(csv.DictReader(handle))
with (RESULTS / "edge-metrics.csv").open(newline="", encoding="utf-8") as handle:
    metrics = list(csv.DictReader(handle))
paired = {x["policy"]: x for x in data["groups"] if x["scenario"] == "paired"}
stress = {x["policy"]: x for x in data["groups"] if x["scenario"] == "cache-pressure"}
intel = {x["policy"]: x for x in data["groups"] if x["scenario"] == "intel"}
sweep = {x["weight"]: x for x in data["groups"] if x["scenario"] == "cost-sweep" and x["policy"] == "jev-gated"}
intel_raw = next(r for r in metrics if r["scenario"] == "intel" and r["policy"] == "raw")
network = {
    stride: {key: sum(r[key] for r in data["network"] if r["stride"] == stride)
             for key in ("generated", "attempted", "received", "hard_alerts_generated",
                         "hard_alerts_received", "sensor_tx_s", "sensor_listen_s", "sensor_cpu_s")}
    for stride in (1, 3)
}
slm_latency = [r["latency_ms"] for r in audit]
all_calls = sum(int(r["slm_calls"]) for r in metrics if r["scenario"] == "paired" and r["policy"] == "jev-all")
gated_calls = sum(int(r["slm_calls"]) for r in metrics if r["scenario"] == "paired" and r["policy"] == "jev-gated")
gated_correct = sum(r["value"] == r["reference"] for r in decisions
                    if r["scenario"] == "paired" and r["policy"] == "jev-gated" and r["source"] == "model")
on_time = sum(int(r["alert_on_time"]) for r in metrics if r["scenario"] == "paired" and r["policy"] == "jev-gated")
alert_count = network[1]["hard_alerts_generated"]
gated_interval = data["paired_vs_raw"]["jev-gated"]["uplink_savings_pct"]
story = []


def p(text, style="body"):
    story.append(Paragraph(text, styles[style]))


def h(text):
    p(text, "heading")


def section(kicker, title):
    story.append(PageBreak())
    p(kicker, "eyebrow")
    p(title, "section")


def table(headers, rows, widths):
    cells = [[Paragraph(escape(str(v)), styles["column"]) for v in headers]]
    cells += [[Paragraph(escape(str(v)), styles["cell"]) for v in row] for row in rows]
    grid = Table(cells, colWidths=[WIDTH * w for w in widths], repeatRows=1, hAlign="LEFT")
    grid.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, PALE]),
        ("LINEBELOW", (0, -1), (-1, -1), 0.6, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.extend((grid, Spacer(1, 9)))


def figure(name, height, caption):
    story.append(Image(str(ROOT / "docs" / "figures" / name), width=WIDTH, height=height))
    p(caption, "small")


def pct(value):
    return "-" if value is None else f"{100 * value:.1f}%"


def ref(text, url):
    p(f'{escape(text)} <a color="#18786f" href="{escape(url)}">Source</a>', "small")


def footer(canvas, document):
    width, height = A4
    canvas.setStrokeColor(LINE)
    canvas.line(52, 45, width - 52, 45)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawString(52, 30, "COMPUTER NETWORKS II   /   TOPIC 3   /   FIRST ITERATION")
    canvas.drawRightString(width - 52, 30, str(document.page))
    canvas.setFillColor(TEAL)
    canvas.rect(52, height - 45, 44, 3, fill=1, stroke=0)


p("COMPUTER NETWORKS II   /   TOPIC 3   /   12 NOVEMBER 2026", "eyebrow")
p("Semantic caching and<br/>aggregation at the<br/>WSN edge", "title")
p("A Contiki-NG/Cooja evaluation of a small language model and a Joint Energy-Value policy")
table(
    ("Backhaul bytes", "Hard alerts within 2 s", "SLM calls avoided"),
    ((f'{gated_interval["mean"]:.1f}% lower than raw', f"{on_time}/{alert_count} with gated JEV",
      f"{100 * (1 - gated_calls / all_calls):.1f}% with gating"),),
    (0.34, 0.34, 0.32),
)
h("Abstract")
p(
    "This pilot evaluates fog-side retention, aggregation, and forwarding for environmental telemetry. "
    "Five Contiki-NG motes sent CoAP over IPv6/RPL-Lite in three Cooja layouts. Nine paired baseline traces "
    "were replayed through seven gateway policies. A quantized Qwen2.5-0.5B model assigned a semantic value "
    "to observed windows, and JEV used the value together with a backhaul cost proxy and a cache penalty. "
    f"Gated JEV retained {pct(paired['jev-gated']['cloud_query_coverage'])} cloud-window coverage while "
    f"reducing bytes by {gated_interval['mean']:.1f}% versus raw forwarding. Fixed aggregation saved more "
    f"bytes and represented more samples. The SLM overpredicted criticality and produced the same gated JEV "
    "actions as the heuristic at the default cost weight. A separate mote-reporting intervention reduced "
    "radio transmit time at the cost of much lower sample retention."
)
h("Research question")
p(
    "Can semantic scoring improve the trade-off between backhaul traffic, retention of investigation detail, "
    "and timely answers under a bounded fog cache? We compare a numeric heuristic, always-on inference, and "
    "inference limited to ambiguous windows. The immediate alert path is held fixed across policies."
)
h("Scope of the evidence")
p(
    "The firmware and wireless traffic ran in Cooja. The gateway consumed collector traces and ran real SLM "
    "inference on the WSL2 host. Backhaul is a binary-size accounting model. Embedded fog energy and live "
    "gateway-to-cloud transport remain unmeasured."
)
p("Keywords  Wireless sensor networks, CoAP, edge computing, data aggregation, semantic scoring", "small")

section("01   SYSTEM DESIGN", "From CoAP readings to fog decisions")
table(
    ("Component", "Function"),
    (
        ("Sensor motes", "Produce temperature and relative humidity with source IDs and sequence numbers. The trace logs generation and UDP send attempts separately."),
        ("Cooja collector", "Receive non-confirmable CoAP POST /telemetry on UDP 5685. Decode versioned 13-byte telemetry and log collector arrival time."),
        ("Fog replay", "Match each reading to its event-time window, maintain a bounded raw cache, and dispatch upstream records under one of seven policies."),
        ("SLM scorer", "Infer ordinal semantic value from measured window features and the lab/hall context. The gateway validates one-digit output and falls back to the heuristic on timeout or invalid output."),
        ("JEV decision", "Choose cache only, summary, or raw forwarding. At 32 C and above, individual readings bypass inference and take the immediate raw path."),
    ), (0.23, 0.77),
)
h("Semantic value")
p(
    "The feature vector contains zone, sample and sensor counts, temperature minimum, maximum and mean, and "
    "humidity mean and maximum. Scores 0, 1 and 2 denote routine, investigation, and critical windows. "
    "Reference labels use the full generated trace. A 32 C reading is critical. Lab investigation includes "
    "two sensors at 26 C and 54% RH or a reading at 29.5 C. Any zone can enter investigation at 29 C and "
    "58% RH on a single reading. The gateway model sees collector arrivals only."
)
h("JEV utility")
p(
    "Let v be semantic value divided by two, n the observed count, r the count below the hard threshold, "
    "and w the energy weight. The gateway maximizes the following pilot utilities. In a window with no "
    "routine records, all readings have already taken the immediate path."
)
table(
    ("Action", "Utility"),
    (
        ("Cache locally", "0.36 + 0.06v - 0.018 min(n, 20)"),
        ("Summary", "0.32 + 0.32v + 0.14 min(1, n/10) - w E(summary) - 0.025"),
        ("Raw forwarding", "0.36 + 0.92v - w r E(raw)"),
    ), (0.29, 0.71),
)
p(
    "E(payload) = 0.16 + 0.004(48 + payload bytes) mJ is an assumed marginal backhaul cost. "
    "The cache term approximates memory pressure. The summary term includes a fixed processing penalty. "
    "These coefficients are design parameters. The model does not estimate them."
)
h("Aggregation and storage")
p(
    "The summary carries count, sums, minima, maxima and the newest timestamp of the records it represents. "
    "Hard-alert records travel separately and are excluded from summaries. A receiver can merge the two "
    "representations by adding counts and sums, then compute a correctly weighted mean. TTL eviction uses "
    "sample time. JEV caches additionally prefer hard alerts and warm readings when capacity is exceeded."
)

section("02   METHODS", "Paired workloads and measurement")
h("Cooja and the empirical workload")
p(
    "Contiki-NG release/v5.2 ran with Cooja motes, RPL-Lite, CSMA, UDGM, a 48 m transmit range, and a 75 m "
    "interference range. Each layout has one collector and five sensors. Line, mesh, and bottleneck each used "
    "seeds 17, 29, and 41 at receive ratios 1.00, 0.93, and 0.86 respectively. Each was repeated at reporting "
    "stride three. Runs last 240 simulated seconds. Generation starts after about 65 s and includes short "
    "bursts, warm/humid excursions, and hard alerts. Seed and receive ratio are coupled in this pilot."
)
p(
    "The Intel Lab check selects node IDs 1-8 from 1 March 2004, 12:00-12:10, and valid temperature and RH. "
    f"It yields {intel_raw['generated']} readings in {intel_raw['windows']} zone-windows. The replay adds "
    "a uniform 50 ms arrival delay and uses node parity for proxy zones. This slice supplies a routine "
    "empirical workload with no labeled hard alerts."
)
h("Controls")
table(
    ("Policy", "Decision input"),
    (
        ("Raw / fixed / rule", "Raw on arrival. Fixed summary on close. Rule forwards windows tagged by the numeric heuristic and summarizes the rest."),
        ("JEV heuristic / reference", "Numeric score from observed telemetry or reference score from the complete trace. The reference variant estimates the effect of correct labels under the same utility."),
        ("JEV all / gated", "Qwen2.5-0.5B-Instruct Q4_K_M on every observed window or on windows with a measured maximum from 25 C up to 32 C. Other gated windows use the heuristic."),
    ), (0.29, 0.71),
)
h("Outcome definitions")
p(
    "Twenty-second windows accept arrivals until end + 2 s and are queried at end + 4 s. The raw cache "
    "holds 20 readings for 75 s in the primary runs. Fresh local hits must return the latest generated "
    "reading within 12 s. The Intel replay uses 60 s windows, a 180 s TTL and a 45 s freshness bound. "
    "Sensitivity runs use a six-record cache and JEV weights 0.06 and 0.35 around the primary weight 0.18."
)
p(
    "Cloud availability is the fraction of nonempty reference windows with any represented sample by query "
    "time. Count coverage averages the fraction of generated samples represented in each window. "
    "Investigation raw coverage is the fraction of generated records in labeled investigation or critical "
    "windows available as raw data by query time. Temperature MAE and sample age use answered windows. "
    "Uncertainty intervals resample the nine paired traces 4,000 times and are descriptive."
)
h("Pilot criteria")
p(
    "H1 targets 50% byte savings, unchanged on-time hard-alert delivery, and at least 90% cloud availability. "
    "H2 targets at least half the SLM calls avoided and semantic accuracy within five percentage points of "
    "always-on inference. H3 checks whether stride three changes transmit time and sample retention."
)

section("03   NETWORK RESULTS", "Reporting rate and radio activity")
pilot = next(row for row in data["network"] if row["topology"] == "line" and row["seed"] == 17 and row["stride"] == 1)
p(
    f"The line baseline generated {pilot['generated']} samples, attempted {pilot['attempted']} sends and "
    f"received {pilot['received']} datagrams. The root received records from sources "
    f"{', '.join(map(str, pilot['multihop_source_ids']))}, which lie outside direct root range. The parser "
    "checked source/sequence identity, payload equality, arrival ordering, and Energest snapshots from all "
    "six motes. Repeating the pilot produced identical trace and Energest files. Within each stride pair, "
    "source IDs, sequences, and readings matched, with sample timestamps up to 44 ms apart."
)
table(
    ("Totals over nine runs per stride", "Stride 1", "Stride 3"),
    (
        ("Generated readings", f"{network[1]['generated']:,}", f"{network[3]['generated']:,}"),
        ("Attempted / received", f"{network[1]['attempted']:,} / {network[1]['received']:,}",
         f"{network[3]['attempted']:,} / {network[3]['received']:,}"),
        ("Received / generated", pct(network[1]["received"] / network[1]["generated"]),
         pct(network[3]["received"] / network[3]["generated"])),
        ("Hard alerts received / generated", f"{network[1]['hard_alerts_received']} / {alert_count}",
         f"{network[3]['hard_alerts_received']} / {network[3]['hard_alerts_generated']}"),
        ("Sensor TX time, s", f"{network[1]['sensor_tx_s']:.3f}", f"{network[3]['sensor_tx_s']:.3f}"),
        ("Sensor listen time, s", f"{network[1]['sensor_listen_s']:,.3f}", f"{network[3]['sensor_listen_s']:,.3f}"),
    ), (0.52, 0.24, 0.24),
)
figure("network-reporting.png", WIDTH * 2.45 / 7.1,
       "Figure 1. The thin lines link paired seeds. The heavier lines show each topology mean. "
       "The transmit totals include routing/control and retransmission activity recorded by Energest.")
h("Interpretation")
p(
    f"Stride three reduced attempts by {pct(1 - network[3]['attempted'] / network[1]['attempted'])} and "
    f"transmit time by {pct(1 - network[3]['sensor_tx_s'] / network[1]['sensor_tx_s'])}. All attempted "
    "datagrams reached the root under the selected conditions. Warm-up reachability and the reporting "
    "stride account for the gap between generated samples and sends. Lower offered traffic preserved "
    "the 32 C bypass here and left far fewer routine samples for queries."
)
p(
    "Radio listen time dominated under the simulated CSMA configuration. Energest state durations describe "
    "where time went. A conversion to joules requires voltage and current coefficients for a chosen board "
    "and radio. Comparing the gateway policies on a shared arrival trace leaves mote state times fixed."
)

section("04   GATEWAY RESULTS", "Backhaul savings and retained detail")
figure("gateway-tradeoff.png", WIDTH * 2.85 / 7.1,
       "Figure 2. Nine paired Cooja traces at stride one. Right panel averages the fraction of samples "
       "represented by each cloud answer, counting an absent answer as zero.")
policy_labels = {
    "raw": "Raw", "fixed": "Fixed", "rule": "Numeric rule", "jev-heuristic": "JEV heuristic",
    "jev-reference": "JEV reference", "jev-all": "JEV all", "jev-gated": "JEV gated",
}
table(
    ("Policy", "Backhaul B", "Cloud windows", "Review raw", "Temp. MAE"),
    tuple((label, f"{paired[key]['uplink_bytes']:,.0f}", pct(paired[key]["cloud_query_coverage"]),
           pct(paired[key]["review_raw_coverage"]), f"{paired[key]['cloud_temp_mae_c']:.3f} C")
          for key, label in policy_labels.items()),
    (0.28, 0.17, 0.19, 0.18, 0.18),
)
h("Primary comparison")
p(
    f"Gated JEV met H1 on the selected Cooja workload. Paired byte savings were {gated_interval['mean']:.1f}% "
    f"(descriptive 95% interval {gated_interval['lo']:.1f}-{gated_interval['hi']:.1f}%). Raw and gated JEV "
    f"both delivered {on_time}/{alert_count} hard alerts by 2 s. Gated JEV exposed "
    f"{pct(paired['jev-gated']['review_raw_coverage'])} of investigation and critical-window readings as raw. "
    f"The numeric rule exposed {pct(paired['rule']['review_raw_coverage'])} while consuming more backhaul. "
    "The minimum raw investigation coverage belongs in the next version's service constraints."
)
h("Cost and cache sensitivity")
p(
    f"With gated JEV, the lower cost weight of 0.06 raised investigation raw coverage to "
    f"{pct(sweep[0.06]['review_raw_coverage'])} at {sweep[0.06]['uplink_bytes'] / 1000:.2f} kB per trace. "
    f"The weight of 0.35 reduced it to {pct(sweep[0.35]['review_raw_coverage'])} at "
    f"{sweep[0.35]['uplink_bytes'] / 1000:.2f} kB. At six cache slots, "
    f"JEV priority achieved {pct(stress['jev-gated']['local_fresh_raw_hit_rate'])} fresh local hits versus "
    f"{pct(stress['fixed']['local_fresh_raw_hit_rate'])} for FIFO. At 20 slots both achieved "
    f"{pct(paired['jev-gated']['local_fresh_raw_hit_rate'])}. Priority retention at small capacity "
    "crowded out recent ordinary readings."
)
p(
    f"On the Intel slice, fixed aggregation answered all 20 windows using {intel['fixed']['uplink_bytes']:,.0f} B. "
    f"Gated JEV used {intel['jev-gated']['uplink_bytes']:,.0f} B and answered "
    f"{pct(intel['jev-gated']['cloud_query_coverage'])} of windows. H1 therefore depends on workload density."
)

section("05   MODEL RESULTS", "Selective inference under class bias")
paired_all = [r for r in decisions if r["scenario"] == "paired" and r["policy"] == "jev-all"]
confusion = Counter((int(r["reference"]), int(r["value"])) for r in paired_all)
table(
    ("Reference class", "Predicted routine", "Predicted investigate", "Predicted critical"),
    tuple((label, *(confusion[(idx, col)] for col in range(3)))
          for idx, label in enumerate(("Routine", "Investigate", "Critical"))),
    (0.26, 0.24, 0.26, 0.24),
)
p(
    f"Always-on accuracy was {pct(paired['jev-all']['decision_score_accuracy'])} across {all_calls} "
    f"Cooja windows. Gating made {gated_calls} calls and achieved "
    f"{pct(paired['jev-gated']['decision_score_accuracy'])} overall because the non-ambiguous windows "
    f"went to the heuristic. On the gated subset the model classified {gated_correct} of {gated_calls} windows correctly. "
    f"The numeric heuristic matched all {all_calls} labels in this particular synthetic pilot. "
    "H2 cleared its stated comparison with always-on inference. The stronger heuristic comparison "
    "found no added semantic value."
)
h("Why a valid output can still be wrong")
p(
    "The grammar limits the response to 0, 1, or 2, but the model called most subcritical windows critical. "
    "Numeric hard alerts are determined directly from incoming measurements, so this bias did not affect "
    "the alert bypass. It did affect the semantic score, especially at the lower energy weight. At the "
    "default weight, gated and heuristic JEV selected the same actions on the Cooja pilot."
)
h("Local inference cost")
table(
    ("Measurement", "Observed"),
    (
        ("Host and runtime", "AMD Ryzen 7 5800H, WSL2, 4 CPU inference threads, llama.cpp b12318"),
        ("Unique inference requests", f"{len(audit)} including the Intel check"),
        ("Transport or validation failures", f"{provenance['unique_inference_failures']}"),
        ("Median / 95th percentile latency", f"{median(slm_latency):.0f} / {quantiles(slm_latency, n=20)[18]:.0f} ms"),
        ("Server peak RSS", f"{provenance['model_process_peak_rss_kib'] / 1024:.0f} MiB"),
        ("Energy", "Host package counter unavailable. Embedded board energy pending physical measurement."),
    ), (0.47, 0.53),
)
p(
    "Each distinct input was inferred once. Replays of the same window reuse the audited value and "
    "latency to keep variants paired. Reported inference latency is host wall time for the local HTTP "
    "request. It is added to the modeled dispatch time. Queue interactions between windows and board-level "
    "power are outside this measured timing path."
)
h("Model comparison for the next iteration")
p(
    "Create a held-out set with contextual distinctions beyond the synthetic temperature generator. "
    "Compare a numeric heuristic, a small tabular classifier, and a validated language model. "
    "Report class-specific recall, calibration, errors near decision thresholds, and measured cost per query."
)

section("06   LIMITATIONS AND REPRODUCTION", "From pilot to course submission")
h("What the data support")
p(
    "Fixed summaries suit the current routine workload when aggregate answers are sufficient. Raw review "
    "coverage changes the preferred policy. The small cache result motivates reserving recent entries "
    "for freshness and separately prioritizing event history. The synthetic reference shares domain rules "
    "with the heuristic and has few independent traces. An externally labeled workload is needed to assess "
    "generalization and the contribution of the SLM."
)
h("Next experiments")
p(
    "<b>1. Live gateway.</b> Connect a Cooja border router to a fog process and run an actual upstream sink. "
    "Measure loss, queueing, reconnect behavior and end-to-end delay. Distinguish gateway retries from "
    "the source's radio retries."
)
p(
    "<b>2. Embedded measurement.</b> Replay a paired workload on Raspberry Pi or Jetson using a calibrated "
    "inline power meter. Measure idle, inference and whole-gateway energy, plus timing and thermal state. "
    "Use the measured coefficients for JEV and enforce minimum investigation fidelity and freshness."
)
p(
    "<b>3. Stronger validation.</b> Cross seeds and link loss independently, use more sensors and workload "
    "families, and validate firmware on hardware-oriented motes or a testbed. Include the control-message "
    "cost if an adaptive policy changes source reporting."
)
h("Reproduction")
p(
    "The repository includes the firmware, headless scenarios, parsed traces, run scripts, gateway replay, "
    "per-window decisions, model audit, and PDF source. Contiki-NG was pinned to 4ccd1a3b9e76 and Cooja "
    "to fabff3ee41ee. The results contain full trace hashes and model/runtime provenance. "
    f"The result summary SHA-256 begins {sha256((RESULTS / 'summary.json').read_bytes()).hexdigest()[:16]}."
)
h("References")
p("1. Computer Networks II, Optional Project 2026-2027, topic 3, pp. 1-2. Course brief and WSN concepts handout.", "small")
ref("2. Winter et al., RPL, RFC 6550, 2012.", "https://www.rfc-editor.org/rfc/rfc6550")
ref("3. Shelby, Hartke and Bormann, CoAP, RFC 7252, 2014.", "https://www.rfc-editor.org/rfc/rfc7252")
ref("4. Contiki-NG, Energest programming documentation.", "https://github.com/contiki-ng/contiki-ng/blob/develop/doc/programming/Energest.md")
ref("5. Madden et al., TAG: a Tiny AGgregation Service, OSDI 2002.", "https://db.csail.mit.edu/madden/html/madden_tag_html/")
ref("6. Kaul, Yates and Gruteser, Real-Time Status: How Often Should One Update?, INFOCOM 2012.",
    "https://www.iiitd.edu.in/~skkaul/Papers/Infocom2012.pdf")
ref("7. Intel Berkeley Research Lab, Intel Lab Data.", "https://db.csail.mit.edu/labdata/labdata.html")
ref("8. Qwen team, Qwen2.5-0.5B-Instruct-GGUF model card.", "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF")

document = SimpleDocTemplate(
    str(OUTPUT), pagesize=A4, leftMargin=52, rightMargin=52, topMargin=65, bottomMargin=58,
    title="Semantic caching and aggregation at the WSN edge",
    subject="Computer Networks II, topic 3, first-iteration experimental report",
)
document.build(story, onFirstPage=footer, onLaterPages=footer)
print(OUTPUT)
