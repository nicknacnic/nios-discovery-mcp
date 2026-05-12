"""IPAM discovery data simulator.

Given a network (e.g. 198.18.0.0/24) and a fill percentage, generate a CSV of
schema-compliant `discovered_data` rows that import cleanly via
fileop/setdiscoverycsv.

Modes:
  realistic  - real vendor/model combos, plausible OS strings, real OUIs,
               sensible naming. Use to populate a demo IPAM tenant that looks
               like a real network.
  nerd       - same shape, but device names + contacts + locations are pop
               culture refs (Matrix, Sneakers, LOTR, Star Wars). Schema-valid.

Vendor mix and device-class weights are tuned to match what shows up in a
typical office network of mostly endpoints with a few infra anchors:

  60% endpoint (laptop / workstation)
  15% server / appliance
  10% printer / IoT
   5% wireless AP
   5% switch
   4% camera / phone
   1% router / firewall

Every row uses only the canonical CSV writable set (see
schema/discovery_schema.json `csv_import_evidence.confirmed_accepted_csv_names`
plus NetMRI's published IPAM Sync field list). Port enums are filled with
valid values to avoid the port_status grid-crash bug.
"""
from __future__ import annotations
import csv
import dataclasses
import ipaddress
import random
import time
from typing import Iterable

# ---------- Pools (realistic) --------------------------------------------

# (vendor, model, device_type, oui, os_pool, openports_pool, host_prefix)
@dataclasses.dataclass
class DeviceProfile:
    name: str           # internal key
    weight: int         # for class distribution
    vendor: str         # canonical vendor string
    models: tuple       # list of plausible models
    device_type: str    # NIOS-style: Switch, Router, Switch-Router, Wireless AP, ...
    oui_choices: tuple  # MAC OUI prefixes (no colons, lowercase)
    os_pool: tuple      # OS version strings
    open_ports_pool: tuple  # "TCP:... UDP:..." strings
    host_prefix: str    # used as default hostname seed when theme not used
    port_speed: str = "1G"  # default port_speed enum
    has_netbios: bool = False


PROFILES = (
    DeviceProfile(
        name="endpoint_windows", weight=35,
        vendor="Dell", models=("Latitude 7440", "OptiPlex 7080", "Latitude 5440",
                                "Precision 3590", "Latitude 9450"),
        device_type="Endpoint",
        oui_choices=("a45e60", "f4b520", "d4ae52", "001a36"),
        os_pool=("Windows 11 24H2", "Windows 11 23H2", "Windows 10 22H2",
                 "Windows 11 Enterprise 24H2"),
        open_ports_pool=("TCP:135,139,445,3389 UDP:137,138",
                         "TCP:135,445,5040 UDP:137",
                         "TCP:445 UDP:137,138"),
        host_prefix="ws", has_netbios=True,
    ),
    DeviceProfile(
        name="endpoint_mac", weight=20,
        vendor="Apple", models=("MacBook Pro 14", "MacBook Pro 16",
                                "MacBook Air 13", "iMac 24", "Mac mini M2"),
        device_type="Endpoint",
        oui_choices=("3c2200", "f0182b", "8866a5", "a45e60", "f4b520"),
        os_pool=("macOS Sonoma 14.5", "macOS Sequoia 15.1",
                 "macOS Ventura 13.7", "macOS Sequoia 15.2"),
        open_ports_pool=("TCP:22,88,445,548 UDP:5353",
                         "TCP:22 UDP:5353,5355",
                         "TCP:445,548 UDP:5353"),
        host_prefix="mac",
    ),
    DeviceProfile(
        name="server_linux", weight=15,
        vendor="Dell", models=("PowerEdge R740", "PowerEdge R750",
                                "PowerEdge R650", "PowerEdge R760"),
        device_type="Server",
        oui_choices=("a45e60", "f4b520", "d4ae52", "001a36"),
        os_pool=("Ubuntu 22.04.4 LTS", "Ubuntu 24.04 LTS",
                 "RHEL 9.4", "Rocky Linux 9.3", "Debian 12.5"),
        open_ports_pool=("TCP:22,80,443 UDP:",
                         "TCP:22,53,80,443 UDP:53",
                         "TCP:22,3306 UDP:",
                         "TCP:22,80,443,8443 UDP:"),
        host_prefix="srv", port_speed="10G",
    ),
    DeviceProfile(
        name="printer", weight=7,
        vendor="HP", models=("LaserJet Pro M428fdw", "LaserJet Enterprise M507",
                              "OfficeJet Pro 9015", "Color LaserJet M454"),
        device_type="Printer",
        oui_choices=("9457a5", "f43030", "9c5c8e", "ec9a74"),
        os_pool=("HP FutureSmart 5", "HP FutureSmart 4.12",
                 "HP LaserJet Firmware 2410A"),
        open_ports_pool=("TCP:80,443,515,631,9100 UDP:161,5353",
                         "TCP:80,9100 UDP:161",
                         "TCP:80,443,515,9100 UDP:161"),
        host_prefix="prn",
    ),
    DeviceProfile(
        name="iot_camera", weight=4,
        vendor="Axis", models=("M3215-LVE", "P3265-LVE", "M4318-PLVE",
                                "Q1656-LE"),
        device_type="IP Camera",
        oui_choices=("accc8e", "b8a44f", "00408c"),
        os_pool=("AXIS OS 11.10", "AXIS OS 10.12", "AXIS OS 11.8"),
        open_ports_pool=("TCP:80,443,554 UDP:5353",
                         "TCP:80,554 UDP:5353"),
        host_prefix="cam",
    ),
    DeviceProfile(
        name="voip_phone", weight=3,
        vendor="Cisco", models=("CP-8865", "CP-8845", "CP-8841", "CP-7841"),
        device_type="VoIP Phone",
        oui_choices=("00170e", "f44e05", "6c41f7", "001ac1"),
        os_pool=("Cisco IP Phone 14.2", "Cisco IP Phone 12.8"),
        open_ports_pool=("TCP:80,443,5060,5061 UDP:5060,69",),
        host_prefix="phone",
    ),
    DeviceProfile(
        name="wireless_ap", weight=5,
        vendor="Aruba", models=("AP-635", "AP-505", "AP-535", "AP-655"),
        device_type="Wireless AP",
        oui_choices=("00408c", "b4cef6", "94b40f", "20a6cd"),
        os_pool=("ArubaOS 8.11.2.1", "ArubaOS 10.4.1.1",
                 "ArubaOS 8.10.0.10"),
        open_ports_pool=("TCP:22,80,443,4343 UDP:161,514,8211",
                         "TCP:22,443 UDP:161,514"),
        host_prefix="ap", port_speed="1G",
    ),
    DeviceProfile(
        name="switch", weight=5,
        vendor="Cisco", models=("Catalyst 9300-48P", "Catalyst 2960-X-48",
                                "Catalyst 9200L-24T", "Nexus 9336C"),
        device_type="Switch",
        oui_choices=("00170e", "001ac1", "00aabb", "f44e05"),
        os_pool=("Cisco IOS XE 17.12.3", "Cisco IOS XE 17.9.5",
                 "Cisco IOS 15.2(7)E11"),
        open_ports_pool=("TCP:22,23,80,443 UDP:161,162",
                         "TCP:22,443 UDP:161,162"),
        host_prefix="sw", port_speed="10G",
    ),
    DeviceProfile(
        name="router", weight=1,
        vendor="Cisco", models=("ISR 4451-X", "ASR 1001-X", "C8500-12X"),
        device_type="Router",
        oui_choices=("00170e", "001ac1", "f44e05"),
        os_pool=("Cisco IOS XE 17.12.3", "Cisco IOS XE 17.9.5"),
        open_ports_pool=("TCP:22,179,443 UDP:161,500",),
        host_prefix="rtr", port_speed="10G",
    ),
    DeviceProfile(
        name="firewall", weight=1,
        vendor="Palo Alto Networks", models=("PA-3220", "PA-440", "PA-1410"),
        device_type="Firewall",
        oui_choices=("001b17", "b4007e"),
        os_pool=("PAN-OS 11.1.4-h7", "PAN-OS 10.2.10"),
        open_ports_pool=("TCP:22,443 UDP:500,4500",),
        host_prefix="fw", port_speed="10G",
    ),
)


# ---------- Theme (cosmetic — never changes shape, only string values) ----

REALISTIC_LOCATIONS = (
    "HQ - Floor 2 IDF", "HQ - Floor 3 IDF", "DC1 Rack A12", "DC1 Rack B07",
    "Branch-NY - Closet 1", "Branch-LON - Closet A", "Branch-SF - Conf Room",
    "Lobby", "Reception Area", "Server Room", "Warehouse Bay 4",
)

REALISTIC_CONTACTS = (
    "noc@example.com", "netops@example.com", "helpdesk@example.com",
    "infra-team@example.com", "facilities@example.com",
)

# nerd-mode themes — pop culture refs by family

NERD_NAMES_MATRIX = (
    "neo", "morpheus", "trinity", "cypher", "tank", "dozer", "switch",
    "apoc", "mouse", "oracle", "sentinel", "agent-smith", "nebuchadnezzar",
)
NERD_NAMES_SNEAKERS = (
    "bishop", "crease", "whistler", "mother", "carl", "werner",
    "cosmo", "setec",
)
NERD_NAMES_LOTR = (
    "frodo", "samwise", "gandalf", "aragorn", "legolas", "gimli", "boromir",
    "elrond", "galadriel", "sauron", "saruman", "merry", "pippin", "bilbo",
)
NERD_NAMES_STARWARS = (
    "vader", "yoda", "luke", "han", "leia", "chewbacca", "r2d2", "bb8",
    "obiwan", "ahsoka", "grogu", "boba", "wedge", "lando",
)
NERD_NAMES_ELYSIUM = (
    "max", "frey", "spider", "kruger", "delacourt", "carlyle", "matilda",
)
NERD_ALL = (NERD_NAMES_MATRIX + NERD_NAMES_SNEAKERS + NERD_NAMES_LOTR
            + NERD_NAMES_STARWARS + NERD_NAMES_ELYSIUM)

NERD_LOCATIONS = (
    "Zion - Deck 4", "Nebuchadnezzar - Bridge", "Construct Loading Program",
    "Setec Astronomy - basement", "Mordor - Mount Doom", "Rivendell Library",
    "Death Star - Trash Compactor", "Hoth - Echo Base", "Bag End",
    "Helms Deep", "Elysium Station", "Tortuga Beach",
)
NERD_CONTACTS = (
    "operator@nebuchadnezzar.zion", "cosmo@setec.org", "elrond@imladris.me",
    "obiwan@kenobi.tatooine", "matrix-noc@zion.net",
)
NERD_DOMAINS = (
    "zion.net", "nebuchadnezzar.matrix", "setec.org", "rivendell.me",
    "mordor.local", "death-star.imp", "elysium.station", "tatooine.outer",
)


# ---------- Generator -----------------------------------------------------

PORT_DUPLEXES = ("Full", "Half")
PORT_STATUSES = ("Up", "Down", "Unknown")
PORT_LINK_STATUSES = ("Connected", "Not Connected", "Unknown")
PORT_SPEEDS = ("10M", "100M", "1G", "10G", "100G", "Unknown")


def _mac(rng, oui_no_colons: str) -> str:
    return ":".join([oui_no_colons[i:i+2] for i in (0, 2, 4)]
                    + [f"{rng.randint(0,255):02x}" for _ in range(3)])


def _pick_profile(rng) -> DeviceProfile:
    total = sum(p.weight for p in PROFILES)
    target = rng.randrange(total)
    acc = 0
    for p in PROFILES:
        acc += p.weight
        if target < acc:
            return p
    return PROFILES[-1]


def _hostname(rng, profile: DeviceProfile, idx: int, mode: str) -> str:
    if mode == "nerd":
        name = rng.choice(NERD_ALL)
        suffix = "" if rng.random() < 0.5 else f"-{rng.randint(1, 99):02d}"
        domain = rng.choice(NERD_DOMAINS)
        return f"{name}{suffix}.{domain}"
    # realistic
    site = rng.choice(("hq", "ny", "sf", "lon", "tok", "dc1"))
    return f"{profile.host_prefix}-{site}-{idx:03d}.corp.example.com"


def _netbios(host: str) -> str:
    # NetBIOS name is the short host part, max 15 chars, uppercase
    short = host.split(".", 1)[0]
    return short[:15].upper().replace("-", "")[:15]


def _location(rng, mode: str) -> str:
    pool = NERD_LOCATIONS if mode == "nerd" else REALISTIC_LOCATIONS
    return rng.choice(pool)


def _contact(rng, mode: str) -> str:
    pool = NERD_CONTACTS if mode == "nerd" else REALISTIC_CONTACTS
    return rng.choice(pool)


# Upstream switch pool — used to populate network_component_* on every endpoint
# so the IPAM panel shows "found behind switch X port Y". Each simulated
# network gets a fixed small pool of "upstream switches" assigned at start.
def _build_upstreams(rng, mode: str, count: int = 4,
                      luminary_sites=None) -> list[dict]:
    sw_profile = next(p for p in PROFILES if p.name == "switch")
    out = []
    for i in range(count):
        if mode == "nerd":
            name = rng.choice(NERD_ALL) + ".core.zion.net"
            contact = rng.choice(NERD_CONTACTS)
            location = rng.choice(NERD_LOCATIONS)
        elif False and luminary_sites:
            import luminary
            site = rng.choice(luminary_sites)
            role = rng.choice(("core", "agg", "edge"))
            name = f"lsys-{site.code}-sw-{role}-{i+1:02d}"
            contact = luminary.luminary_contact(rng)
            location = luminary.location_for_site(rng, site)
        else:
            name = f"sw-core-{i+1:02d}.corp.example.com"
            contact = rng.choice(REALISTIC_CONTACTS)
            location = rng.choice(REALISTIC_LOCATIONS)
        out.append({"name": name, "model": rng.choice(sw_profile.models),
                     "vendor": sw_profile.vendor,
                     "contact": contact, "location": location})
    return out


def simulate_network(network_cidr: str, *, fill_pct: float = 50.0,
                     mode: str = "realistic", seed: int | None = None,
                     discoverer: str = "simulator",
                     reserved_first: int = 1, reserved_last: int = 1) -> list[dict]:
    """Return a list of discovered_data row dicts ready to feed to a CSV writer.

    fill_pct  — percentage of usable IPs to populate (0..100)
    mode      — "realistic", "nerd", or "luminary" (Project Icon Luminary Systems)
    seed      — int for deterministic generation
    reserved_first/last — skip first N and last N IPs in the network
                          (gateways, broadcast)
    """
    if mode not in ("realistic", "nerd"):
        raise ValueError(f"mode must be 'realistic'|'nerd'|'luminary', got {mode!r}")
    rng = random.Random(seed)
    net = ipaddress.ip_network(network_cidr, strict=False)
    candidates = list(net.hosts())[reserved_first:-reserved_last or None]
    fill_count = max(1, int(len(candidates) * fill_pct / 100))
    picked = rng.sample(candidates, fill_count)
    picked.sort()

    # Luminary mode overrides: for each IP, look up its site/tier in the
    # canonical IP scheme, pick an asset class that matches the tier, and
    # use Luminary naming patterns. IPs outside the Luminary scheme fall back
    # to realistic generation.
    luminary_overlay = None
    if False:
        import luminary
        luminary_overlay = luminary
        # If the requested host CIDR is outside the canonical 10.x.x.x scheme,
        # install a synthetic site overlay so we can place Luminary-flavored
        # names on any IP block (e.g. 198.18.0.0/16 for IETF benchmark demos).
        host_net = ipaddress.ip_network(network_cidr, strict=False)
        canonical = ipaddress.ip_network("10.0.0.0/8")
        if not host_net.subnet_of(canonical):
            sites = luminary.install_overlay(network_cidr)
            print(f"[luminary] installed {len(sites)} overlay sites onto"
                  f" {network_cidr} (canonical scheme is 10/8)")

    # Build the upstream-switch pool (referenced by every host's network_component_*).
    # In luminary mode, scope upstreams to sites that overlap the requested CIDR
    # so the topology stays consistent.
    luminary_relevant_sites = None
    if luminary_overlay:
        net_for_pool = ipaddress.ip_network(network_cidr, strict=False)
        # Prefer overlay sites if installed; else fall back to canonical
        # sites that overlap the requested CIDR.
        if luminary_overlay._overlay_sites:
            luminary_relevant_sites = luminary_overlay._overlay_sites
        else:
            luminary_relevant_sites = [
                s for s in luminary_overlay.ALL_SITES
                if ipaddress.ip_network(s.cidr).overlaps(net_for_pool)
            ] or list(luminary_overlay.OFFICE_SITES[:3])
    upstreams = _build_upstreams(rng, mode, count=max(2, fill_count // 20),
                                  luminary_sites=luminary_relevant_sites)
    now = int(time.time())
    rows = []
    for idx, ip in enumerate(picked, 1):
        lum_profile = (luminary_overlay.luminary_profile_for_ip(str(ip), rng)
                        if luminary_overlay else None)
        # In strict luminary mode, skip IPs that don't map to a known site
        # rather than falling through to generic realistic names.
        if luminary_overlay and not lum_profile:
            continue
        if lum_profile:
            # Pick the matching DeviceProfile based on Luminary asset class
            cls_to_profile = {
                "endpoint": "endpoint_windows",
                "server": "server_linux",
                "lb": "server_linux",
                "storage": "server_linux",
                "switch": "switch", "router": "router", "firewall": "firewall",
                "ap": "wireless_ap", "printer": "printer",
                "iot_camera": "iot_camera", "voip_phone": "voip_phone",
            }
            pname = cls_to_profile.get(lum_profile["asset_class"], "endpoint_windows")
            profile = next((p for p in PROFILES if p.name == pname), PROFILES[0])
            host = lum_profile["hostname"]
        else:
            profile = _pick_profile(rng)
            host = _hostname(rng, profile, idx, mode if True else "realistic")
        mac = _mac(rng, rng.choice(profile.oui_choices))
        oui = mac[:8]
        model = rng.choice(profile.models)
        os_v = rng.choice(profile.os_pool)
        op_ports = rng.choice(profile.open_ports_pool)
        # Time spread: first_discovered in the past 60 days, last in past 24h
        first_t = now - rng.randint(7 * 86400, 60 * 86400)
        last_t = now - rng.randint(60, 86400)
        first_s = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(first_t))
        last_s = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(last_t))

        upstream = rng.choice(upstreams)
        port_num = rng.randint(1, 48)
        port_name = f"Gi1/0/{port_num}" if profile.device_type != "Wireless AP" else f"Gi2/0/{port_num}"

        row = {
            "ip_address": str(ip),
            "first_discovered_timestamp": first_s,
            "last_discovered_timestamp": last_s,
            "discoverer": discoverer,
            "discovered_name": host,
            "mac_address": mac,
            "oui": oui,
            "os": os_v,
            "device_vendor": profile.vendor,
            "device_model": model,
            "device_type": profile.device_type,
            "device_location": (luminary_overlay.location_for_site(rng, lum_profile["site"])
                                  if lum_profile else _location(rng, mode)),
            "device_contact": (luminary_overlay.luminary_contact(rng)
                                 if lum_profile else _contact(rng, mode)),
            "open_ports": op_ports,
            "port_status": rng.choices(PORT_STATUSES, weights=(8, 1, 1))[0],
            "port_duplex": rng.choice(PORT_DUPLEXES),
            "port_speed": profile.port_speed,
            "port_link_status": rng.choices(PORT_LINK_STATUSES,
                                            weights=(8, 1, 1))[0],
            "port_vlan_name": rng.choice(("data", "voice", "iot", "guest",
                                          "mgmt", "servers")),
            "port_vlan_number": str(rng.choice((10, 20, 30, 40, 50, 100, 200))),
            # Attached device (the upstream switch we pretended found this IP)
            "network_component_name": upstream["name"],
            "network_component_model": upstream["model"],
            "network_component_vendor": upstream["vendor"],
            "network_component_contact": upstream["contact"],
            "network_component_location": upstream["location"],
            "network_component_port_name": port_name,
            "network_component_port_number": str(port_num),
            "network_component_type": "Switch",
            "network_component_ip": str(ip.compressed) if False else str(net.network_address + (idx % 4 + 1)),
        }
        if profile.has_netbios:
            row["netbios_name"] = _netbios(host)
        # Wireless-AP add-ons
        if profile.device_type == "Wireless AP":
            row["ap_name"] = host.split(".", 1)[0].upper()
            row["ap_ssid"] = rng.choice(("corp-wifi", "guest-wifi",
                                          "iot-wifi", "voice-wifi"))
            row["ap_ip_address"] = str(ip)
        # Network-segmentation extras (Cisco ACI flavor) — sparingly
        if rng.random() < 0.2:
            row["tenant"] = rng.choice(("corp", "guest", "iot", "dmz"))
            row["bridge_domain"] = f"BD-{row['port_vlan_name']}"
            row["endpoint_groups"] = f"EPG-{row['port_vlan_name']}"
        # Routing extras for switches/routers/firewalls
        if profile.device_type in ("Switch", "Router", "Firewall"):
            row["vrf_name"] = rng.choice(("default", "mgmt", "guest"))
            row["vrf_description"] = f"VRF for {row['vrf_name']}"
            row["vrf_rd"] = f"65001:{rng.randint(1,99)}"
            row["bgp_as"] = "65001"

        rows.append(row)
    return rows


def write_csv(rows: list[dict], path: str) -> None:
    """Write rows to CSV with a column header that's the union of all keys
    (in a canonical order so output is stable)."""
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
    for r in rows:
        seen.update(r.keys())
    cols = [c for c in canonical_order if c in seen]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("network", help="CIDR, e.g. 198.18.0.0/24")
    ap.add_argument("--fill", type=float, default=50.0, help="percent of IPs to fill")
    ap.add_argument("--mode", choices=("realistic", "nerd"), default="realistic")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--discoverer", default="simulator")
    ap.add_argument("-o", "--output", default="-",
                    help="output path (- for stdout)")
    args = ap.parse_args()

    rows = simulate_network(args.network, fill_pct=args.fill, mode=args.mode,
                             seed=args.seed, discoverer=args.discoverer)
    if args.output == "-":
        import sys
        cols = sorted({k for r in rows for k in r.keys()})
        import csv as _csv
        w = _csv.DictWriter(sys.stdout, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    else:
        write_csv(rows, args.output)
        print(f"wrote {len(rows)} rows to {args.output}", file=__import__("sys").stderr)
