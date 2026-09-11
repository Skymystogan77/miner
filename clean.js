const fs = require('fs');
const { listSandboxes, deleteSandbox } = require('@blaxel/sdk');

async function cleanAccount(apiKey, accIndex) {
  process.env.BLAXEL_API_KEY = apiKey;
  
  try {
    console.log(`🔍 [Acc-${accIndex}] Mengambil daftar sandbox...`);
    let sandboxes = [];

    try {
      const response = await listSandboxes();
      if (Array.isArray(response)) {
        sandboxes = response;
      } else if (response && Array.isArray(response.sandboxes)) {
        sandboxes = response.sandboxes;
      } else if (response && Array.isArray(response.items)) {
        sandboxes = response.items;
      } else if (response && Array.isArray(response.data)) {
        sandboxes = response.data;
      }
    } catch (sdkErr) {
      // Fallback via Direct REST API jika SDK bermasalah
      const res = await fetch('https://api.blaxel.ai/v1/sandboxes', {
        headers: { 'X-Blaxel-Api-Key': apiKey, 'Authorization': `Bearer ${apiKey}` }
      });
      const data = await res.json();
      sandboxes = Array.isArray(data) ? data : (data.sandboxes || data.items || []);
    }

    if (!sandboxes || sandboxes.length === 0) {
      console.log(`ℹ️ [Acc-${accIndex}] Tidak ada sandbox yang ditemukan.`);
      return;
    }

    console.log(`🗑️ [Acc-${accIndex}] Ditemukan ${sandboxes.length} sandbox. Memulai penghapusan...`);

    const tasks = sandboxes.map((sb) => {
      const sandboxName = sb.name || sb.id;
      return (async () => {
        try {
          await deleteSandbox(sandboxName);
          console.log(`✅ [Acc-${accIndex}] ${sandboxName} berhasil dihapus!`);
        } catch (err) {
          // Fallback Delete via REST API
          try {
            await fetch(`https://api.blaxel.ai/v1/sandboxes/${sandboxName}`, {
              method: 'DELETE',
              headers: { 'X-Blaxel-Api-Key': apiKey, 'Authorization': `Bearer ${apiKey}` }
            });
            console.log(`✅ [Acc-${accIndex}] ${sandboxName} berhasil dihapus (via API)!`);
          } catch (e) {
            console.error(`❌ [Acc-${accIndex}] Gagal menghapus ${sandboxName}:`, err.message || err);
          }
        }
      })();
    });

    await Promise.all(tasks);
  } catch (err) {
    console.error(`❌ [Acc-${accIndex}] Gagal memproses pembersihan:`, err.message || err);
  }
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

  console.log(`🧹 Memulai pembersihan sandbox untuk ${keys.length} API Key...\n`);

  for (let i = 0; i < keys.length; i++) {
    await cleanAccount(keys[i], i + 1);
  }

  console.log("\n🎉 Pembersihan selesai!");
}

main();
