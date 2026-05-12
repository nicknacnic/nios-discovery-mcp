# NIOS Discovery CSV — Format Guide

Empirically established across five vNIOS test grids on 2026-05-11. Where
this document disagrees with the WAPI `discoverydata` struct, prefer this
document — the WAPI struct includes read-only fields that the CSV path
silently drops.

## 1. The wire format

`fileop?_function=setdiscoverycsv` accepts a UTF-8 CSV with:
- A single header row.
- One row per IP address being populated.
- `ip_address` and `last_discovered_timestamp` are the only required columns.
  Everything else is optional.
- Timestamps in CSV are `YYYY-MM-DD HH:MM:SS` (GMT, no timezone suffix).
  WAPI returns them as `uint` epoch seconds on read-back.
- Strings with commas/quotes must be RFC 4180 quoted.

The import is async. `setdiscoverycsv` returns `{}` immediately and
spawns a `discoverytask` to do the actual work. Poll `discoverytask?_ref=...current`
to track progress.

## 2. The canonical writable column set (NetMRI IPAM Sync)

```text
Core columns (you almost always want all five):
  ip_address                   IPv4 string. REQUIRED.
  last_discovered_timestamp    "YYYY-MM-DD HH:MM:SS" GMT. REQUIRED.
  first_discovered_timestamp   "YYYY-MM-DD HH:MM:SS" GMT.
  discovered_name              FQDN of the discovered host.
  discoverer                   Source label ("NetMRI", "scanner-foo", etc.).

General device:
  mac_address                  Lowercase, colon-separated.
  netbios_name                 Max 15 chars.
  os                           Max 256 chars.
  device_model
  device_vendor
  device_type                  Free string in practice (Switch, Router,
                               Switch-Router, Endpoint, Wireless AP, ...).
  device_location
  device_contact
  device_management_ip         Chris's long alias; the importer accepts it
                               and validates as IPv4. (The WAPI internal
                               name mgmt_ip_address SILENTLY DROPS.)
  oui                          MAC OUI prefix.
  open_ports                   "TCP:p1,p2,p3 UDP:p1,p2" — max 1000 ports total.
                               Trailing colon = empty proto.
  device_port_name             Interface name on the discovered device
                               (e.g. "primarylan1", "eth0").
  device_port_type             Interface type (SNMP IF-MIB ifType label,
                               e.g. "ethernet-csmacd").

Attached device / CDP-LLDP neighbor (the upstream switch+port the host
was found behind). These use the WAPI internal `network_component_*`
names — NOT Chris's long `attached_device_*` aliases (those silently drop).
  network_component_type           E.g. "Switch", "Router". Max 32 ch.
  network_component_name           Max 64.
  network_component_description    Max 256.
  network_component_ip             IPv4 string.
  network_component_model
  network_component_vendor
  network_component_location
  network_component_contact
  network_component_port_number    uint 0-9999.
  network_component_port_name      Max 64.
  network_component_port_description  Max 256.

Port data (host's local interface). FOUR ENUM FIELDS — see §3.
  port_vlan_name        Max 64.
  port_vlan_number      uint 0-9999.
  port_speed            ENUM (see §3) — CRASH RISK if invalid.
  port_duplex           ENUM (see §3) — CRASH RISK if invalid.
  port_status           ENUM (see §3) — CRASH RISK if invalid.
  port_link_status      ENUM (see §3) — CRASH RISK if invalid.

Cisco ACI (SDN context):
  tenant
  bridge_domain
  endpoint_groups       (Read-side name is `endpoint_groups`; Chris's CSV
                         alias `epg` silently drops — use this one.)

VRF / BGP (L3 context):
  vrf_name
  vrf_description
  vrf_rd
  bgp_as                uint.

Wireless AP:
  ap_name
  ap_ip_address
  ap_ssid
```

## 3. ⚠️ Enum fields that crash the appliance on invalid input

The discovery worker has a defect that **causes the vNIOS appliance to
become unreachable** (HTTPS port 443 refused, ~5 min recovery, sometimes
a reboot loop) when it receives an off-list enum value for any of these
four fields:

```text
port_status       Up | Down | Unknown
port_duplex       Full | Half           (NOT Full-duplex / Half-duplex / Auto)
port_speed        10M | 100M | 1G | 10G | 100G | Unknown
port_link_status  Connected | Not Connected | Unknown
```

**Always validate enum values before upload.** The pre-flight check is
trivial — see `validate_discovery_csv()` in this PR. Pre-flight refusal is
the only thing standing between you and an unscheduled grid reboot.

## 4. Silently dropped — fields the importer ignores entirely

Despite appearing in the WAPI `discoverydata` struct, the following are
populated only by native NIOS integrations (Network Insight, Cisco ISE,
hypervisor discovery, HSRP/VRRP scraping). The CSV import IGNORES them:

- All virtualization fields: `v_host`, `v_cluster`, `v_datacenter`,
  `v_entity_name`, `v_entity_type`, `v_switch`, `v_adapter`,
  `vmhost_*`, `vmi_*`, `vport_*`, `vswitch_*`, `vlan_port_group`,
  `attached_virtual_*`
- Cisco ISE family: `cisco_ise_endpoint_profile`, `cisco_ise_ssid`,
  `cisco_ise_security_group`, `cisco_ise_session_state`
- HSRP/VRRP discovery: `iprg_no`, `iprg_state`, `iprg_type`
- Internal-only: `cmp_type`, `duid`, `port_type`, `task_name`,
  `mgmt_ip_address` (use the Chris alias `device_management_ip` for the
  management-IP write — it's the only one the importer accepts).

If a column doesn't appear in §2's canonical writable set, **assume it
will be silently dropped** rather than experimenting on a live grid.

## 5. The CSV-import discovery-DB capacity

`setdiscoverycsv` writes per-IP `discoverydata` records into the grid's
discovery DB. This database has its own row-count ceiling, separate from
the Network Insight ND-* license cap. The only data point we have is
empirical: on a TE-V926-class vNIOS 9.0.3 GM with an ND-906 license, the
discovery worker errored with `"Grid master database limit reached and
restart discovery."` at approximately 37,000 records.

ND-* license caps gate the *NI-managed device population* — a different
bucket. CSV-imported metadata is not gated by the ND license. Per-model
ND device counts and per-model CSV-path ceilings are NOT published in
the public Trinzic X6 datasheet. Verify empirically against the grid
you're targeting before bulk imports.

## 6. The CSV-import path doesn't create discovery:device records

`setdiscoverycsv` writes only `ipv4address.discovered_data`. It does NOT
create `discovery:device`, `discovery:deviceinterface`,
`discovery:deviceneighbor`, or `discovery:devicecomponent` records. Those
are the native Network Insight surfaces and have no documented external
write path — the `discovery:device` WAPI object has `restrictions: ['create',
'delete', 'scheduling', 'csv']` so it explicitly cannot be created via
CSV or the WAPI.

For a host to appear under "Devices" in IPAM with the rich asset graph
(interfaces, components, neighbors), Network Insight has to actually poll
the device. NetMRI's IPAM Sync uses the same `setdiscoverycsv` path we
do — NetMRI maintains its own rich device database and ships only the
IP-level subset to NIOS.

## 7. Companion DNS records

`setdiscoverycsv` doesn't create A or PTR records. To make discovered
hostnames resolve, use the standard NIOS CSV import (`fileop?_function=
csv_import`) with `header-hostrecord`:

```text
header-hostrecord,fqdn*,addresses,configure_for_dns,configure_for_dhcp,view,comment
hostrecord,host01.site.env.corp.example.com,10.64.0.5,TRUE,FALSE,default,sim
```

`dns_csv.py` in this PR generates this format directly from a
`setdiscoverycsv` row set. Auth zones must exist first — create them with
`header-authzone,fqdn*,view,zone_format,comment` rows, then trigger a DNS
service restart via `grid:<ref>?_function=restartservices` before
inserting records (NIOS will not write into a freshly-created zone until
the service has restarted).

## 8. Critical operational notes

- **INSERT vs REPLACE on `csv_import`**: NIOS returns valid values
  `INSERT, UPDATE, REPLACE, DELETE, CUSTOM`. There is no `OVERRIDE`. Use
  `INSERT` for first writes — `REPLACE` requires existing records and
  errors with *"Parent zone of <fqdn> does not contain any records.
  Perform INSERT operation instead of REPLACE"* on empty zones.

- **DNS-service restart timing**: After `csv_import` creates auth zones,
  trigger a DNS service restart via
  `grid/<ref>?_function=restartservices` with body
  `{"service_option":"DNS","restart_option":"RESTART_IF_NEEDED"}` before
  pushing host records. The restart takes ~30-60 sec on vNIOS.

- **Discovery CSV is async**: `setdiscoverycsv` returns `{}` immediately
  and processes in the background. Poll `discoverytask?_ref=...current`
  for `state == COMPLETE`. Counts of `lines_processed`/`lines_failed` on
  the `discoverytask` object don't update — use `discoverytask.status`
  (a string with summary counts) instead.

- **Hostname collisions**: a CSV with duplicate `discovered_name` values
  for different IPs will result in some rows failing during `csv_import`
  of host records (NIOS rejects the duplicate FQDN). Use unique
  numeric IDs in your hostname generator (sequential row index, not
  `random.randint(1, 9999)`).
