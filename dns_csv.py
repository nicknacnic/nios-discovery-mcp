"""Generate NIOS CSV-import bundles for DNS objects matching the simulator
output. Two CSV files:

  dns_zones.csv     — forward + reverse auth zones (one row per zone)
  dns_records.csv   — host records (one row per simulated host; A+PTR auto-created)

Uses the standard NIOS CSV import path (fileop?_function=csv_import), NOT
setdiscoverycsv. This path is robust to millions of rows per Infoblox docs.

Reference: NIOS Administrator Guide → "CSV Import Reference"
"""
from __future__ import annotations
import csv
import ipaddress
from typing import Iterable


# ---------- Zone discovery ------------------------------------------------

def forward_zones_for_rows(rows: Iterable[dict]) -> set[str]:
    """Collect the union of DNS zones needed to host every `discovered_name`.
    A zone is the second-level domain and up: for `lap.sjhq.prd.corp.luminarysys.com`,
    the zone we'll create is `corp.luminarysys.com` (we put all host records at
    that level for simplicity; the deeper labels become hostname prefixes)."""
    zones = set()
    for r in rows:
        host = r.get("discovered_name")
        if not host or "." not in host:
            continue
        # Use the last 3 labels as the auth zone — e.g.
        # lap3499.sjhq.prd.corp.luminarysys.com  -> luminarysys.com (last 2)?
        # We want corp.luminarysys.com as the zone so that hostnames
        # nest beneath it. So pick the last 3 labels.
        parts = host.split(".")
        if len(parts) >= 4:
            zones.add(".".join(parts[-3:]))   # corp.luminarysys.com
        elif len(parts) >= 2:
            zones.add(".".join(parts[-2:]))
    return zones


def reverse_zones_for_rows(rows: Iterable[dict]) -> set[str]:
    """Build the set of /16 reverse zones (e.g. 64.10.in-addr.arpa) needed
    to cover all the populated IPs. /16 granularity keeps zone count small."""
    zones = set()
    for r in rows:
        ip = r.get("ip_address")
        if not ip:
            continue
        try:
            o1, o2 = str(ip).split(".")[:2]
            zones.add(f"{o2}.{o1}.in-addr.arpa")
        except (ValueError, IndexError):
            continue
    return zones


# ---------- CSV writers ---------------------------------------------------

def write_zones_csv(forward_zones: set, reverse_zones: set, path: str,
                    view: str = "default",
                    fwd_comment: str = "Luminary Systems forward zone (sim)",
                    rev_comment: str = "Luminary Systems reverse zone (sim)") -> None:
    """Write a NIOS CSV-import file containing authoritative zones."""
    cols = ["header-authzone", "fqdn*", "view", "zone_format", "comment"]
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for z in sorted(forward_zones):
            w.writerow(["authzone", z, view, "FORWARD", fwd_comment])
        for z in sorted(reverse_zones):
            w.writerow(["authzone", z, view, "IPV4", rev_comment])


def write_hostrecords_csv(rows: Iterable[dict], path: str,
                          view: str = "default",
                          configure_for_dns: bool = True,
                          configure_for_dhcp: bool = False,
                          comment: str = "luminary-sim") -> int:
    """Write a NIOS CSV-import file with host records (A+PTR atomic).
    Returns row count written."""
    cols = ["header-hostrecord", "fqdn*", "addresses",
            "configure_for_dns", "configure_for_dhcp", "view", "comment"]
    n = 0
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for r in rows:
            host = r.get("discovered_name")
            ip = r.get("ip_address")
            if not host or not ip:
                continue
            w.writerow(["hostrecord", host, ip,
                        str(configure_for_dns).upper(),
                        str(configure_for_dhcp).upper(),
                        view, comment])
            n += 1
    return n


def generate_dns_bundle(discovery_rows: list[dict],
                        zones_path: str,
                        records_path: str,
                        view: str = "default") -> dict:
    """One-shot: turn a list of simulator rows into the two DNS CSVs.
    Returns a summary dict with counts."""
    fwd = forward_zones_for_rows(discovery_rows)
    rev = reverse_zones_for_rows(discovery_rows)
    write_zones_csv(fwd, rev, zones_path, view=view)
    n_recs = write_hostrecords_csv(discovery_rows, records_path, view=view)
    return {"forward_zones": sorted(fwd),
            "reverse_zones": sorted(rev),
            "records": n_recs,
            "zones_csv": zones_path,
            "records_csv": records_path}


if __name__ == "__main__":
    import argparse, json, sys
    ap = argparse.ArgumentParser()
    ap.add_argument("discovery_csv", help="simulator output CSV to mirror as DNS")
    ap.add_argument("--zones-out", default="dns_zones.csv")
    ap.add_argument("--records-out", default="dns_records.csv")
    ap.add_argument("--view", default="default")
    args = ap.parse_args()
    rows = list(csv.DictReader(open(args.discovery_csv)))
    summary = generate_dns_bundle(rows, args.zones_out, args.records_out,
                                   view=args.view)
    print(json.dumps(summary, indent=2))
