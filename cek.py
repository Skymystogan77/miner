import os
import sys
import argparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# Reconfigure encoding di Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
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
                found_token = next((p for p in parts if p.startswith("e2b_")), None)
                if found_token:
                    token = found_token
                    label = parts[0] if parts[0] != token else f"user-{line_no}"
                elif len(parts) >= 2:
                    label, token = parts[0], parts[1]
                else:
                    token = parts[0]
            else:
                token = line

            if token:
                accounts.append({
                    "index": line_no,
                    "label": label,
                    "token": token
                })
    return accounts

def check_key_status(acc, deep_check=False):
    token = acc["token"]
    label = acc["label"]
    headers = {
        "X-API-Key": token,
        "Accept": "application/json"
    }

    result = {
        "label": label,
        "token_short": f"{token[:10]}...{token[-4:]}",
        "status": "UNKNOWN",
        "code": 0,
        "detail": "",
        "can_spawn": False
    }

    # 1. Fast REST API Check
    try:
        resp = requests.get("https://api.e2b.dev/sandboxes", headers=headers, timeout=10)
        result["code"] = resp.status_code

        if resp.status_code == 200:
            result["status"] = "VALID / ACTIVE"
            result["can_spawn"] = True
            result["detail"] = "API response OK (200)"
        elif resp.status_code == 401:
            result["status"] = "INVALID KEY"
            result["detail"] = "Unauthorized / Key Salah (401)"
        elif resp.status_code == 403:
            result["status"] = "BANNED / RESTRICTED"
            result["detail"] = "Forbidden (403) - Soft-ban / Abuse / Quota Zero"
        elif resp.status_code == 429:
            result["status"] = "RATE LIMITED"
            result["detail"] = "Too Many Requests (429)"
        else:
            result["status"] = f"HTTP {resp.status_code}"
            result["detail"] = resp.text[:60]
    except Exception as e:
        result["status"] = "ERROR"
        result["detail"] = str(e)
        return result

    # 2. Deep Check: Coba Spawn Sandbox beneran (Opsional)
    if deep_check and result["can_spawn"]:
        try:
            from e2b import Sandbox
            print(f"   [DEEP] Testing Sandbox Spawn for {label}...")
            sbx = Sandbox.create(template="base", api_key=token, timeout=15)
            sbx_id = sbx.sandbox_id
            sbx.kill()
            result["detail"] += f" | Spawn Test PASSED [{sbx_id[:8]}]"
        except Exception as spawn_err:
            result["can_spawn"] = False
            result["status"] = "SPAWN FAILED"
            result["detail"] += f" | Spawn Failed: {str(spawn_err)[:50]}"

    return result

def main():
    parser = argparse.ArgumentParser(description="E2B API Key Validator Tool")
    parser.add_argument("--file", "-f", default="list.txt", help="File list API Key (default: list.txt)")
    parser.add_argument("--deep", "-d", action="store_true", help="Tes spawn 1 sandbox kecil untuk konfirmasi")
    args = parser.parse_args()

    file_path = args.file
    accounts = parse_keys_file(file_path)

    if not accounts:
        print(f"❌ File '{file_path}' tidak ditemukan atau kosong.")
        sys.exit(1)

    print("=============================================================")
    print(f"🔍 E2B API KEY CHECKER (Total Key: {len(accounts)})")
    print(f"📂 Source File: {file_path}")
    print(f"⚡ Mode: {'DEEP CHECK (REST + Test Spawn)' if args.deep else 'FAST CHECK (REST API Only)'}")
    print("=============================================================\n")

    results = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(check_key_status, acc, args.deep): acc for acc in accounts}
        for fut in as_completed(futures):
            res = fut.result()
            results.append(res)

    # Sort berdasarkan index urutan asli
    results.sort(key=lambda x: accounts[next(i for i, a in enumerate(accounts) if a['label'] == x['label'])]['index'])

    valid_count = 0
    banned_count = 0
    invalid_count = 0

    print(f"{'INDEX':<6} | {'LABEL':<15} | {'KEY':<18} | {'STATUS':<20} | {'DETAIL'}")
    print("-" * 90)

    for i, res in enumerate(results, 1):
        status = res["status"]
        if "VALID" in status:
            valid_count += 1
            icon = "🟢"
        elif "BANNED" in status or "FAILED" in status:
            banned_count += 1
            icon = "🔴"
        else:
            invalid_count += 1
            icon = "❌"

        print(f"{i:<6} | {res['label']:<15} | {res['token_short']:<18} | {icon} {status:<17} | {res['detail']}")

    print("-" * 90)
    print(f"📊 RINGKASAN: Total {len(results)} | 🟢 Valid: {valid_count} | 🔴 Banned/Restricted: {banned_count} | ❌ Invalid: {invalid_count}\n")

if __name__ == "__main__":
    main()