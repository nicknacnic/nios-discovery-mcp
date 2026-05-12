"""Build a NIOS network-container hierarchy that mirrors the Luminary
Systems IP scheme onto an arbitrary host CIDR.

Usage:
  python network_hierarchy.py 198.18.0.0/16 [--view default] [--dry-run]

Creates:
  - host CIDR as a networkcontainer (parent)
  - per Luminary overlay site (sjhq /18, aus /20, etc.), a child network
    inside the container

Idempotent — skips creation if a record already exists.

This is fork-only; not part of the upstream PRs. Future work:
extend to create /24 sub-allocations within each site for VLAN scoping.
"""
from __future__ import annotations
import argparse
import sys

import luminary
from nios_client import NiosClient, load_cfg


def ensure_container(cli: NiosClient, cidr: str, view: str, comment: str,
                      dry_run: bool = False) -> str | None:
    existing = cli.get("networkcontainer", network=cidr, network_view=view)
    if existing:
        print(f"  [container] {cidr} already exists")
        return existing[0]["_ref"]
    if dry_run:
        print(f"  [container] would create {cidr}")
        return None
    ref = cli.post("networkcontainer", json_body={
        "network": cidr, "network_view": view, "comment": comment})
    print(f"  [container] created {cidr}  ref={ref}")
    return ref


def ensure_network(cli: NiosClient, cidr: str, view: str, comment: str,
                    dry_run: bool = False) -> str | None:
    existing = cli.get("network", network=cidr, network_view=view)
    if existing:
        print(f"  [network]   {cidr} already exists  ({comment})")
        return existing[0]["_ref"]
    if dry_run:
        print(f"  [network]   would create {cidr}  ({comment})")
        return None
    ref = cli.post("network", json_body={
        "network": cidr, "network_view": view, "comment": comment})
    print(f"  [network]   created {cidr}  ({comment})")
    return ref


def build_hierarchy(host_cidr: str, view: str = "default",
                     dry_run: bool = False) -> dict:
    """Install the Luminary overlay onto `host_cidr` and create matching
    NIOS networkcontainer + child networks. Returns a summary dict."""
    cfg = load_cfg()
    cli = NiosClient(cfg, allow_writes=True)
    if cli.read_only:
        print(f"ERR: {cli.cfg['gm']} is prod-readonly; refusing")
        return {"error": "prod-readonly"}

    sites = luminary.install_overlay(host_cidr)
    print(f"[hierarchy] {len(sites)} Luminary sites overlaid onto {host_cidr}")

    # 1. Parent container
    print(f"\n[hierarchy] step 1: create parent container {host_cidr}")
    ensure_container(cli, host_cidr, view,
                     comment="Luminary Systems demo — sim parent (csv-simulator)",
                     dry_run=dry_run)

    # 2. Per-site child networks
    print(f"\n[hierarchy] step 2: create {len(sites)} child networks")
    created = 0
    skipped = 0
    for s in sites:
        comment = (f"lsys-{s.region}-{s.code}-{s.tier} ({s.name})  "
                   f"[csv-simulator]")
        before = cli.get("network", network=s.cidr, network_view=view)
        ref = ensure_network(cli, s.cidr, view, comment, dry_run=dry_run)
        if before:
            skipped += 1
        elif ref:
            created += 1

    summary = {"parent": host_cidr, "view": view, "sites": len(sites),
                "created": created, "skipped": skipped}
    print(f"\n[hierarchy] DONE: {summary}")
    return summary


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("host_cidr", help="parent /16 (or whatever) to overlay")
    ap.add_argument("--view", default="default")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    result = build_hierarchy(args.host_cidr, view=args.view, dry_run=args.dry_run)
    return 1 if "error" in result else 0


if __name__ == "__main__":
    sys.exit(main())
