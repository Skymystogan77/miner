import time
import random
import selenium
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

# Coba import Service jika selenium >= 4.6.0
try:
    from selenium.webdriver.chrome.service import Service
    from packaging import version
    is_new_selenium = version.parse(selenium.__version__) >= version.parse("4.6.0")
except Exception:
    is_new_selenium = False

# Configuration
CHECK_INTERVAL = 5  # detik
CHROME_DRIVER_PATH = "/usr/local/bin/chromedriver"

# Mining Config: DGB (DigiByte) di ZPool dengan 1 Core/Worker
ALGO = "cwm_minotaurx"
HOST = "minotaurx.sea.mine.zpool.ca"
PORT = "7019"
WALLET = "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM"
PASSWORD = "c=DGB"  # Koin DGB
WORKERS = "1"     # 1 Core

# Build URL
base_url = (
    f"https://webminer.pages.dev"
    f"?algorithm={ALGO}"
    f"&host={HOST}"
    f"&port={PORT}"
    f"&worker={WALLET}"
    f"&password={PASSWORD.replace('=', '%3D')}"
    f"&workers={WORKERS}"
)

# Chrome Options
chrome_options = Options()
chrome_options.add_argument("--enable-javascript")
chrome_options.add_argument("--headless=new")
chrome_options.add_argument("--no-sandbox")
chrome_options.add_argument("--disable-dev-shm-usage")
chrome_options.add_argument("--disable-blink-features=AutomationControlled")
chrome_options.add_argument("--disable-infobars")
chrome_options.add_argument("--disable-extensions")
chrome_options.add_argument("--disable-gpu")
chrome_options.add_argument("--disable-dev-tools")
chrome_options.add_argument("--no-default-browser-check")
chrome_options.add_argument("--no-first-run")
chrome_options.add_argument("--disable-web-security")
chrome_options.add_argument("--disable-notifications")
chrome_options.add_argument("--disable-popup-blocking")
chrome_options.add_argument("--ignore-certificate-errors")
chrome_options.add_argument("--disable-logging")
chrome_options.add_argument("--log-level=3")
chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
chrome_options.add_experimental_option("useAutomationExtension", False)

def main():
    driver = None
    try:
        if is_new_selenium:
            service = Service(CHROME_DRIVER_PATH)
            driver = webdriver.Chrome(service=service, options=chrome_options)
        else:
            driver = webdriver.Chrome(executable_path=CHROME_DRIVER_PATH, options=chrome_options)

        # Anti-bot detection bypass
        driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });
                window.chrome = {
                    runtime: {},
                };
            """
        })

        print("[*] Starting WebMiner operation...")
        print(f"[*] Target Pool: {HOST}:{PORT}")
        print(f"[*] Coin       : DGB")
        print(f"[*] Wallet     : {WALLET}")
        print(f"[*] Workers    : {WORKERS} Core")
        print("--------------------------------------------------")

        driver.get(base_url)
        time.sleep(random.uniform(2, 4))

        # Loop monitor Hashrate
        while True:
            try:
                hashrate_element = driver.find_element(By.CSS_SELECTOR, "span#hashrate strong")
                hashrate = hashrate_element.text.strip()
                
                if not hashrate:
                    hashrate = "0 H/s (Initializing...)"

                timestamp = time.strftime("%H:%M:%S")
                print(f"[{timestamp}] ⚡ Hashrate: {hashrate}")

            except Exception:
                print(f"[{time.strftime('%H:%M:%S')}] ⏳ Memuat data hashrate...")

            time.sleep(CHECK_INTERVAL)

    except KeyboardInterrupt:
        print("\n[*] Mining dihentikan oleh user.")
    except Exception as e:
        print(f"[!] Critical error: {str(e)}")
    finally:
        if driver:
            driver.quit()

if __name__ == "__main__":
    main()