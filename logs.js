const { getSandbox, listSandboxes } = require('@blaxel/sdk');
const fs = require('fs');

async function debugSandboxes() {
  const keys = fs.readFileSync('list.txt', 'utf-8')
    .split('\n')
    .map(k => k.trim())
    .filter(k => k && !k.startsWith('#'));

  if (keys.length === 0) return;

  process.env.BLAXEL_API_KEY = keys[0]; // Pakai key pertama

  try {
    const list = await listSandboxes();
    if (!list || list.length === 0) {
      console.log("❌ Tidak ada sandbox yang terdeteksi running!");
      return;
    }

    const targetSandbox = list[0].name || list[0].id;
    console.log(`🔍 Mengambil detail & status dari sandbox: ${targetSandbox}`);

    const details = await getSandbox(targetSandbox);
    console.log("\n--- Detail Sandbox ---");
    console.log(JSON.stringify(details, null, 2));

  } catch (err) {
    console.error("❌ Error saat fetching log/sandbox:", err.message || err);
  }
}

debugSandboxes();
