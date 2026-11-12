"""Generate headless Cooja configurations and parse logged CoAP/Energest events."""

import argparse
from collections import Counter
import csv
import json
from math import dist
from pathlib import Path
import re
import xml.etree.ElementTree as ET


TOPOLOGIES = {
    "line": [(0, 0), (35, 0), (70, 0), (105, 0), (140, 0), (175, 0)],
    "mesh": [(0, 0), (28, 10), (22, -25), (56, -4), (56, 29), (80, -23)],
    "bottleneck": [(0, 0), (39, 0), (73, 0), (81, -22), (83, 23), (111, 0)],
}
RADIO_RANGE = 48
INTERFACES = (
    "interfaces.Position",
    "contikimote.interfaces.ContikiMoteID",
    "contikimote.interfaces.ContikiRS232",
    "contikimote.interfaces.ContikiIPAddress",
    "contikimote.interfaces.ContikiRadio",
    "contikimote.interfaces.ContikiClock",
)
MARKER = re.compile(r"^(\d+)\s+ID:(\d+)\s+.*?WSN,([A-Z]+),([\d,]+)")
TRACE_FIELDS = ("node", "seq", "sample_s", "arrival_s", "temperature_c", "humidity_pct", "zone")


def config(path, topology, seed, rx_ratio, stride):
    root = ET.Element("simconf")

    def add(parent, key, value=None):
        item = ET.SubElement(parent, key)
        if value is not None:
            item.text = str(value)
        return item

    sim = add(root, "simulation")
    add(sim, "title", f"WSN edge {topology} / seed {seed} / stride {stride}")
    add(sim, "randomseed", seed)
    add(sim, "motedelay_us", 1000000)
    radio = add(sim, "radiomedium", "org.contikios.cooja.radiomediums.UDGM")
    add(radio, "transmitting_range", RADIO_RANGE)
    add(radio, "interference_range", 75)
    add(radio, "success_ratio_tx", 1.0)
    add(radio, "success_ratio_rx", rx_ratio)
    add(add(sim, "events"), "logoutput", 40000)
    for app in ("edge-root", "sensor"):
        mote_type = add(sim, "motetype", "org.contikios.cooja.contikimote.ContikiMoteType")
        add(mote_type, "identifier", app)
        add(mote_type, "description", app)
        add(mote_type, "source", f"[CONFIG_DIR]/firmware/{app}.c")
        add(mote_type, "commands", f"$(MAKE) -j4 {app}.cooja TARGET=cooja WSN_REPORT_STRIDE={stride}")
        for interface in INTERFACES:
            add(mote_type, "moteinterface", f"org.contikios.cooja.{interface}")
    for node, (x, y) in enumerate(TOPOLOGIES[topology], start=1):
        mote = add(sim, "mote")
        position = add(mote, "interface_config", "org.contikios.cooja.interfaces.Position")
        add(position, "x", x)
        add(position, "y", y)
        add(position, "z", 0)
        mote_id = add(mote, "interface_config", "org.contikios.cooja.contikimote.interfaces.ContikiMoteID")
        add(mote_id, "id", node)
        add(mote, "motetype_identifier", "edge-root" if node == 1 else "sensor")
    plugin = add(root, "plugin", "org.contikios.cooja.plugins.ScriptRunner")
    script = add(plugin, "plugin_config")
    add(script, "script", """TIMEOUT(240000, log.testOK());
while (true) {
  YIELD();
  if (msg.indexOf("WSN,") >= 0) {
    log.log(time + " ID:" + id + " " + msg + "\\n");
  }
}
""")
    add(script, "active", "true")
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def parse(log, topology, seed, rx_ratio, stride, out):
    generated, received = {}, {}
    sends, energy = {}, {}
    duplicates = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        match = MARKER.match(line)
        if not match:
            continue
        time, mote, event, fields = match.groups()
        time, mote = int(time) / 1_000_000, int(mote)
        values = tuple(map(int, fields.split(",")))
        if event == "ENERGY":
            energy[mote] = (time, *values)
            continue
        key = values[:2]
        if event == "GEN":
            if key in generated or mote != key[0]:
                raise ValueError(f"Invalid generation event for {key}")
            generated[key] = (time, values[2:])
        elif event == "SEND":
            sends[key] = values[2]
        elif event == "RECV":
            if mote != 1:
                raise ValueError(f"Unexpected collector {mote}")
            if key in received:
                duplicates += 1
            received.setdefault(key, (time, values[2:]))
    if not generated or not sends or not received or len(energy) != len(TOPOLOGIES[topology]):
        raise ValueError(f"Incomplete Cooja run: {len(generated)=}, {len(sends)=}, {len(received)=}, {len(energy)=}")
    if not received.keys() <= sends.keys() <= generated.keys():
        raise ValueError("Cooja event IDs are inconsistent")
    coords = TOPOLOGIES[topology]
    multihop = sorted({node for node, _ in received if dist(coords[0], coords[node - 1]) > RADIO_RANGE})
    if not multihop:
        raise ValueError("No sender outside the collector's direct radio range reached it")

    out.mkdir(parents=True, exist_ok=True)
    with (out / "trace.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(TRACE_FIELDS)
        for key, (sample_time, payload) in sorted(generated.items(), key=lambda pair: pair[1][0]):
            arrival, observed = received.get(key, ("", payload))
            if observed != payload or arrival != "" and arrival < sample_time:
                raise ValueError(f"Inconsistent payload or time for {key}")
            writer.writerow((*key, sample_time, arrival, payload[1] / 100, payload[2] / 100,
                             "lab" if key[0] % 2 == 0 else "hall"))
    with (out / "energy.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(("node", "at_s", "ticks_per_s", "cpu", "lpm", "deep_lpm", "tx", "listen"))
        writer.writerows((node, *values) for node, values in sorted(energy.items()))
    alert_keys = {key for key, (_, payload) in generated.items() if payload[1] >= 3200}
    data = {
        "topology": topology, "seed": seed, "rx_ratio": rx_ratio, "stride": stride,
        "generated": len(generated), "attempted": len(sends), "received": len(received),
        "duplicates": duplicates, "source_datagram_bytes": sum(sends.values()),
        "sent_by_node": dict(Counter(key[0] for key in sends)),
        "hard_alerts_generated": len(alert_keys), "hard_alerts_received": len(alert_keys & received.keys()),
        "multihop_source_ids": multihop,
    }
    (out / "network.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("config", "parse"))
    parser.add_argument("path", type=Path)
    parser.add_argument("topology", choices=TOPOLOGIES)
    parser.add_argument("seed", type=int)
    parser.add_argument("rx_ratio", type=float)
    parser.add_argument("stride", type=int, choices=(1, 3))
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if not 0 < args.rx_ratio <= 1:
        parser.error("rx_ratio must be in (0, 1]")
    if args.action == "config":
        config(args.path, args.topology, args.seed, args.rx_ratio, args.stride)
    else:
        if args.out is None:
            parser.error("parse requires --out")
        parse(args.path, args.topology, args.seed, args.rx_ratio, args.stride, args.out)


if __name__ == "__main__":
    main()
