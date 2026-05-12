# nios-discovery-mcp

**An MCP server + agentic skill for populating Infoblox NIOS Discovered Data via `setdiscoverycsv`.**

Bring your own LLM, your own MCP client, your own grid. The server exposes a tool catalog grounded in an empirically-verified schema (probed across five vNIOS test grids — three were lost making this); the skill in `skills/nios-discovery/` tells any model how to use it.

This is the standalone agentic packaging. The plain-Python tooling and empirical schema live in the upstream PRs to [ccmarris/nios_discovery_csv](https://github.com/ccmarris/nios_discovery_csv) — this repo wraps them with MCP + a Claude-Code-style skill.

> Built on top of Chris Marrison's original [Hacking Infoblox Discovery for Fun and Profit](https://www.infoblox.com/blog/community/hacking-infoblox-discovery-for-fun-and-profit-its-just-another-csv/).

---

## What you get

| | |
|---|---|
| **MCP server** (`mcp_server.py`) | 8 tools: `describe_field`, `list_accepted_csv_names`, `list_themes`, `list_device_classes`, `generate_discovery_csv`, `validate_discovery_csv`, `import_discovery_csv`, `recon_grid` |
| **Agentic skill** (`skills/nios-discovery/SKILL.md`) | A model-facing brief that explains the workflow, hard rules, when not to use this, and where to dig deeper |
| **Empirical schema** (`schema/discovery_schema.json`) | 26 confirmed-accepted CSV column names, 52 confirmed silently-dropped names, 4 confirmed row-rejection triggers, full enum allow-lists |
| **Golden templates** (`templates/`) | L3 switch, wireless AP, endpoint — round-trip cleanly into IPAM's Discovered Data panel |
| **Hard-rules validator** | Refuses any CSV with invalid `port_status` / `port_duplex` / `port_speed` / `port_link_status` values (those crash the discovery worker — see [docs/LESSONS_LEARNED.md §2](docs/LESSONS_LEARNED.md)) |
| **Chunking pipeline** (`chunk_push_discovery.py`) | 40-row batches; safe below the empirical ~50-row per-task ceiling |
| **Network/DNS helpers** | `network_hierarchy.py`, `mark_discovered.py`, `dns_csv.py` |
| **Data generators** (`simulator.py`, `themes.py`) | Realistic / nerd / themed (darknet, BSG, TNG) populated CSVs |

---

## Quick start

```bash
git clone https://github.com/nicknacnic/nios-discovery-mcp
cd nios-discovery-mcp
pip install -e .              # installs requests, urllib3, mcp
cp .env.example .env          # then edit NIOS_GM / NIOS_USER / NIOS_PASS
python cli.py schema          # smoke-test: prints schema summary
python cli.py themes          # smoke-test: lists generator themes
```

Now wire the MCP server into your client. The launch command is the same regardless of client:

```
command: python
args:    ["-m", "mcp_server"]
cwd:     <absolute path to this repo>
env:     NIOS_GM, NIOS_USER, NIOS_PASS, (NIOS_VERIFY_CERT, NIOS_READONLY_GMS)
```

See `.mcp.json.example` for a drop-in example block.

---

## Bring your own model

The MCP transport is stdio JSON-RPC. There is no auth surface, no HTTP listener, no cloud dependency. Any MCP-capable client works:

- **Claude Desktop** — paste the block from `.mcp.json.example` into `claude_desktop_config.json`
- **Claude Code** — same block in `~/.claude.json` or the project's `.mcp.json`
- **Cursor / Continue / Cline** — point their MCP config at the same command + args
- **Glean / Tines / n8n / custom in-house agent** — any MCP SDK can spawn the server

You pay for whichever model you point at it. The server doesn't care which.

---

## Safety

- **`NIOS_READONLY_GMS`** is a comma-separated list of GM hostnames/IPs that the client will refuse to write to. Set it to your production GM to make the entire toolkit read-only against that grid; remove the entry when you've consciously decided to write.
- **The validator hard-rejects rows with invalid `port_*` enum values.** This is the appliance-crash trigger from `docs/LESSONS_LEARNED.md` §2; the rule is not advisory.
- **`import_discovery_csv` chunks at 40 rows.** The empirical per-task ceiling is around 50 on vNIOS; 40 is the safe target.
- **The CSV path populates `ipv4address.discovered_data` only.** It does NOT create `discovery:device` records — that's Network Insight territory and has no documented external write path. The skill explains how to set expectations with the user.

---

## How this relates to the upstream PRs

- **PR #1** ([ccmarris/nios_discovery_csv#1](https://github.com/ccmarris/nios_discovery_csv/pull/1)) — minimum viable empirical findings: docs, schema, templates
- **PR #2** ([ccmarris/nios_discovery_csv#2](https://github.com/ccmarris/nios_discovery_csv/pull/2)) — tooling and harness: lint, CI, recon/probe scripts, tests
- **This repo** — MCP + skill packaging on top of PR1/PR2 contents. The agentic surface, model-agnostic. Not intended for upstream.

---

## License

MIT. See `LICENSE`.

## Acknowledgments

- Chris Marrison ([@ccmarris](https://github.com/ccmarris)) for the original `nios_discovery_csv` toolkit and the [community blog post](https://www.infoblox.com/blog/community/hacking-infoblox-discovery-for-fun-and-profit-its-just-another-csv/) that motivated this work.
- The NetMRI 7.5.1 IPAM Sync documentation, which turned out to be the authoritative source for the writable CSV column set the WAPI docs don't quite spell out.
