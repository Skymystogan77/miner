import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

CHECK_INTERVAL = 5

# Mining Config: DGB (DigiByte) di ZPool dengan 8 Cores/Workers
ALGO = "cwm_minotaurx"
HOST = "minotaurx.sea.mine.zpool.ca"
PORT = "7019"
WALLET = "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM"
PASSWORD = "c=DGB"
WORKERS = "8"

base_url = (
    f"https://webminer.pages.dev"
    f"?algorithm={ALGO}"
    f"&host={HOST}"
    f"&port={PORT}"
    f"&worker={WALLET}"
    f"&password={PASSWORD.replace('=', '%3D')}"
    f"&workers={WORKERS}"
)

chrome_options = Options()
chrome_options.binary_location = "/usr/bin/chromium-browser"
chrome_options.add_argument("--enable-javascript")
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--no-first-run")
chrome_options.add_argument("--ignore-certificate-errors")

def main():
    driver = None
    try:
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=chrome_options)

        print("[*] Starting WebMiner operation...")
        print(f"[*] Target Pool: {HOST}:{PORT}")
        print(f"[*] Wallet     : {WALLET}")
        print("--------------------------------------------------")

        driver.get(base_url)
        time.sleep(3)

        while True:
            try:
                hashrate_element = driver.find_element(By.CSS_SELECTOR, "span#hashrate strong")
                hashrate = hashrate_element.text.strip()
                if not hashrate:
                    hashrate = "0 H/s (Initializing...)"
                print(f"[{time.strftime('%H:%M:%S')}] ⚡ Hashrate: {hashrate}")
            except Exception:
                print(f"[{time.strftime('%H:%M:%S')}] ⏳ Memuat data hashrate...")
            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n[*] Mining dihentikan.")
    except Exception as e:
        print(f"[!] Critical error: {str(e)}")
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()
