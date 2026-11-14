import time
import random
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By

CHECK_INTERVAL = 5  # detik

# Mining Config: DGB (DigiByte) di ZPool dengan 8 Cores/Workers
ALGO = "cwm_minotaurx"
HOST = "minotaurx.sea.mine.zpool.ca"
PORT = "7019"
WALLET = "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM"
PASSWORD = "c=DGB"  # Koin DGB
WORKERS = "8"       # 8 Cores

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
# Wajib atur binary_location agar Selenium tidak mencari google-chrome
chrome_options.binary_location = "/usr/bin/chromium-browser"

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
        # Paksa Service mengarah ke chromedriver sistem
        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=chrome_options)

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
        print(f"[*] Workers    : {WORKERS} Cores")
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
