"""Unified CLI for the nios_discovery_csv toolkit.

  python cli.py schema                  Print the merged schema + evidence
  python cli.py field <name>            Describe a single field
  python cli.py accepted                List CSV header names empirically confirmed to import
  python cli.py themes                  List available themes
  python cli.py generate <class>        Print a populated CSV for a device class
        --theme darknet|bsg|tng|random
        --ip 198.51.100.10
        --count 1
  python cli.py templates               List the canonical golden templates
"""
import argparse
import csv
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _load_schema():
    return json.load(open(f"{HERE}/schema/discovery_schema.json"))


def cmd_schema(args):
    s = _load_schema()
    print(f"version: {s.get('version')}")
    print(f"source : {s.get('source')}")
    ev = s["csv_import_evidence"]
    print(f"\nempirical: {len(ev['confirmed_accepted_csv_names'])} accepted, "
          f"{len(ev['confirmed_silently_dropped_csv_names'])} silently dropped, "
          f"{len(ev['confirmed_rejected_row_csv_names'])} rejected_row")
    print(f"CSV-mapped fields: {len(s['csv_fields'])}")
    print(f"WAPI-only fields : {len(s['wapi_only_fields'])}")


def cmd_field(args):
    s = _load_schema()
    name = args.name
    if name in s["csv_fields"]:
        print(json.dumps(s["csv_fields"][name], indent=2))
    elif name in s["wapi_only_fields"]:
        print(json.dumps(s["wapi_only_fields"][name], indent=2))
    else:
        print(f"unknown field: {name}")
        return 1


def cmd_accepted(args):
    s = _load_schema()
    for n in s["csv_import_evidence"]["confirmed_accepted_csv_names"]:
        print(n)
    print("--- WAPI internal names (recommended for any new CSV header) ---")
    # Every wapi_only field plus the 12 confirmed-accepted is the safe set
    safe = set(s["csv_import_evidence"]["confirmed_accepted_csv_names"])
    safe.update(s["wapi_only_fields"].keys())
    for n in sorted(safe):
        print(n)


def cmd_themes(args):
    import themes
    for t in themes.THEMES:
        print(t)


# Virtual host / VM templates were removed in v0.4 — every virtualization
# field (vmhost_*, vmi_*, vport_*, vswitch_*, v_*) is silently dropped by the
# CSV importer. That data flows only via the hypervisor / SDN discovery
# integrations, not via fileop/setdiscoverycsv. See
# `memory/project_canonical_csv_schema.md`.
_CLASS_TEMPLATES = {
    "l3_switch":    "template_l3_switch.csv",
    "wireless_ap":  "template_wireless_ap.csv",
    "endpoint":     "template_endpoint.csv",
}


def cmd_templates(args):
    for k, v in _CLASS_TEMPLATES.items():
        print(f"  {k:12s} -> templates/{v}")


def cmd_generate(args):
    """Read the template, apply theme overrides, optionally rewrite ip_address."""
    import themes as themes_mod
    tpath = f"{HERE}/templates/{_CLASS_TEMPLATES[args.cls]}"
    rows = list(csv.DictReader(open(tpath)))
    rng = None
    if args.theme:
        import random
        rng = random.Random(args.seed) if args.seed is not None else random.Random()
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    yest = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 86400))
    output_rows = []
    base_octet = int(args.ip.split(".")[-1]) if args.ip else 10
    for i in range(args.count):
        for r in rows:
            r = dict(r)  # copy
            r["first_discovered_timestamp"] = yest
            r["last_discovered_timestamp"] = now
            if args.ip:
                ipparts = args.ip.split(".")
                ipparts[-1] = str(base_octet + i)
                r["ip_address"] = ".".join(ipparts)
            if args.theme:
                for k in list(r.keys()):
                    v = themes_mod.apply(args.theme, k, args.cls, rng=rng)
                    if v is not None:
                        r[k] = v
            output_rows.append(r)
    # write CSV to stdout (or -o file)
    fields = list(output_rows[0].keys())
    out = sys.stdout if args.output == "-" else open(args.output, "w", newline="")
    w = csv.DictWriter(out, fieldnames=fields)
    w.writeheader()
    for r in output_rows:
        w.writerow(r)
    if args.output != "-":
        print(f"wrote {args.output} ({len(output_rows)} rows)", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("schema", help="show schema summary").set_defaults(fn=cmd_schema)
    sp = sub.add_parser("field", help="describe a single field")
    sp.add_argument("name")
    sp.set_defaults(fn=cmd_field)
    sub.add_parser("accepted", help="list CSV header names confirmed to import").set_defaults(fn=cmd_accepted)
    sub.add_parser("themes", help="list themes").set_defaults(fn=cmd_themes)
    sub.add_parser("templates", help="list golden templates by class").set_defaults(fn=cmd_templates)

    sp = sub.add_parser("generate", help="emit a populated CSV for a device class")
    sp.add_argument("cls", choices=list(_CLASS_TEMPLATES))
    sp.add_argument("--theme", choices=("darknet", "bsg", "tng", "random"))
    sp.add_argument("--ip", help="base IP; last octet incremented per --count")
    sp.add_argument("--count", type=int, default=1)
    sp.add_argument("--seed", type=int, default=None)
    sp.add_argument("--output", default="-", help="output CSV path (default: stdout)")
    sp.set_defaults(fn=cmd_generate)

    sp = sub.add_parser("simulate",
                        help="generate a whole simulated IPAM network — "
                             "convincing or pop-culture")
    sp.add_argument("network", help="CIDR, e.g. 198.18.0.0/24")
    sp.add_argument("--fill", type=float, default=50.0,
                    help="percent of usable IPs to fill (default 50)")
    sp.add_argument("--mode", choices=("realistic", "nerd"),
                    default="realistic",
                    help="realistic=generic corp; nerd=Matrix/LOTR/Sneakers; "
                         "")
    sp.add_argument("--seed", type=int, default=None,
                    help="deterministic seed")
    sp.add_argument("--discoverer", default="simulator")
    sp.add_argument("-o", "--output", default="-",
                    help="output CSV path (default: stdout)")
    sp.set_defaults(fn=cmd_simulate)

    args = ap.parse_args()
    return args.fn(args) or 0


def cmd_simulate(args):
    import simulator
    rows = simulator.simulate_network(args.network, fill_pct=args.fill,
                                       mode=args.mode, seed=args.seed,
                                       discoverer=args.discoverer)
    if args.output == "-":
        import csv as _csv
        cols = sorted({k for r in rows for k in r.keys()})
        w = _csv.DictWriter(sys.stdout, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    else:
        simulator.write_csv(rows, args.output)
        print(f"wrote {len(rows)} rows to {args.output}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
