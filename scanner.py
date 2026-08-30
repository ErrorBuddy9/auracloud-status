import os
import subprocess
import sys
import requests

TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

if not TURSO_URL or not TURSO_TOKEN:
    print("Error: Missing TURSO_DATABASE_URL or TURSO_AUTH_TOKEN environment variables.")
    sys.exit(1)

# Format Turso URL to HTTPS endpoint for HTTP Pipeline API
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
    # Strip protocol/ports if present
    clean_target = ip_or_host.replace("http://", "").replace("https://", "").split("/")[0].split(":")[0]
    
    res = subprocess.run(
        ["ping", "-c", "1", "-W", "2", clean_target],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return "up" if res.returncode == 0 else "down"

def main():
    print("Starting node telemetry scan...")
    try:
        # 1. Fetch active nodes from Turso
        res = query_turso("SELECT name, ip_address FROM nodes WHERE is_active = 1")
        
        results = res.get("results", [])
        if not results or "response" not in results[0]:
            print("No active nodes found or database returned empty results.")
            return

        rows = results[0]["response"]["result"].get("rows", [])
        print(f"Found {len(rows)} active node(s).")

        # 2. Run ICMP check & store telemetry
        for row in rows:
            name = row[0]["value"]
            ip = row[1]["value"]
            
            status = ping_target(ip)
            print(f"[{status.upper()}] Node: {name} ({ip})")
            
            query_turso(
                "INSERT INTO node_history (node_name, status) VALUES (?, ?)",
                [name, status]
            )

        print("Scan finished and synced to database.")
        
    except Exception as e:
        print(f"Execution error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
