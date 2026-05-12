"""NIOS discovery-record capacity for CSV-imported metadata.

IMPORTANT: this is NOT the Network Insight (ND-*) device license cap. Those
two limits are different things:

  ND-* SKU device limit
    The maximum number of NETWORK-INSIGHT-MANAGED devices the appliance can
    actively poll via SNMP / CDP / LLDP. These get the full
    discovery:device topology graph (interfaces, neighbors, components,
    port stats). Per-model device counts are NOT published in the public
    Trinzic X6 datasheet — confirm against your account team and the
    release notes for the NIOS version you're running before sizing.

  CSV-import discovery-record capacity
    The maximum number of `ipv4address.discovered_data` rows the grid's
    discovery database can hold from EXTERNAL CSV imports (NetMRI IPAM
    Sync, this simulator, etc.). These are per-IP metadata rows — not
    NI-managed devices. They do not consume ND license counts.

We hit "Grid master database limit reached and restart discovery." at
~37,000 records on a TE-V926-class vNIOS GM running NIOS 9.1.0 with an
ND-906 license on 2026-05-11. That's the only data point we have for
CSV-path discovery-DB capacity; other appliance models, NIOS releases,
and license tiers will be different and have no published numbers to
extrapolate from.

We treat the observed vNIOS ceiling as the conservative default for any
target whose model we can't identify. For physical X5/X6 appliances or
different NIOS versions, verify empirically before any large bulk import.

See `docs/LESSONS_LEARNED.md` for the failure-mode trace.
"""

# Conservative CSV-path ceiling observed on a 9.1.0 vNIOS GM in the homelab
# (ND-906 license). Use this when we can't identify the appliance.
OBSERVED_VNIOS_CSV_CEILING = 37000

# Per-appliance overrides for the CSV-path discovery DB capacity. Currently
# empty — we don't have authoritative numbers for physical X5/X6 hardware.
# Fill in here as evidence accumulates from real deployments.
APPLIANCE_CSV_CAPACITY = {
    # "TE-1606-HW-AC": 100000,   # placeholder — verify before relying on
    # "TE-2306-HW-AC": 250000,   # placeholder — verify before relying on
}


def get_csv_capacity(model: str | None) -> dict:
    """Look up CSV-path discovery-record capacity for a model. Returns a
    dict with max_records, source, doc_ref. Falls back to the observed
    vNIOS ceiling when the model is unknown."""
    if model and model in APPLIANCE_CSV_CAPACITY:
        return {"max_records": APPLIANCE_CSV_CAPACITY[model],
                "model": model, "source": "documented"}
    return {"max_records": OBSERVED_VNIOS_CSV_CEILING,
            "model": model or "unknown",
            "source": "observed_vnios_ceiling",
            "doc_ref": "vNIOS 9.1.0 GM with ND-906 license hit 'Grid master "
                       "database limit reached and restart discovery.' at "
                       "~37,000 records during a /16 setdiscoverycsv import "
                       "on 2026-05-11."}


def check_capacity(model: str | None, planned_rows: int) -> dict:
    """Pre-flight check for setdiscoverycsv. Returns {'ok', 'limit',
    'planned', 'headroom', 'warning'}. Use before firing the upload."""
    info = get_csv_capacity(model)
    limit = info["max_records"]
    headroom = limit - planned_rows
    ok = planned_rows <= limit
    warn = None
    if not ok:
        warn = (f"{planned_rows} rows EXCEEDS the {limit}-record "
                f"discovery-DB ceiling for {info['model']} "
                f"({info['source']}). Discovery worker will error "
                f"mid-import: 'Grid master database limit reached and "
                f"restart discovery.'")
    elif planned_rows > limit * 0.8:
        warn = (f"{planned_rows} rows is >80% of the {limit}-record "
                f"discovery-DB ceiling for {info['model']}. Consider "
                f"splitting into multiple smaller imports.")
    return {"ok": ok, "limit": limit, "planned": planned_rows,
            "headroom": headroom, "model": info["model"],
            "source": info["source"], "warning": warn}


# Reference — Network Insight ND-* license SKUs (for the NI-managed
# device topology graph). These do NOT apply to CSV-imported
# discovery_data; they cap how many devices Network Insight actively
# polls. Per-model device counts are NOT published in the public Trinzic
# X6 datasheet or current docs.infoblox.com Trinzic intro pages — confirm
# against your account team and the release notes for the NIOS version
# you're running before sizing.
ND_LICENSE_SKUS_REFERENCE = {
    # X5 family
    "ND-805":   {"x_series": "X5", "ni_managed_devices": "unpublished"},
    "ND-1405":  {"x_series": "X5", "ni_managed_devices": "unpublished"},
    "ND-2205":  {"x_series": "X5", "ni_managed_devices": "unpublished"},
    "ND-4005":  {"x_series": "X5", "ni_managed_devices": "unpublished"},
    # X6 family
    "ND-906":   {"x_series": "X6", "ni_managed_devices": "unpublished"},
    "ND-1606":  {"x_series": "X6", "ni_managed_devices": "unpublished"},
    "ND-2306":  {"x_series": "X6", "ni_managed_devices": "unpublished"},
    "ND-4106":  {"x_series": "X6", "ni_managed_devices": "unpublished"},
}


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) > 2:
        print(json.dumps(check_capacity(sys.argv[1], int(sys.argv[2])), indent=2))
    else:
        print("--- CSV-path capacity overrides ---")
        print(json.dumps(APPLIANCE_CSV_CAPACITY or {"_default":
              f"observed vNIOS ceiling = {OBSERVED_VNIOS_CSV_CEILING}"}, indent=2))
        print()
        print("--- ND-* license SKUs (REFERENCE ONLY — not CSV-path; "
              "device counts unpublished) ---")
        print(json.dumps(ND_LICENSE_SKUS_REFERENCE, indent=2))
