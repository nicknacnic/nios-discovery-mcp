"""Push a discovery CSV in small chunks to stay under the per-task ceiling.

Empirically on the homelab grid (NIOS 9.1.0 + ND-906) the per-task limit
for setdiscoverycsv landed in the 40-50 row range — pushes of >50 error
with "Grid master database limit reached" even when total grid capacity
is plenty.

This script splits a large discovery CSV into N-row chunks and pushes
them one at a time, waiting for each task to complete before the next.
Honors existing rows: rows that already have discoverydata get coerced
to "updated" by setdiscoverycsv merge_data semantics, no errors.
"""
import argparse
import csv
import sys
import time

from nios_client import NiosClient, load_cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_path")
    ap.add_argument("--chunk-size", type=int, default=40)
    ap.add_argument("--view", default="default")
    ap.add_argument("--start", type=int, default=0,
                    help="resume from row N (1-indexed; for restarts)")
    ap.add_argument("--max-rows", type=int, default=None,
                    help="cap total rows pushed for a smaller demo")
    ap.add_argument("--per-task-wait", type=int, default=20,
                    help="seconds to wait for each task to complete before next push")
    ap.add_argument("--mark-host-cidr", default=None,
                    help="parent CIDR to scan & tag with (DISCOVERED) after the "
                         "push completes (e.g. 198.18.0.0/16). Skip to disable.")
    ap.add_argument("--mark-threshold", type=float, default=5.0,
                    help="percent populated to qualify for full marker (default 5%%)")
    args = ap.parse_args()

    cli = NiosClient(load_cfg(), allow_writes=True)
    if cli.read_only:
        print("ERR: gm.ini/.env points at a read-only grid"); return 2

    rows = list(csv.DictReader(open(args.csv_path)))
    if args.max_rows:
        rows = rows[:args.max_rows]
    if args.start:
        rows = rows[args.start:]
    fields = list(rows[0].keys()) if rows else []

    total = len(rows)
    print(f"[chunk-push] target: {cli.cfg['gm']}  rows={total}  chunk={args.chunk_size}")
    print(f"[chunk-push] expected chunks: {(total + args.chunk_size - 1) // args.chunk_size}")

    landed = 0
    failed_chunks = 0
    for i in range(0, total, args.chunk_size):
        chunk = rows[i:i + args.chunk_size]
        path = "/tmp/chunk_push.csv"
        with open(path, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for r in chunk:
                w.writerow(r)
        try:
            cli.upload_discovery_csv(path, network_view=args.view, merge=True)
        except Exception as e:
            msg = str(e)
            if "discovery operation is currently running" in msg:
                # Wait and retry
                print(f"  [{time.strftime('%H:%M:%S')}] task busy, waiting 30s...")
                time.sleep(30)
                try:
                    cli.upload_discovery_csv(path, network_view=args.view, merge=True)
                except Exception as e2:
                    print(f"  [{time.strftime('%H:%M:%S')}] still erroring: {e2}")
                    failed_chunks += 1
                    continue
            else:
                print(f"  [{time.strftime('%H:%M:%S')}] upload err: {e}")
                failed_chunks += 1
                continue

        # Wait for task to complete
        deadline = time.time() + 120
        last_state = None
        last_counts = None
        while time.time() < deadline:
            time.sleep(args.per_task_wait)
            try:
                dt = cli.get("discoverytask",
                             _return_fields="state,status,csv_file_name")
            except Exception:
                continue
            cur = next((d for d in dt if "current" in d["_ref"]), None)
            if not cur:
                continue
            state = cur.get("state")
            counts = {}
            for line in (cur.get("status") or "").split("\n"):
                if ":" in line and "status" not in line.lower() and "admin" not in line.lower():
                    try:
                        k, v = line.strip().split(":", 1)
                        counts[k.strip()] = v.strip()
                    except Exception:
                        pass
            if state != last_state:
                last_state = state
            if state in ("COMPLETE", "ERROR", "FAILED"):
                disc = int(counts.get("Discovered", "0") or 0)
                landed += disc
                pct = 100 * (i + len(chunk)) / total
                status_str = "OK" if state == "COMPLETE" else f"ERR(disc={disc})"
                print(f"  [{time.strftime('%H:%M:%S')}] chunk {i//args.chunk_size + 1}: "
                      f"{status_str}  rows={i+1}-{i+len(chunk)}/{total} ({pct:.1f}%)  "
                      f"task-disc={disc}  cumulative-landed={landed}")
                break

    print(f"\n[chunk-push] DONE. landed={landed}  failed_chunks={failed_chunks}")

    if args.mark_host_cidr:
        print(f"\n[chunk-push] marking populated networks under {args.mark_host_cidr} ...")
        try:
            import mark_discovered
            result = mark_discovered.scan_and_mark(
                args.mark_host_cidr, view=args.view,
                threshold_pct=args.mark_threshold)
            print(f"[chunk-push] mark result: {result}")
        except Exception as e:
            print(f"[chunk-push] mark step failed: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
