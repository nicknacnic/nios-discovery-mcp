# Lessons Learned — NIOS Discovery CSV

Empirical notes from extending Chris Marrison's tool across five vNIOS test
grids on 2026-05-11/12. Five grids bricked (two prod-class, three test), the
canonical schema was reverse-engineered against a live grid, and the
NetMRI Administrator Guide turned out to be the authoritative source the
WAPI docs don't quite supplant. Each section here is a thing we had to
learn the hard way that the next person shouldn't.

## 1. The CSV header set isn't what the WAPI doc implies

The `discoverydata` WAPI struct lists ~96 fields. About 30 of them are
writable via `setdiscoverycsv`. The rest are read-side names for data
populated by native NIOS integrations (Network Insight, Cisco ISE,
hypervisor discovery, HSRP/VRRP). The CSV importer silently drops them.

The authoritative list of writable CSV fields is the
[NetMRI 7.5.1 IPAM Sync schema](https://docs.infoblox.com/space/NetMRI751/42469405/Data+Collection+Techniques)
— that's the same wire format `setdiscoverycsv` accepts. See
`docs/CSV_FORMAT.md` for the full breakdown.

**Fail mode**: you send `attached_device_name=x` (a long alias documented
in Chris's `discovery_fields.json`). The importer accepts the row, returns
`{}` (success), and on readback the field is empty. No error, no warning,
no log entry — silent drop. Use `network_component_name` instead.

## 2. The `port_*` enums crash the appliance

```text
port_status       Up | Down | Unknown
port_duplex       Full | Half           (NOT Full-duplex / Half-duplex / Auto)
port_speed        10M | 100M | 1G | 10G | 100G | Unknown
port_link_status  Connected | Not Connected | Unknown
```

Sending an invalid value (e.g. `port_status=PROBE_port_status`) crashes
the discovery worker. The appliance becomes HTTPS-unreachable for ~5
minutes; in two cases on test grids the crash was unrecoverable and the
appliance had to be redeployed.

**Fail mode**: a chunk of 10 single-row probes — 9 with valid values, 1
with `port_status=PROBE_port_status` — bricked the test grid mid-upload.
The discovery service entered a reboot loop on the offending row.

**Mitigation**: pre-flight validate every row against the enum table
above. `validate_discovery_csv()` in this PR refuses to ship a CSV with
any off-list enum value for these four fields.

## 3. Volume isn't the brick trigger, bad data is

Early in the probe campaign I suspected sustained CSV upload volume was
killing test grids. It wasn't. The pattern that actually kills grids is:

- Any single row with an invalid `port_*` enum value (see §2)
- 4 KB+ string values in some fields (suspected but not isolated)

When grids "died after lots of uploads", what actually happened was that
ONE row's bad data killed the discovery worker, the worker re-entered the
death state on every reboot, and the appliance became unrecoverable.

A clean /16 with 45,872 rows imported fine on a vNIOS test grid in one
shot. Volume was never the issue once the data was clean.

## 4. CSV-import discovery DB has a row ceiling separate from ND-* license

Two limits apply to "discovered devices" on a NIOS grid:

- **ND-* license cap** — gates Network-Insight-managed devices (the ones
  with active SNMP polling + `discovery:device` topology graph).
- **CSV-path discovery-DB capacity** — different bucket; gates per-IP
  `discoverydata` records from external CSV imports. This ceiling is NOT
  documented in the public Trinzic X6 datasheet or current
  docs.infoblox.com Trinzic intro pages.

External CSV uploads (NetMRI IPAM Sync, this tool, etc.) consume the
second budget and do NOT consume the ND license. Don't confuse the two.

The only data point we have for the CSV-path ceiling is empirical:

> On a TE-V926-class vNIOS 9.0.3 GM with an ND-906 license, the
> discovery worker errored with *"Grid master database limit reached and
> restart discovery."* at approximately 37,000 records during a /16
> setdiscoverycsv import on 2026-05-11.

That number is the *observed* ceiling for that specific
appliance/version combination. Other appliance models, NIOS releases,
and license tiers will be different — and we have no published numbers
to extrapolate from. Verify against your target grid before any large
bulk import.

### 4a. Per-`discoverytask` row ceiling (separate from the DB-row total)

In addition to the cumulative DB-row ceiling above, each individual
`setdiscoverycsv` invocation has its own per-task ceiling beyond which
the resulting `discoverytask` errors with the same
*"Grid master database limit reached"* message — even when the cumulative
DB is far below the total cap.

On the same TE-V926-class GM, that per-task ceiling sat around **50
rows**. Pushes of 100 rows in a single CSV errored after the first
50 had landed; pushes of 40 rows completed cleanly every time.

**Practical chunking targets:**

| CSV size       | Chunks @ 40 rows | Wall-clock (≈30s/chunk) | Notes                         |
|----------------|-----------------:|------------------------:|-------------------------------|
| 100            |                3 |                ~1.5 min | smoke test                    |
| 1,000          |               25 |                  13 min | small demo (one /24)          |
| 2,000          |               50 |                  25 min | medium demo (one site)        |
| 10,000         |              250 |             ~2 h 5 min  | full /20 worth                |
| 26,000 (/16 @ 40% fill) |     650 |             ~5 h 30 min | large demo                    |
| 45,000 (/16 @ 70% fill) |   1,125 |             ~9 h 30 min | a saturated /16               |

Each chunk runs as its own `discoverytask`, so progress is observable
and resumable. If a chunk errors, the next one usually succeeds — the
worker self-resets between tasks. Plan accordingly: a /16 demo is an
overnight job, not a coffee-break job.

If you find a NIOS configuration knob that raises the per-task ceiling
(beyond `unmanaged_ips_limit`, which appears to apply elsewhere),
please open a PR documenting it.

### 4b. The mode=FULL stuck-task gotcha

If the discoverytask gets into an `ERROR` state on a previous run, the
next `setdiscoverycsv` invocation can come back with `mode=FULL` instead
of `mode=CSV`, error at 0 rows, and report misleading status counts.
The fix is to restart the Network Insight member (the discovery worker
runs there, not on the GM). User shell on the NI member only exposes:

```text
restart            (dhcp | dns | tftp | http_fd | ftp | ntp | captive_portal)
restart_product    (full appliance reboot — ~5 min downtime)
```

`restart_product` is the bigger hammer but reliably clears the bad state.
Fresh tasks land cleanly afterward.

## 5. `setdiscoverycsv` doesn't create `discovery:device` records

The CSV path populates `ipv4address.discovered_data` only. It does NOT
create `discovery:device`, `discovery:deviceinterface`,
`discovery:deviceneighbor`, or `discovery:devicecomponent` records.
The `discovery:device` WAPI object has
`restrictions: ['create', 'delete', 'scheduling', 'csv']` — explicitly
unwritable via any external path.

The "Asset / Components / Interfaces / Neighbors" tabs in IPAM populate
only when Network Insight has actively polled the device with valid
credentials. NetMRI IPAM Sync uses the same `setdiscoverycsv` path; the
rich device data stays in NetMRI's own database.

If your goal is "make this IP look like a discovered device in IPAM",
the CSV path is the right tool. If your goal is "make this device appear
under Network Insight → Devices with full topology", the CSV path is the
wrong tool — that data has no documented external write path.

## 6. `csv_import` operations — INSERT vs UPDATE vs REPLACE

The standard NIOS CSV import (`fileop?_function=csv_import` — used for DNS
records, networks, fixed addresses, etc., NOT setdiscoverycsv) accepts:

```text
INSERT   — error if the object already exists
UPDATE   — error if the object doesn't exist
REPLACE  — INSERT if missing, UPDATE if present
DELETE   — remove
CUSTOM   — caller specifies per-row
```

`OVERRIDE` is NOT a valid operation (we tried; NIOS returns
`Invalid value for operation`). Despite that, on first import into an
empty zone, REPLACE fails with:

```text
Parent zone of '<fqdn>' does not contain any records.
Perform INSERT operation instead of REPLACE.
```

So on first writes use INSERT. On subsequent re-syncs use REPLACE if you
want idempotent imports.

## 7. DNS service restart is required between zone creation and record insertion

After creating an authoritative zone via `csv_import`, NIOS will not
write records into it until the DNS service has restarted. Trigger via:

```http
POST /wapi/v2.12/grid/<ref>?_function=restartservices
{"service_option": "DNS", "restart_option": "RESTART_IF_NEEDED"}
```

The restart takes ~30-60 sec on vNIOS. Wait for HTTP 200 on a subsequent
`networkview` poll before pushing records.

We learned this the hard way — sent zones + records in immediate
succession and the records all errored with no obvious cause. The grid's
correction message ("Perform INSERT operation instead of REPLACE")
masked the underlying restart-not-yet-applied issue.

## 8. Timestamps round-trip with type conversion

CSV import accepts timestamp strings in `YYYY-MM-DD HH:MM:SS` format
(GMT, no timezone suffix). The WAPI returns them as `uint` epoch seconds
on read-back. So:

```text
last_discovered_timestamp = "2026-05-11 17:37:34"
```

reads back as

```text
last_discovered = 1747000654
```

Don't assume the format you sent is the format you'll get back.

## 9. `discoverytask` async behaviour

`setdiscoverycsv` returns `{}` immediately on accept. The actual import
is performed by the grid's `discoverytask` worker, which you can poll:

```http
GET /wapi/v2.12/discoverytask?_return_fields=state,csv_file_name,status
```

The current task has `_ref` containing `current`. Its `state` will be
`RUNNING` while processing, `COMPLETE` when done, or `ERROR` on failure.
The `status` field is a string with summary counts (Discovered, Managed,
Unmanaged, Conflicts) — the `lines_processed` numeric field on the
related csvimporttask object does NOT update for setdiscoverycsv runs.

## 10. Operational summary — the safe path

To populate a clean demo:

1. **Read the canonical schema from the live grid** via
   `/ipv4address?_schema=1&_get_doc=1`. The grid is authoritative.
2. **Use only the writable CSV columns documented in `CSV_FORMAT.md`**.
   Anything else either silently drops or crashes the appliance.
3. **Pre-flight validate every row** for hard-enum violations BEFORE
   upload. The crash bug is real and easy to trip.
4. **Pre-flight capacity check** the row count against your target
   appliance's discovery-DB ceiling. Don't assume large physical X6
   grids accept arbitrary volume.
5. **Networks first, zones second, restart third, records and discovery
   data fourth.** The restart between steps 2 and 3 is non-obvious but
   required.
6. **INSERT for first write, REPLACE for re-sync.** Don't try OVERRIDE
   (not valid) or REPLACE on first write (fails on empty zone).
7. **Discovery data via `setdiscoverycsv`**; DNS records via the regular
   `csv_import` host-record path. Two different fileop endpoints.

## 11. What "before" and "after" look like in IPAM

The visual difference between an IP that has a host record but no
`discovered_data`, and one that has both, is the entire reason for using
the CSV path.

**Before** — an IP with a host record only (the `csv_import`
`header-hostrecord` path created the FQDN, but `setdiscoverycsv` has
not yet run for this IP):

![empty discovered data panel](img/before-empty-discovery.png)

The IP appears USED with a hostname, but the "Discovered Data" panel
underneath is empty. NetBIOS Name, MAC Address, OS, Device Vendor, port
information — every field a NetMRI sync or asset scanner would
contribute — is blank.

**After** — same panel after `setdiscoverycsv` lands for that IP:

![populated discovered data panel](img/after-populated-discovery.png)

NetBIOS Name, Discovered MAC, OS string, vendor, model, open_ports,
port speed/duplex/status, attached-device fields — populated. That's
the "Discovered Data" the WAPI doc and Chris's original post are
talking about, and it's what this tool produces.

The two screenshots above are from the same NIOS grid taken minutes
apart — host records were imported first via `csv_import`, then a
chunked `setdiscoverycsv` pass populated discovery data on a subset of
IPs (see §4a on the per-task ceiling and chunking cadence).

## 12. `DELETE` doesn't free DB capacity — empty the Recycle Bin

NIOS soft-deletes. When you `DELETE` a host record, zone, network, or
discoverydata row, the object moves to the **Recycle Bin** and continues
to count against the appliance's DB capacity limit. The grid's
"Database Capacity Used" indicator (visible in the GUI under Dashboards →
System) will not drop until the bin is emptied.

**Symptom**: you bulk-delete a demo dataset, expect the capacity meter
to fall, and instead find it reading 100%+ — sometimes well past, because
the meter doesn't cap. After deleting ~28K demo objects on a TE-V926
vNIOS the meter read **202%**.

**Fix**: POST `empty_recycle_bin` against the grid object:

```http
POST /wapi/v2.12/grid/<ref>?_function=empty_recycle_bin
```

Returns `{}` immediately (async). A background garbage-collector worker
processes the bin and the capacity meter drops within a few minutes.
There's also a per-zone variant on `zone_auth` if you need finer
granularity.

Worth wiring into any teardown script so you don't have to chase the
capacity alarm afterward. Sample Python:

```python
grid_ref = cli.get("grid")[0]["_ref"]
cli.post(grid_ref, _function="empty_recycle_bin")
```
