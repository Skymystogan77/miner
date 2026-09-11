const fs = require('fs');
const { listSandboxes, deleteSandbox } = require('@blaxel/sdk');

async function cleanAccount(apiKey, accIndex) {
  process.env.BLAXEL_API_KEY = apiKey;
  
  try {
    console.log(`🔍 [Acc-${accIndex}] Mengambil daftar sandbox...`);
    const sandboxes = await listSandboxes();

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
          console.error(`❌ [Acc-${accIndex}] Gagal menghapus ${sandboxName}:`, err.message || err);
        }
      })();
    });

    await Promise.all(tasks);
  } catch (err) {
    console.error(`❌ [Acc-${accIndex}] Gagal mengambil daftar sandbox:`, err.message || err);
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

  console.log("\n🎉 Pembersihan selesai! Semua container telah dihapus.");
}

main();
