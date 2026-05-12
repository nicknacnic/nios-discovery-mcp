---
name: nios-discovery
description: Use when the user wants to populate, validate, or audit Infoblox NIOS Discovered Data via setdiscoverycsv. Covers the empirically-verified writable CSV column set, the four port_* enum fields whose invalid values crash the appliance, the per-task ~50-row ceiling that forces chunked uploads, the IPAM-only nature of the CSV path (it doesn't create discovery:device records), and the MCP tools `describe_field`, `list_accepted_csv_names`, `generate_discovery_csv`, `validate_discovery_csv`, `import_discovery_csv`, `recon_grid`. Triggers — "populate Discovered Data for these IPs", "make this network look discovered in IPAM", "generate a NetMRI-shaped discovery CSV", "validate this discovery CSV before I import", "what fields does setdiscoverycsv actually accept", "audit discoverydata on a network view".
---

# nios-discovery — populate Infoblox IPAM Discovered Data via MCP

This skill wraps an empirically-verified discovery-CSV toolkit as an MCP server. The model never has to guess which `discoverydata` fields the importer accepts vs silently drops, what enum values are legal, or how big a chunk it can push without bricking the grid — the tools enforce the schema and the MCP host (Claude Desktop, Claude Code, Cursor, any MCP-capable client) drives the workflow.

The schema in `schema/discovery_schema.json` was built by probing five vNIOS test grids; the gotchas in `docs/LESSONS_LEARNED.md` cost three of those grids to discover. The `port_status` / `port_duplex` / `port_speed` / `port_link_status` enums in particular are not advisory — sending an off-list value crashes the discovery worker. The validator hard-rejects those before any upload.

## Prereqs

1. **NIOS Grid Manager** reachable from the machine running the MCP server, with WAPI v2.12+ enabled and credentials that can call `fileop?_function=setdiscoverycsv`.
2. **Python 3.10+** and the `mcp` package (`pip install -e .` from the repo root installs everything).
3. **A non-production test grid for first runs.** The crash bug in §2 of `docs/LESSONS_LEARNED.md` is real. Use `NIOS_READONLY_GMS=<your-prod-gm>` to hard-block writes to any GM you don't want to risk.
4. *(Optional)* Any MCP-capable client. The server is transport-stdio; configure your client to spawn `python -m mcp_server` with the repo as `cwd` and `NIOS_*` env vars set.

You **do not** need Anthropic Claude specifically. Any LLM + MCP client combo works — bring your own model, key, and host. The skill body assumes Claude Code / Claude Desktop because those are the most common, but Cursor, Continue, Glean, n8n, Tines, or a custom agent all drive the same tool surface.

## Setup — Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "nios-discovery": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/nios-discovery-mcp",
      "env": {
        "NIOS_GM": "192.0.2.10",
        "NIOS_USER": "admin",
        "NIOS_PASS": "changeme",
        "NIOS_VERIFY_CERT": "false"
      }
    }
  }
}
```

## Setup — Claude Code

Equivalent block in `~/.claude.json` (user-scope) or the project's `.mcp.json`:

```json
{
  "mcpServers": {
    "nios-discovery": {
      "command": "python",
      "args": ["-m", "mcp_server"],
      "cwd": "/path/to/nios-discovery-mcp",
      "env": { "NIOS_GM": "192.0.2.10", "NIOS_USER": "admin", "NIOS_PASS": "changeme" }
    }
  }
}
```

## Setup — any other MCP host

The transport is plain JSON-RPC over stdio. The launch command is `python -m mcp_server` with `NIOS_*` env vars and the repo as the working directory. There's no auth or HTTP surface — the model talks to the server, and the server talks to your grid over WAPI.

## Tool catalog

| Tool | What it does | Read or write |
|---|---|---|
| `describe_field(name)` | Schema entry for one CSV or WAPI field — type, enums, max length, csv-acceptance status | read |
| `list_accepted_csv_names()` | The 26 CSV header names the importer empirically accepts, plus the safe WAPI-internal names | read |
| `list_themes()` | Available data-generation themes (darknet / bsg / tng / random) | read |
| `list_device_classes()` | Golden template classes that round-trip cleanly (l3_switch, wireless_ap, endpoint) | read |
| `generate_discovery_csv(class, count, theme, base_ip, network_cidr, mode)` | Returns a populated discovery CSV as a string. `mode=simulate` produces a full simulated network; otherwise produces N rows of a single device class | read |
| `validate_discovery_csv(text)` | Per-row classification against the schema. **Hard-rejects** any row with an invalid `port_*` enum value — that's the appliance-crash trigger from §2 of LESSONS_LEARNED | read |
| `import_discovery_csv(text, network_view)` | Upload to the grid via `setdiscoverycsv`. Refuses if the GM is listed in `NIOS_READONLY_GMS`, refuses if validation fails, capacity-checks against `device_limits` first | **WRITE** |
| `recon_grid()` | Pulls the live WAPI struct + network views + existing `discoverydata` for one-shot orientation | read |

## How an agent should use this

The natural flow is: validate the field set you intend to write → generate or accept a CSV → validate → import. Every step except the last is read-only.

```
1. user: "populate discovered data for 198.51.100.0/24"
2. agent → describe_field("port_status")          # learn the enum is hard
3. agent → list_accepted_csv_names()              # know which columns to emit
4. agent → generate_discovery_csv(
              mode="simulate", network_cidr="198.51.100.0/24",
              fill_pct=60, theme="random")        # produces CSV text
5. agent → validate_discovery_csv(<text>)         # MUST pass with zero rejected rows
6. agent → import_discovery_csv(<text>, "default")
7. agent reports: rows landed, networks affected, any failed chunks
```

If the user supplies their own CSV, skip steps 3–4 and go straight to `validate_discovery_csv`.

## Hard rules

- **Never bypass validation.** Calling `import_discovery_csv` on a CSV that `validate_discovery_csv` flagged is the single most likely way to brick an appliance. The MCP server already wires these together, but if you're routing around the server (raw `setdiscoverycsv` via `chunk_push_discovery.py`), validate first.
- **`port_status`, `port_duplex`, `port_speed`, `port_link_status` accept only their documented enum values.** Off-list values crash the discovery worker. The allow-lists are in `docs/CSV_FORMAT.md` and enforced by the validator.
- **Chunk size ≤ 40 rows per `setdiscoverycsv` invocation.** The empirical per-task ceiling is around 50; 40 is the safe target. `import_discovery_csv` chunks automatically. If you're pushing your own pipeline, use `chunk_push_discovery.py` or replicate its 40-row batching.
- **The CSV path writes `ipv4address.discovered_data` only.** It does NOT create `discovery:device`, `discoveryinterface`, or `discoveryneighbor` records — those are Network Insight territory and have no documented external write path. If the user expects "device with full topology under Network Insight → Devices", explain that the CSV path can't do that.
- **First write to an empty zone uses `INSERT`; re-syncs use `REPLACE`.** `OVERRIDE` is not a valid `csv_import` operation despite what some forum threads claim.
- **Restart the DNS service between creating a zone and inserting records.** The grid's "perform INSERT instead of REPLACE" error message masks the underlying not-yet-restarted state. See `docs/LESSONS_LEARNED.md` §7.

## What to suggest if a write is refused

`import_discovery_csv` will refuse for one of three reasons:

1. **`NIOS_READONLY_GMS` lists the GM.** Tell the user to either remove the GM from the list (acknowledge they're writing to it) or point `NIOS_GM` at a test grid.
2. **Validation failed.** Show the rejected rows. Suggest fixes — usually it's a `port_*` value the user copied from somewhere that doesn't match the importer's allow-list.
3. **Capacity check failed.** The `device_limits` module flagged that the projected row count would push the grid past its observed CSV-path ceiling (~37k rows on vNIOS in our tests). Suggest a smaller CIDR or splitting the import across multiple grids.

## When NOT to use this skill

- **The user wants Network Insight to discover devices** (active SNMP polling, topology graph). The CSV path can't do that; suggest configuring NI directly.
- **The user wants DNS records** (host, A, AAAA, CNAME, PTR). That's `csv_import` not `setdiscoverycsv`. The `dns_csv.py` helper in this repo generates those, but they go through a different fileop endpoint.
- **The user has a NetMRI deployment already wired to IPAM Sync.** NetMRI already pushes `setdiscoverycsv` on a schedule; running this skill in parallel will produce merge churn.

## Where to dig deeper

- `docs/CSV_FORMAT.md` — field-by-field writable set, enum tables, CSV-name ↔ WAPI-internal-name translation
- `docs/LESSONS_LEARNED.md` — 11 operational gotchas including the brick-the-grid bug, the per-task ceiling, the DNS-restart-between-zone-and-records sequencing, and before/after IPAM screenshots
- `schema/discovery_schema.json` — machine-readable schema with the `csv_import_evidence` block listing the 26 accepted, 52 silently-dropped, and 4 row-rejection-trigger CSV names
- `templates/` — golden CSVs that import cleanly and round-trip
