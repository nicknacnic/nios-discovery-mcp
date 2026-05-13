"""MCP stdio server wrapping the nios_discovery toolkit.

Exposes:
  describe_field(name)            -> schema entry for a CSV/WAPI field
  list_accepted_csv_names()       -> empirically-confirmed CSV headers
  list_themes()                   -> available easter-egg themes
  list_device_classes()           -> golden template classes
  generate_discovery_csv(...)     -> populated CSV string
  validate_discovery_csv(text)    -> per-row classification using schema
  import_discovery_csv(text, view) -> upload to grid (HARD-BLOCKED on prod IPs)
  recon_grid()                    -> read-only WAPI snapshot

Install:
  pip install mcp        # or the user's preferred MCP SDK
Run:
  python mcp_server.py   # stdio mode; register via ~/.claude.json

This file deliberately does NOT auto-import the `mcp` SDK at module top — it
imports lazily inside main() so the rest of the module is usable as a plain
Python library for unit tests.
"""
import csv
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


# --- Pure functions (no MCP dependency) -----------------------------------

def _schema():
    return json.load(open(f"{HERE}/schema/discovery_schema.json"))


def describe_field(name: str) -> dict:
    s = _schema()
    if name in s["csv_fields"]:
        return {"kind": "csv", **s["csv_fields"][name]}
    if name in s["wapi_only_fields"]:
        return {"kind": "wapi_only", **s["wapi_only_fields"][name]}
    return {"error": f"unknown field {name}"}


def list_accepted_csv_names() -> list:
    s = _schema()
    accepted = set(s["csv_import_evidence"]["confirmed_accepted_csv_names"])
    accepted.update(s["wapi_only_fields"].keys())
    return sorted(accepted)


def list_themes() -> list:
    import themes
    return list(themes.THEMES.keys())


def list_device_classes() -> list:
    return ["l3_switch", "wireless_ap", "virtual_host", "vm", "endpoint"]


def generate_discovery_csv(device_class: str, theme: str = None,
                            base_ip: str = "198.51.100.10",
                            count: int = 1, seed: int = None) -> str:
    """Return a CSV string ready to upload."""
    import themes as themes_mod
    import time, random
    from cli import _CLASS_TEMPLATES
    rng = random.Random(seed) if (theme and seed is not None) else (random.Random() if theme else None)
    rows = list(csv.DictReader(open(f"{HERE}/templates/{_CLASS_TEMPLATES[device_class]}")))
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    yest = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() - 86400))
    base_octet = int(base_ip.split(".")[-1])
    out_rows = []
    for i in range(count):
        for r in rows:
            r = dict(r)
            r["first_discovered_timestamp"] = yest
            r["last_discovered_timestamp"] = now
            ipparts = base_ip.split(".")
            ipparts[-1] = str(base_octet + i)
            r["ip_address"] = ".".join(ipparts)
            if theme:
                for k in list(r.keys()):
                    v = themes_mod.apply(theme, k, device_class, rng=rng)
                    if v is not None:
                        r[k] = v
            out_rows.append(r)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(out_rows[0].keys()))
    w.writeheader()
    for r in out_rows:
        w.writerow(r)
    return buf.getvalue()


def validate_discovery_csv(text: str) -> dict:
    """Classify each header in the CSV against empirical evidence + NetMRI doc.

    Authoritative writable set is the NetMRI IPAM Sync field list confirmed by
    https://docs.infoblox.com/space/NetMRI751/42469405/Data+Collection+Techniques
    plus our probe evidence.

    Hard-rejects rows with invalid enum values on the four port_* enum fields
    that crash vNIOS when invalid (port_status, port_duplex, port_speed,
    port_link_status).
    """
    # Canonical NetMRI IPAM Sync writable CSV columns (doc-confirmed):
    CANONICAL_ACCEPTED = {
        "ip_address", "last_discovered_timestamp", "first_discovered_timestamp",
        "discovered_name", "mac_address", "netbios_name", "os",
        "device_model", "device_vendor", "device_location", "device_contact",
        "oui", "discoverer",
        "network_component_type", "network_component_name",
        "network_component_description", "network_component_ip",
        "network_component_model", "network_component_vendor",
        "network_component_location", "network_component_contact",
        "network_component_port_number", "network_component_port_name",
        "network_component_port_description",
        "port_vlan_name", "port_vlan_number",
        "port_speed", "port_duplex", "port_status", "port_link_status",
        "tenant", "bridge_domain", "endpoint_groups",
        "vrf_name", "vrf_description", "vrf_rd", "bgp_as",
        "ap_name", "ap_ip_address", "ap_ssid",
        "open_ports", "device_type", "device_port_name", "device_port_type",
        "device_management_ip",
    }
    # Enum fields where invalid values crash the grid (port_status confirmed,
    # the other three suspected on the same code path):
    HARD_ENUMS = {
        "port_status":      {"Up", "Down", "Unknown"},
        "port_duplex":      {"Full", "Half"},
        "port_speed":       {"10M", "100M", "1G", "10G", "100G", "Unknown"},
        "port_link_status": {"Connected", "Not Connected", "Unknown"},
    }
    accepted = CANONICAL_ACCEPTED
    reader = csv.reader(io.StringIO(text))
    header = next(reader)
    body_rows = list(reader)
    report = {"row_count": len(body_rows), "header_count": len(header),
              "headers": {}, "hard_enum_violations": []}
    for h in header:
        if h == "ip_address":
            report["headers"][h] = "required-core"
        elif h in ("first_discovered_timestamp", "last_discovered_timestamp",
                   "discoverer", "discovered_name"):
            report["headers"][h] = "accepted-core"
        elif h in HARD_ENUMS:
            report["headers"][h] = f"accepted (enum: {sorted(HARD_ENUMS[h])})"
        elif h in accepted:
            report["headers"][h] = "accepted"
        else:
            report["headers"][h] = ("NOT IN NETMRI CANONICAL CSV SCHEMA — "
                                    "will silently drop OR (for port_* enums) "
                                    "CRASH THE GRID if invalid")
    # Hard enum scan — invalid values on these fields crash the grid (confirmed
    # on .36 with port_status='PROBE_port_status' 2026-05-11).
    for ri, row in enumerate(body_rows):
        for ci, h in enumerate(header):
            if h not in HARD_ENUMS or ci >= len(row):
                continue
            v = row[ci]
            if v == "":
                continue  # empty is fine
            if v not in HARD_ENUMS[h]:
                report["hard_enum_violations"].append({
                    "row": ri + 2,  # 1 for header, 1 to make 1-indexed
                    "column": h,
                    "value": v,
                    "valid": sorted(HARD_ENUMS[h]),
                    "danger": "WILL CRASH vNIOS APPLIANCE",
                })
    return report


def recon_grid() -> dict:
    """Pure read of the grid in gm.ini. Refuses if grid is in PROD_READONLY_GMS — wait, it DOESN'T refuse reads, only writes."""
    from nios_client import NiosClient, load_cfg
    cli = NiosClient(load_cfg(f"{HERE}/gm.ini"))
    schema_doc = cli.get("ipv4address", _schema=1, _schema_version=2, _get_doc=1)
    return {"grid": cli.cfg["gm"], "read_only": cli.read_only,
            "schema_field_count": len(schema_doc.get("fields", []))}


def import_discovery_csv(text: str, network_view: str = "default",
                          target_model: str = None) -> dict:
    """HARD-BLOCKED on prod IPs by NiosClient.
    HARD-BLOCKED if hard-enum violations would crash the grid.
    HARD-BLOCKED if planned row count exceeds the target appliance's
    discovered-device limit (see device_limits.py — pass target_model
    like 'ND-1606' / 'IB-V815' for an accurate ceiling).
    Returns {} on success or error dict."""
    import tempfile
    from nios_client import NiosClient, load_cfg, WriteBlockedError
    import device_limits
    # Refuse upload if any port_* enum value is invalid — that path crashes vNIOS.
    pre = validate_discovery_csv(text)
    if pre.get("hard_enum_violations"):
        return {"error": "refusing import: invalid enum value(s) detected; "
                          "uploading would crash the grid",
                 "violations": pre["hard_enum_violations"]}
    # Refuse upload if row count exceeds the model's discovery DB limit.
    cap = device_limits.check_capacity(target_model, pre["row_count"])
    if not cap["ok"]:
        return {"error": ("refusing import: row count exceeds the discovered-"
                           "device limit of the target appliance"),
                 "capacity": cap}
    cfg = load_cfg(f"{HERE}/gm.ini")
    # Check readonly guard BEFORE constructing the client with allow_writes=True
    # (allow_writes=True would otherwise wipe the cfg["gm"] in NIOS_READONLY_GMS check).
    from nios_client import _readonly_gms
    if cfg["gm"] in _readonly_gms():
        return {"error": f"refusing import: {cfg['gm']} is listed in NIOS_READONLY_GMS"}
    cli = NiosClient(cfg, allow_writes=True)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        fh.write(text); path = fh.name
    try:
        result = cli.upload_discovery_csv(path, network_view=network_view, merge=True)
        return {"upload_result": result, "network_view": network_view}
    except WriteBlockedError as e:
        return {"error": str(e)}
    finally:
        os.unlink(path)


# --- MCP stdio server -----------------------------------------------------

def main():
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("ERROR: mcp package not installed. Run: pip install mcp",
              file=sys.stderr)
        return 2

    mcp = FastMCP("nios_discovery_csv")

    @mcp.tool()
    def describe_field_tool(name: str) -> dict:
        """Describe a NIOS discovery field by name (CSV or WAPI internal)."""
        return describe_field(name)

    @mcp.tool()
    def list_accepted_csv_names_tool() -> list:
        """List CSV header names empirically confirmed to import."""
        return list_accepted_csv_names()

    @mcp.tool()
    def list_themes_tool() -> list:
        """List easter-egg themes for the generator."""
        return list_themes()

    @mcp.tool()
    def list_device_classes_tool() -> list:
        """List golden template device classes."""
        return list_device_classes()

    @mcp.tool()
    def generate_discovery_csv_tool(device_class: str, theme: str = None,
                                     base_ip: str = "198.51.100.10",
                                     count: int = 1) -> str:
        """Generate a populated discovery CSV for a device class. Returns CSV text."""
        return generate_discovery_csv(device_class, theme=theme,
                                       base_ip=base_ip, count=count)

    @mcp.tool()
    def validate_discovery_csv_tool(text: str) -> dict:
        """Classify each CSV header against the empirical schema."""
        return validate_discovery_csv(text)

    @mcp.tool()
    def recon_grid_tool() -> dict:
        """Read-only sanity poll of the grid in gm.ini."""
        return recon_grid()

    @mcp.tool()
    def import_discovery_csv_tool(text: str, network_view: str = "default") -> dict:
        """Upload a discovery CSV. HARD-BLOCKED if gm.ini points at a prod grid."""
        return import_discovery_csv(text, network_view=network_view)

    @mcp.tool()
    def empty_recycle_bin_tool() -> dict:
        """Permanently delete everything in the grid Recycle Bin.

        Run this AFTER any bulk teardown (zone/network/host-record DELETEs).
        NIOS soft-deletes — deleted objects continue to count against the
        appliance's DB capacity until the bin is emptied, so the capacity
        meter can read >100% even after a successful teardown.

        Returns the WAPI response (typically `{}`, async accept). The
        background GC worker processes the bin asynchronously; capacity
        drops within a few minutes.

        HARD-BLOCKED if the target GM is listed in NIOS_READONLY_GMS.
        """
        from nios_client import NiosClient, load_cfg, _readonly_gms
        cfg = load_cfg(f"{HERE}/gm.ini")
        if cfg["gm"] in _readonly_gms():
            return {"error": f"refusing empty_recycle_bin: {cfg['gm']} is listed in NIOS_READONLY_GMS"}
        cli = NiosClient(cfg, allow_writes=True)
        return cli.empty_recycle_bin()

    @mcp.tool()
    def simulate_network_tool(network: str, fill_pct: float = 50.0,
                               mode: str = "realistic",
                               seed: int = None,
                               discoverer: str = "simulator") -> str:
        """Generate a simulated IPAM discovery CSV for a CIDR.
        mode='realistic' uses real vendor/model/OS combos;
        mode='nerd' uses Matrix/LOTR/Star Wars/Sneakers/Elysium pop-culture refs.
        Returns CSV text. Validated against the canonical schema; safe to
        pipe into import_discovery_csv_tool."""
        import simulator, csv, io
        rows = simulator.simulate_network(network, fill_pct=fill_pct,
                                           mode=mode, seed=seed,
                                           discoverer=discoverer)
        canonical_order = [
            "ip_address", "first_discovered_timestamp", "last_discovered_timestamp",
            "discoverer", "discovered_name", "mac_address", "oui", "netbios_name",
            "os", "device_vendor", "device_model", "device_type",
            "device_location", "device_contact",
            "open_ports", "port_status", "port_duplex", "port_speed",
            "port_link_status", "port_vlan_name", "port_vlan_number",
            "network_component_name", "network_component_model",
            "network_component_vendor", "network_component_contact",
            "network_component_location", "network_component_port_name",
            "network_component_port_number", "network_component_type",
            "network_component_ip",
            "ap_name", "ap_ssid", "ap_ip_address",
            "tenant", "bridge_domain", "endpoint_groups",
            "vrf_name", "vrf_description", "vrf_rd", "bgp_as",
        ]
        seen = set()
        for r in rows: seen.update(r.keys())
        cols = [c for c in canonical_order if c in seen]
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows: w.writerow(r)
        return buf.getvalue()

    mcp.run()


if __name__ == "__main__":
    sys.exit(main() or 0)
