"""Scan a parent CIDR for child networks that have discoverydata-populated
IPs, then mark those networks' comments with a (DISCOVERED) prefix so
they're easy to spot in IPAM's network list view.

Designed to be both a CLI and a function callable from chunk_push_discovery
so the marker is applied automatically after a chunked push completes.

Idempotent: if the comment already starts with `(DISCOVERED)`, it's left
alone. If a network was previously marked but now has 0 discoverydata
rows, the marker is removed.
"""
from __future__ import annotations
import argparse
import ipaddress
import sys

import requests

from nios_client import NiosClient, load_cfg


DISCOVERED_PREFIX = "(DISCOVERED) "
PARTIAL_PREFIX = "(DISCOVERED-partial) "  # < threshold rows


def _update_comment(cli: NiosClient, net_ref: str, new_comment: str) -> bool:
    r = requests.put(cli.base + net_ref, json={"comment": new_comment},
                      auth=cli.auth, verify=cli.verify, timeout=30)
    return r.ok


def scan_and_mark(host_cidr: str, view: str = "default",
                   threshold_pct: float = 5.0,
                   dry_run: bool = False) -> dict:
    """Walk every child network of `host_cidr` and add/remove the
    (DISCOVERED) marker on the comment based on whether the network has
    discoverydata-populated IPs.

    threshold_pct: a network needs at least this percentage of its
    address space populated to get the full `(DISCOVERED)` marker.
    Below that and above zero gets `(DISCOVERED-partial)`. Exactly zero
    gets the marker removed (if previously applied).

    Returns a summary dict.
    """
    cli = NiosClient(load_cfg(), allow_writes=True)
    if cli.read_only:
        return {"error": "read-only grid"}

    parent_net = ipaddress.ip_network(host_cidr, strict=False)
    all_nets = cli.get("network", network_view=view,
                        _return_fields="network,comment")
    children = [n for n in all_nets
                if ipaddress.ip_network(n["network"]).subnet_of(parent_net)
                and n["network"] != host_cidr]

    summary = {"scanned": 0, "marked_full": 0, "marked_partial": 0,
                "marker_removed": 0, "unchanged": 0,
                "host_cidr": host_cidr}

    for net in children:
        cidr = net["network"]
        rows = cli.get("ipv4address", network=cidr, network_view=view,
                        _max_results=20000,
                        _return_fields="ip_address,discovered_data")
        dd_count = sum(1 for r in rows if r.get("discovered_data"))
        total = len(rows)
        pct = (100.0 * dd_count / total) if total else 0.0

        current = net.get("comment", "") or ""
        # Strip any existing marker before deciding what to add
        stripped = current
        for p in (DISCOVERED_PREFIX, PARTIAL_PREFIX):
            if stripped.startswith(p):
                stripped = stripped[len(p):]

        if dd_count == 0:
            target = stripped
        elif pct >= threshold_pct:
            target = DISCOVERED_PREFIX + stripped
        else:
            target = PARTIAL_PREFIX + stripped

        summary["scanned"] += 1
        if target == current:
            summary["unchanged"] += 1
            continue

        label = ("full" if target.startswith(DISCOVERED_PREFIX)
                 else "partial" if target.startswith(PARTIAL_PREFIX)
                 else "remove")
        print(f"  {cidr:18s} dd={dd_count:>5}/{total:>5} ({pct:>5.1f}%)"
              f"  -> {label}")
        if not dry_run:
            ok = _update_comment(cli, net["_ref"], target)
            if not ok:
                print(f"    WARN: PUT failed for {cidr}")
        if target.startswith(DISCOVERED_PREFIX):
            summary["marked_full"] += 1
        elif target.startswith(PARTIAL_PREFIX):
            summary["marked_partial"] += 1
        else:
            summary["marker_removed"] += 1

    return summary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host_cidr", help="parent CIDR; e.g. 198.18.0.0/16")
    ap.add_argument("--view", default="default")
    ap.add_argument("--threshold", type=float, default=5.0,
                    help="percent of network needing discoverydata to get the "
                         "full (DISCOVERED) marker (default 5.0)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    result = scan_and_mark(args.host_cidr, view=args.view,
                            threshold_pct=args.threshold,
                            dry_run=args.dry_run)
    print(f"\n[mark-discovered] {result}")
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
