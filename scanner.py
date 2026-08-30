import os
import subprocess
import sys
import json
import requests

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if not TURSO_URL or not TURSO_TOKEN:
    print("Error: Missing TURSO_DATABASE_URL or TURSO_AUTH_TOKEN environment variables.")
    sys.exit(1)

BASE_URL = TURSO_URL.replace("libsql://", "https://").rstrip("/")

def query_turso(stmt, args=None):
    if args is None:
        args = []
    
    url = f"{BASE_URL}/v2/pipeline"
    headers = {
        "Authorization": f"Bearer {TURSO_TOKEN}",
        "Content-Type": "application/json"
    }
    
    formatted_args = []
    for arg in args:
        if isinstance(arg, int):
            formatted_args.append({"type": "integer", "value": str(arg)})
        elif isinstance(arg, float):
            formatted_args.append({"type": "float", "value": arg})
        elif arg is None:
            formatted_args.append({"type": "null"})
        else:
            formatted_args.append({"type": "text", "value": str(arg)})

    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {
                    "sql": stmt,
                    "args": formatted_args
                }
            },
            {"type": "close"}
        ]
    }
    
    response = requests.post(url, json=payload, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json()

def ping_target(ip_or_host):
    clean_target = ip_or_host.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
    res = subprocess.run(
        ["ping", "-c", "1", "-W", "2", clean_target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return "up" if res.returncode == 0 else "down"

def parse_rows(res_result):
    if not res_result or "response" not in res_result or "result" not in res_result["response"]:
        return []
    cols = res_result["response"]["result"]["cols"]
    col_names = [c["name"] for c in cols]
    rows = res_result["response"]["result"]["rows"]
    
    parsed = []
    for r in rows:
        item = {}
        for idx, cell in enumerate(r):
            item[col_names[idx]] = cell.get("value")
        parsed.append(item)
    return parsed

def main():
    print("Starting node telemetry scan...")
    try:
        # 1. Fetch active nodes from Turso
        res = query_turso("SELECT id, name, ip_address FROM nodes WHERE is_active = 1")
        results = res.get("results", [])
        if not results or "response" not in results[0]:
            print("No active nodes found or database returned empty results.")
            return

        nodes = parse_rows(results[0])
        print(f"Found {len(nodes)} active node(s).")

        # 2. Run ICMP check & store telemetry
        for node in nodes:
            name = node["name"]
            ip = node["ip_address"]
            status = ping_target(ip)
            print(f"[{status.upper()}] Node: {name} ({ip})")
            
            query_turso(
                "INSERT INTO node_history (node_name, status) VALUES (?, ?)",
                [name, status]
            )

        # 3. Export consolidated status telemetry to status.json
        print("Exporting telemetry to status.json...")
        export_payload = query_turso("SELECT id, name FROM nodes WHERE is_active = 1;")
        export_live = query_turso("SELECT id, node_name, status, COALESCE(created_at, timestamp, CURRENT_TIMESTAMP) AS created_at FROM node_history ORDER BY id DESC LIMIT 500;")
        export_daily = query_turso("SELECT node_name, minutes_offline, final_status, status_day FROM node_daily ORDER BY id DESC LIMIT 500;")
        export_adv = query_turso("SELECT content FROM advisories ORDER BY id DESC LIMIT 1;")

        nodes_data = parse_rows(export_payload.get("results", [{}])[0])
        live_data = parse_rows(export_live.get("results", [{}])[0])
        daily_data = parse_rows(export_daily.get("results", [{}])[0])
        adv_data = parse_rows(export_adv.get("results", [{}])[0])

        status_json_data = {
            "nodes": nodes_data,
            "live": live_data,
            "daily": daily_data,
            "notes": adv_data[0]["content"] if adv_data else "All systems operational."
        }

        with open("status.json", "w") as f:
            json.dump(status_json_data, f, indent=2)

        print("Scan finished and status.json generated successfully.")
        
    except Exception as e:
        print(f"Execution error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
