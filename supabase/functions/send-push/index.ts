// send-push - Supabase Edge Function: sends a phone notification to every phone that turned notifications on in the
// AmperEC app. A Database Webhook calls it when a row is added to power_save_events or alert_events.
// Secrets (Edge Functions -> Secrets): VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT (mailto:you@example.com).
// SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are given to every Edge Function by Supabase.
import webpush from "npm:web-push@3.6.7";
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const TEXT: Record<string, (r: any) => { title: string; body: string } | null> = {
  over_limit: (r) => ({ title: "Current above the limit", body: `${num(r.current_a)} A (limit ${num(r.threshold_a)} A) on ${r.device_id}` }),
  warning: (r) => ({ title: "Power save: lamp 2 will switch off", body: `${num(r.current_a)} A for too long. Open AmperEC to cancel.` }),
  turned_off: () => ({ title: "Power save switched lamp 2 off", body: "Open AmperEC to turn it back on." }),
  would_turn_off: () => ({ title: "Power save (test)", body: "Lamp 2 would switch off now (no relay connected yet)." }),
  restored: () => ({ title: "Lamp 2 is back on", body: "Power save turned it on again." }),
  cancelled: () => null, // no notification
};

function num(x: unknown) {
  return typeof x === "number" ? x.toFixed(2) : "?";
}

Deno.serve(async (req) => {
  let payload: any;
  try { payload = await req.json(); } catch { return new Response("bad json", { status: 400 }); }
  // new projects give secret keys in SUPABASE_SECRET_KEYS (JSON); older ones in SUPABASE_SERVICE_ROLE_KEY
  const secret = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") || (Object.values(JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}"))[0] as string);
  const db = createClient(Deno.env.get("SUPABASE_URL")!, secret);
  // safety: only real, recent rows trigger a notification (the text comes from the database, not from the caller)
  const table = payload?.table, id = payload?.record?.id;
  if (!["power_save_events", "alert_events"].includes(table) || !id) return new Response("ignored", { status: 400 });
  const { data: row } = await db.from(table).select("*").eq("id", id).maybeSingle();
  if (!row || Date.now() - new Date(row.created_at).getTime() > 120000) return new Response("not found", { status: 404 });
  const r = row;
  const make = TEXT[r.event];
  const msg = make ? make(r) : null;
  if (!msg) return new Response(JSON.stringify({ sent: 0, reason: "no notification for this event" }));

  webpush.setVapidDetails(Deno.env.get("VAPID_SUBJECT")!, Deno.env.get("VAPID_PUBLIC_KEY")!, Deno.env.get("VAPID_PRIVATE_KEY")!);
  const { data: subs, error } = await db.from("push_subscriptions").select("endpoint,p256dh,auth");
  if (error) return new Response(JSON.stringify({ error: error.message }), { status: 500 });

  const body = JSON.stringify({ ...msg, tag: r.event });
  let sent = 0;
  const gone: string[] = [];
  await Promise.all((subs ?? []).map(async (s) => {
    try {
      await webpush.sendNotification({ endpoint: s.endpoint, keys: { p256dh: s.p256dh, auth: s.auth } }, body, { TTL: 600 });
      sent++;
    } catch (e: any) {
      if (e?.statusCode === 404 || e?.statusCode === 410) gone.push(s.endpoint); // phone removed the app / turned it off
    }
  }));
  if (gone.length) await db.from("push_subscriptions").delete().in("endpoint", gone);
  return new Response(JSON.stringify({ sent, removed: gone.length }), { headers: { "Content-Type": "application/json" } });
});
