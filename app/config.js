// AmperEC app settings. The key below is the Supabase PUBLISHABLE key: it is made to be public (it is inside
// every web page that uses Supabase). It can only do what the database rules allow: without signing in, only add
// sensor readings. NEVER put the secret key (sb_secret_...) here.
window.AMPEREC_CONFIG = {
  url: "https://toohsvplyulcaulxlkwu.supabase.co",
  key: "sb_publishable_GIsVOsIQgGu00tcJ-NMUgQ_sP8G6FhI",
  device: "esp32-lab-1",
  volts: 12,
  // public half of the notification keys (the private half is only in the Supabase Edge Function secrets)
  vapidPublicKey: "BMvluUfcjypHG2zkFN6w5JVKsmCrlUVAWv3szG_ZJxKdl8lDRWBnA3r-8QY8y9PfR7lV4z44TDDzLWMd9yaixFE"
};
