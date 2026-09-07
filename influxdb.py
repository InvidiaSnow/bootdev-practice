"""InfluxDB query helpers: schema discovery, Flux queries, diagnostics."""

import requests
from datetime import datetime
from .ui import C, ok, warn, err, heading

ORG_ID = "31b1bdd5d5103ead"


def flux_query(session: requests.Session, proxy_base: str, query: str,
               debug: bool = False, annotated: bool = False) -> str | None:
    """Run a Flux query. Returns raw CSV text, or None on error/timeout.

    If annotated=True, uses JSON body with dialect to include #group/#datatype/#default
    annotations and comma delimiters (matching InfluxDB Data Explorer export format).
    """
    headers = dict(session.headers)
    headers["Accept"] = "application/csv"
    if annotated:
        headers["Content-Type"] = "application/json"
        body = {
            "query": query,
            "dialect": {
                "header": True,
                "delimiter": ",",
                "annotations": ["group", "datatype", "default"],
            },
        }
        post_kwargs = {"json": body}
    else:
        headers["Content-Type"] = "application/vnd.flux"
        post_kwargs = {"data": query}
    try:
        resp = session.post(
            f"{proxy_base}/api/v2/query?orgID={ORG_ID}",
            headers=headers,
            timeout=60,
            **post_kwargs,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        if debug:
            err(f"Connection error: {e}")
        return None
    if debug:
        if resp.status_code != 200:
            print(f"    {C.RED}HTTP {resp.status_code}: {resp.text[:200]}{C.RESET}")
    return resp.text if resp.status_code == 200 else None


def _parse_csv_values(raw_csv: str) -> list[str]:
    """Extract _value column from Flux CSV output.

    Flux CSV format:
      #group,false,false,false     <- annotation (starts with #)
      ,result,table,_value         <- header row
      ,_result,0,Data              <- data row (empty first col)
    """
    values = []
    for line in raw_csv.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split(",")
        if len(parts) >= 4:
            val = parts[-1].strip()
            if val and val != "_value":
                values.append(val)
    return sorted(set(values))


# ── Schema discovery ─────────────────────────────────────────────────────────

def list_buckets(session: requests.Session, proxy_base: str) -> list[str]:
    resp = session.get(f"{proxy_base}/api/v2/buckets?limit=100", timeout=15)
    if resp.status_code != 200:
        return []
    return [b["name"] for b in resp.json().get("buckets", []) if b.get("type") == "user"]


def list_measurements(session: requests.Session, proxy_base: str, bucket: str,
                      debug: bool = False) -> list[str]:
    query = f'import "influxdata/influxdb/schema"\nschema.measurements(bucket: "{bucket}")'
    raw = flux_query(session, proxy_base, query, debug=debug)
    return _parse_csv_values(raw) if raw else []


def list_field_keys(session: requests.Session, proxy_base: str, bucket: str,
                    debug: bool = False) -> list[str]:
    query = f'import "influxdata/influxdb/schema"\nschema.fieldKeys(bucket: "{bucket}")'
    raw = flux_query(session, proxy_base, query, debug=debug)
    return _parse_csv_values(raw) if raw else []


def list_tag_keys(session: requests.Session, proxy_base: str,
                  bucket: str, measurement: str) -> list[str]:
    query = (
        'import "influxdata/influxdb/schema"\n'
        f'schema.measurementTagKeys(bucket: "{bucket}", measurement: "{measurement}")'
    )
    raw = flux_query(session, proxy_base, query)
    if not raw:
        return []
    return [t for t in _parse_csv_values(raw) if not t.startswith("_")]


def list_serial_numbers(session: requests.Session, proxy_base: str,
                        bucket: str, measurement: str) -> list[str] | None:
    """Returns list of serial numbers, or None if the tag doesn't exist."""
    tags = list_tag_keys(session, proxy_base, bucket, measurement)
    if "Serial number" not in tags:
        return None
    query = (
        'import "influxdata/influxdb/schema"\n'
        f'schema.measurementTagValues(bucket: "{bucket}", '
        f'measurement: "{measurement}", tag: "Serial number")'
    )
    raw = flux_query(session, proxy_base, query)
    if not raw:
        return None
    values = _parse_csv_values(raw)
    return values if values else None


def get_time_range(session: requests.Session, proxy_base: str,
                   bucket: str, measurement: str,
                   serial: str | None) -> tuple[str, str] | None:
    """Returns (earliest_timestamp, latest_timestamp) or None."""
    serial_filter = (
        f'  |> filter(fn: (r) => r["Serial number"] == "{serial}")\n'
        if serial else ""
    )
    base_query = (
        f'from(bucket: "{bucket}")\n'
        f'  |> range(start: -10y)\n'
        f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
        f'{serial_filter}'
        f'  |> group()\n'
    )
    first_raw = flux_query(session, proxy_base,
                           base_query + '  |> first()\n  |> keep(columns: ["_time"])')
    last_raw = flux_query(session, proxy_base,
                          base_query + '  |> last()\n  |> keep(columns: ["_time"])')

    def extract_time(csv_text: str) -> str | None:
        for line in csv_text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            for p in line.split(","):
                p = p.strip()
                if "T" in p and p.endswith("Z"):
                    return p
        return None

    if first_raw and last_raw:
        t1, t2 = extract_time(first_raw), extract_time(last_raw)
        if t1 and t2:
            return (t1, t2)
    return None


def diagnose_bucket(session: requests.Session, proxy_base: str, bucket: str):
    """Print diagnostic info when a bucket seems empty."""
    heading("Bucket diagnostics")
    print(f"  Inspecting schema for bucket '{bucket}'...\n")

    measurements = list_measurements(session, proxy_base, bucket)
    fields = list_field_keys(session, proxy_base, bucket)

    print(f"  Measurements: {C.BOLD}{measurements or '(none found)'}{C.RESET}")
    print(f"  Fields:       {C.BOLD}{fields or '(none found)'}{C.RESET}")

    if measurements:
        for m in measurements:
            tags = list_tag_keys(session, proxy_base, bucket, m)
            print(f"  Tags in '{m}': {C.BOLD}{tags or '(none)'}{C.RESET}")

    if not measurements:
        err("Bucket has no measurements — it may be empty or use a different schema.")
    elif "Data" not in measurements:
        warn(f"Expected measurement 'Data' but found: {measurements}")
    if fields and "Temperature" not in fields:
        warn(f"Expected field 'Temperature' but found: {fields}")
    print()


# ── Data query ───────────────────────────────────────────────────────────────

def build_chunk_query(bucket: str, measurement: str, field: str,
                      serial: str | None,
                      start: datetime, stop: datetime) -> str:
    """Build a Flux query for one time window."""
    start_rfc = start.strftime("%Y-%m-%dT%H:%M:%SZ")
    stop_rfc = stop.strftime("%Y-%m-%dT%H:%M:%SZ")
    serial_filter = (
        f'  |> filter(fn: (r) => r["Serial number"] == "{serial}")\n'
        if serial else ""
    )
    drop_cols = '["_result","_start","_stop","_field","_measurement"'
    if serial:
        drop_cols += ',"Serial number"'
    drop_cols += ']'
    return (
        'import "strings"\n'
        f'from(bucket: "{bucket}")\n'
        f'  |> range(start: {start_rfc}, stop: {stop_rfc})\n'
        f'  |> filter(fn: (r) => r["_measurement"] == "{measurement}")\n'
        f'  |> filter(fn: (r) => r["_field"] == "{field}")\n'
        f'{serial_filter}'
        '  |> filter(fn: (r) => not strings.containsAny(v: r["Meter"], chars: "-"))\n'
        f'  |> drop(columns: {drop_cols})\n'
        '  |> map(fn: (r) => ({r with Meter: float(v: r.Meter)}))\n'
        '  |> sort(columns: ["_time"], desc: true)\n'
        '  |> sort(columns: ["Meter"], desc: false)'
    )
