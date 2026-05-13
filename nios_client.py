"""WAPI client for NIOS — session, upload, readback.

Config precedence: real env vars > .env in repo root > gm.ini > defaults.

Set NIOS_READONLY_GMS to a comma-separated list of GM hostnames/IPs that
must never receive writes (e.g. production grids). Defaults to empty.
"""
import configparser
import os
import urllib3
import requests

urllib3.disable_warnings()


def _read_dotenv(path):
    if not os.path.exists(path):
        return {}
    out = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.split("#", 1)[0].strip().strip("'\"")
        out[k.strip()] = v
    return out


def load_cfg(ini_path=None):
    here = os.path.dirname(os.path.abspath(__file__))
    dotenv = _read_dotenv(os.path.join(here, ".env"))

    def pick(env_key):
        if os.environ.get(env_key):
            return os.environ[env_key]
        if env_key in dotenv:
            return dotenv[env_key]
        return None

    cfg = {}
    for env_key, ini_key in [("NIOS_GM", "gm"), ("NIOS_API_VERSION", "api_version"),
                              ("NIOS_USER", "user"), ("NIOS_PASS", "pass"),
                              ("NIOS_VERIFY_CERT", "valid_cert")]:
        v = pick(env_key)
        if v is not None:
            cfg[ini_key] = v

    if ini_path is None:
        ini_path = os.path.join(here, "gm.ini")
    if os.path.exists(ini_path):
        c = configparser.ConfigParser()
        c.read(ini_path)
        if "NIOS" in c:
            for k in c["NIOS"]:
                cfg.setdefault(k, c["NIOS"][k].strip("'\""))

    cfg.setdefault("api_version", "v2.12")
    cfg.setdefault("valid_cert", "false")
    cfg.setdefault("user", "admin")
    cfg.setdefault("pass", "infoblox")
    return cfg


def _readonly_gms():
    raw = os.environ.get("NIOS_READONLY_GMS", "")
    return {x.strip() for x in raw.split(",") if x.strip()}


class WriteBlockedError(RuntimeError):
    pass


class NiosClient:
    def __init__(self, cfg, allow_writes=False):
        self.cfg = cfg
        self.base = f"https://{cfg['gm']}/wapi/{cfg['api_version']}/"
        self.auth = (cfg["user"], cfg["pass"])
        self.verify = cfg.get("valid_cert", "false").lower() == "true"
        self.read_only = cfg["gm"] in _readonly_gms() and not allow_writes
        if self.read_only:
            print(f"[nios_client] READ-ONLY for {cfg['gm']} "
                  f"(listed in NIOS_READONLY_GMS)")

    def get(self, path, **params):
        r = requests.get(self.base + path, params=params,
                         auth=self.auth, verify=self.verify, timeout=60)
        if not r.ok:
            raise RuntimeError(f"GET {path} {r.status_code}: {r.text}")
        return r.json()

    def post(self, path, json_body=None, **params):
        if self.read_only:
            raise WriteBlockedError(f"refusing POST {path} to {self.cfg['gm']}")
        r = requests.post(self.base + path, params=params, json=json_body,
                          auth=self.auth, verify=self.verify, timeout=60)
        if not r.ok:
            raise RuntimeError(f"POST {path} {r.status_code}: {r.text}")
        return r.json()

    def delete(self, ref):
        if self.read_only:
            raise WriteBlockedError(f"refusing DELETE {ref} on {self.cfg['gm']}")
        r = requests.delete(self.base + ref, auth=self.auth,
                            verify=self.verify, timeout=60)
        if not r.ok:
            raise RuntimeError(f"DELETE {ref} {r.status_code}: {r.text}")
        return r.json() if r.text else {}

    def upload_discovery_csv(self, csv_path, network_view="default", merge=True):
        if self.read_only:
            raise WriteBlockedError(f"refusing upload to {self.cfg['gm']}")
        r = requests.post(self.base + "fileop",
                          params={"_function": "uploadinit",
                                  "filename": os.path.basename(csv_path)},
                          auth=self.auth, verify=self.verify, timeout=60)
        r.raise_for_status()
        init = r.json()
        cookies = {"ibapauth": r.cookies["ibapauth"]}
        with open(csv_path, "rb") as fh:
            r = requests.post(init["url"], files={"filedata": fh},
                              cookies=cookies, verify=self.verify, timeout=120)
            r.raise_for_status()
        r = requests.post(self.base + "fileop",
                          params={"_function": "setdiscoverycsv",
                                  "token": init["token"],
                                  "merge_data": merge,
                                  "network_view": network_view},
                          cookies=cookies, verify=self.verify, timeout=120)
        if not r.ok:
            raise RuntimeError(f"setdiscoverycsv failed {r.status_code}: {r.text}")
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "text": r.text}

    def csv_import(self, csv_path, operation="INSERT", on_error="CONTINUE",
                    update_method="OVERRIDE", separator="COMMA"):
        if self.read_only:
            raise WriteBlockedError(f"refusing csv_import to {self.cfg['gm']}")
        r = requests.post(self.base + "fileop",
                          params={"_function": "uploadinit",
                                  "filename": os.path.basename(csv_path)},
                          auth=self.auth, verify=self.verify, timeout=60)
        r.raise_for_status()
        init = r.json()
        cookies = {"ibapauth": r.cookies["ibapauth"]}
        with open(csv_path, "rb") as fh:
            r = requests.post(init["url"], files={"filedata": fh},
                              cookies=cookies, verify=self.verify, timeout=300)
            r.raise_for_status()
        r = requests.post(self.base + "fileop",
                          params={"_function": "csv_import",
                                  "token": init["token"],
                                  "operation": operation,
                                  "on_error": on_error,
                                  "update_method": update_method,
                                  "separator": separator},
                          cookies=cookies, verify=self.verify, timeout=120)
        if not r.ok:
            raise RuntimeError(f"csv_import failed {r.status_code}: {r.text[:500]}")
        return r.json()

    def empty_recycle_bin(self):
        """Permanently delete everything in the grid Recycle Bin.

        NIOS soft-deletes: object DELETE moves rows to the bin where they
        still count against the appliance's DB capacity. Without this call
        the capacity meter can read >100% after a bulk teardown until the
        next nightly maintenance window.

        Returns the WAPI response (typically `{}`, async accept).
        """
        if self.read_only:
            raise WriteBlockedError(f"refusing empty_recycle_bin on {self.cfg['gm']}")
        grid_ref = self.get("grid")[0]["_ref"]
        return self.post(grid_ref, _function="empty_recycle_bin")

    def get_ipv4_discoverydata(self, network, network_view, fields=None):
        rf = fields or "ip_address,mac_address,names,types,usage,discovered_data"
        rows = self.get("ipv4address", network=network, network_view=network_view,
                         _max_results=4000, _return_fields=rf)
        if isinstance(rows, dict) and rows.get("Error"):
            raise RuntimeError(rows["Error"])
        return rows
