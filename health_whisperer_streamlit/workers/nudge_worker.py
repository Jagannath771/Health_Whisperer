import os, time, hashlib, logging, re, asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import create_client
from telegram import Bot
from telegram.constants import ParseMode

load_dotenv()

SUPABASE_URL   = os.getenv("SUPABASE_URL")
SERVICE_KEY    = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_SERVICE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not (SUPABASE_URL and SERVICE_KEY and TELEGRAM_TOKEN):
    raise RuntimeError("Missing SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, or TELEGRAM_TOKEN")

sb  = create_client(SUPABASE_URL, SERVICE_KEY)
bot = Bot(token=TELEGRAM_TOKEN)

log = logging.getLogger("nudge_worker")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# ---------------- time helpers ----------------
def now_utc() -> datetime: return datetime.now(timezone.utc)
def user_tz(uid: str) -> ZoneInfo:
    try:
        r = sb.table("hw_preferences").select("tz").eq("uid", uid).maybe_single().execute()
        tz = (r.data or {}).get("tz") or "America/New_York"
    except Exception: tz = "America/New_York"
    try:    return ZoneInfo(tz)
    except: return ZoneInfo("America/New_York")
def today_bounds_local(uid: str) -> Tuple[datetime, datetime]:
    tz = user_tz(uid)
    now_l = now_utc().astimezone(tz)
    start_l = datetime(now_l.year, now_l.month, now_l.day, tzinfo=tz)
    return start_l, now_l
def to_utc(dt: datetime) -> datetime: return dt.astimezone(timezone.utc)

# ---------------- data helpers ----------------
def prefs(uid: str) -> dict:
    try:
        r = sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single().execute()
        return r.data or {}
    except Exception:
        return {}

def latest_metrics_today(uid: str) -> dict:
    start_l, now_l = today_bounds_local(uid)
    r = (sb.table("hw_metrics").select("*")
         .eq("uid", uid)
         .gte("ts", to_utc(start_l).isoformat())
         .lt("ts",  to_utc(now_l).isoformat())
         .order("ts", desc=True).limit(1).execute())
    return (r.data[0] if r.data else {}) or {}

def meals_between(uid: str, start_u_iso: str, end_u_iso: str) -> List[dict]:
    r = (sb.table("hw_meals").select("*")
         .eq("uid", uid)
         .gte("ts", start_u_iso)
         .lt("ts",  end_u_iso)
         .order("ts", desc=True).execute())
    return r.data or []

def meals_today(uid: str) -> List[dict]:
    start_l, now_l = today_bounds_local(uid)
    return meals_between(uid, to_utc(start_l).isoformat(), to_utc(now_l).isoformat())

# ---------------- 7-day pace profile ----------------
def median(vals: List[float]) -> float:
    s = sorted(vals); n = len(s)
    return (s[n//2] if n % 2 else 0.5*(s[n//2 - 1] + s[n//2])) if n else 12.0

def rolling_7d_profile(uid: str) -> List[Tuple[float, float]]:
    tz = user_tz(uid)
    now_l = now_utc().astimezone(tz)
    meals = meals_between(uid, to_utc(now_l - timedelta(days=7)).isoformat(), to_utc(now_l).isoformat())
    if not meals: return [(10.5, 0.25), (14.0, 0.60), (17.0, 0.70), (20.0, 0.95)]
    buckets = {"breakfast":[], "lunch":[], "snacks":[], "dinner":[]}
    kcal_by = {"breakfast":0,"lunch":0,"snacks":0,"dinner":0}; total = 0
    for m in meals:
        mt = (m.get("meal_type") or "snacks").lower()
        if mt not in buckets: mt = "snacks"
        kcal = int(m.get("calories") or 0); total += kcal; kcal_by[mt] += kcal
        t = datetime.fromisoformat(m["ts"].replace("Z","+00:00")).astimezone(tz)
        buckets[mt].append(t.hour + t.minute/60.0)
    def frac(k): return (kcal_by[k] / max(1, total))
    points = sorted([
        (median(buckets["breakfast"]) if buckets["breakfast"] else 9.5,  frac("breakfast")),
        (median(buckets["lunch"])     if buckets["lunch"]     else 13.0, frac("lunch")),
        (median(buckets["snacks"])    if buckets["snacks"]    else 16.0, frac("snacks")),
        (median(buckets["dinner"])    if buckets["dinner"]    else 19.5, frac("dinner")),
    ], key=lambda x: x[0])
    anchors, cum = [], 0.0
    for h, fr in points:
        cum = min(1.0, cum + fr); anchors.append((h, cum))
    return anchors

def expected_fraction(now_l: datetime, anchors: List[Tuple[float, float]]) -> float:
    h = now_l.hour + now_l.minute/60.0
    frac = 0.0
    for ah, cum in sorted(anchors):
        if h >= ah: frac = cum
        else: break
    return min(1.0, max(0.0, frac))

def ate_recently(meals: List[dict], minutes=75) -> bool:
    if not meals: return False
    last = datetime.fromisoformat(meals[0]["ts"].replace("Z","+00:00"))
    return (now_utc() - last) < timedelta(minutes=minutes)

# ---------------- baseline nudge builder ----------------
def build_nudges(uid: str) -> List[Dict]:
    pf = prefs(uid)
    tz = user_tz(uid)
    now_l = now_utc().astimezone(tz)
    start_l, _ = today_bounds_local(uid)
    meals = meals_between(uid, to_utc(start_l).isoformat(), to_utc(now_l).isoformat())
    latest = latest_metrics_today(uid)
    anchors = rolling_7d_profile(uid)

    kcal_goal  = int(pf.get("daily_calorie_goal") or 2000)
    steps_goal = int(pf.get("daily_step_goal")   or 8000)
    water_goal = int(pf.get("daily_water_ml")    or 2000)
    sleep_goal = int(pf.get("sleep_goal_min")    or 420)

    nudges: List[Dict] = []

    # Calories pace
    exp_frac = expected_fraction(now_l, anchors)
    exp_kcal = int(kcal_goal * exp_frac)
    actual_kcal = sum(int(m.get("calories") or 0) for m in meals)
    if (actual_kcal + 50 < exp_kcal) and (not ate_recently(meals)) and now_l.hour >= 11:
        nudges.append({"icon":"🍽️","title":"Fuel up (pace)","msg":f"~{exp_kcal - actual_kcal} kcal behind your usual pace. Try a protein-rich mini-meal."})

    # Steps pace
    day_frac = (now_l.hour + now_l.minute/60.0)/24.0
    exp_steps = int(steps_goal * max(0.0, min(1.0, day_frac*1.05)))
    steps = int(latest.get("steps") or 0)
    if steps + 400 < exp_steps and now_l.hour >= 10:
        nudges.append({"icon":"🚶","title":"Move a little","msg":f"{exp_steps - steps} steps to stay on pace. 10–15 min brisk walk should do it."})

    # Hydration blocks 9–19
    if 9 <= now_l.hour <= 19:
        blocks = ((now_l.hour - 9)*60 + now_l.minute)//90
        exp_water = int(min(10, max(0, blocks)) * (water_goal/10))
    else:
        exp_water = 0
    water = int(latest.get("water_ml") or 0)
    if water + 150 < exp_water:
        nudges.append({"icon":"💧","title":"Hydrate","msg":f"{exp_water - water} ml to stay on track. Sip a glass now."})

    # Recovery/safety
    sleep = int(latest.get("sleep_minutes") or 0)
    if sleep and sleep < sleep_goal:
        nudges.append({"icon":"😴","title":"Earlier wind-down","msg":"Sleep was light. Try a 30-min earlier wind-down tonight."})
    mood = int(latest.get("mood") or 0)
    if mood and mood <= 2:
        nudges.append({"icon":"🌤️","title":"Mental reset","msg":"Low mood—2-minute box breathing or a 5-minute walk can help."})
    hr = int(latest.get("heart_rate") or 0)
    if hr and (hr < 45 or hr > 110):
        nudges.append({"icon":"❤️","title":"Heart rate check","msg":f"Resting HR ~{hr} bpm looks unusual. If unwell, check in."})

    # Proactive mental-health touches
    if not latest.get("mood") and 12 <= now_l.hour <= 16:
        nudges.append({"icon":"🧠","title":"Mood check-in","msg":"How are you feeling (1–5)? A 2-min pause can reset your afternoon."})
    if not nudges and 10 <= now_l.hour <= 18:
        nudges.append({"icon":"🌬️","title":"60-second breathing","msg":"Inhale 4, hold 4, exhale 4, hold 4 — 8 cycles to reset."})

    return nudges[:3] if nudges else [{"icon":"✨","title":"On track","msg":"Nice work! Keep the streak going."}]

def nudges_hash(nudges: List[Dict]) -> str:
    blob = "".join(f"{n['title']}|{n['msg']}|{n.get('icon','')};" for n in nudges)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

# ---------------- event-driven “immediate” rules ----------------
BAD_SNACK_RE = re.compile(r"(milk\s*shake|soda|soft\s*drink|fries|candy|dessert|ice\s*cream|cookie|cake|donut|pastry)", re.I)

def bad_decision_nudges(uid: str, event: dict) -> List[Dict]:
    tz = user_tz(uid)
    now_l = now_utc().astimezone(tz)
    nudges: List[Dict] = []

    if event["kind"] == "meal_logged":
        e = event.get("payload") or {}
        meal_type = (e.get("meal_type") or "snacks").lower()
        cal = int(e.get("calories") or 0)
        protein = int(e.get("protein_g") or 0)
        sugar = int(e.get("sugar_g") or 0)
        raw = (e.get("raw_text") or "")

        if now_l.hour >= 22:
            nudges.append({"icon":"🌙","title":"Late-night bite?",
                           "msg":"If this is a bedtime snack, keep it light & protein-forward to protect sleep."})
        if meal_type in ("snacks","unknown") and ((sugar >= 15 and protein < 8) or BAD_SNACK_RE.search(raw)):
            nudges.append({"icon":"⚖️","title":"Balance that snack",
                           "msg":"Add a protein/fiber buffer (nuts, yogurt) to keep energy steady."})
        if meal_type == "breakfast" and protein < 15:
            nudges.append({"icon":"🍳","title":"Protein at breakfast",
                           "msg":"Consider eggs/greek yogurt/tofu — it improves satiety for the day."})
        if meal_type == "dinner" and now_l.hour >= 20 and cal >= 800:
            nudges.append({"icon":"🕗","title":"Heavy late dinner",
                           "msg":"Go easy on portions & finish eating 2–3h before bed to aid sleep."})

    elif event["kind"] == "metrics_saved":
        e = event.get("payload") or {}
        water = int(e.get("water_ml") or 0)
        hr = int(e.get("heart_rate") or 0)
        temp = float(e.get("body_temp") or 0.0)

        base = build_nudges(uid)

        if 8 <= now_l.hour <= 19 and 45 <= hr <= 54:
            nudges.append({"icon":"🧍","title":"Loosen up",
                           "msg":"HR looks low; a brief stretch or light walk can help circulation."})
        if 99.3 <= temp < 100.0:
            nudges.append({"icon":"🌡️","title":"Easy does it",
                           "msg":"Slightly warm. Hydrate and keep activity light for a bit."})
        if 12 <= now_l.hour <= 18 and water < int(prefs(uid).get("daily_water_ml") or 2000) * 0.5:
            nudges.append({"icon":"💧","title":"Hydrate now",
                           "msg":"Halfway through the day — a full glass keeps you on track."})

        titles = {n["title"] for n in nudges}
        for n in base:
            if n["title"] not in titles:
                nudges.append(n); titles.add(n["title"])

    return nudges[:3] if nudges else []

# ---------------- event queue processing ----------------
def fetch_pending_events(limit=50) -> list[dict]:
    rows = (sb.table("hw_events")
            .select("*")
            .eq("processed", False)
            .order("ts", desc=False)
            .limit(limit)
            .execute().data) or []
    return rows

def mark_processed(event_ids: List[int]):
    if not event_ids: return
    sb.table("hw_events").update({"processed": True}).in_("id", event_ids).execute()

def _resolve_chat_id(uid: str) -> Optional[int]:
    # 1) preferences
    try:
        pref = sb.table("hw_preferences").select("telegram_chat_id").eq("uid", uid).maybe_single().execute().data or {}
        if pref.get("telegram_chat_id"):
            return int(pref["telegram_chat_id"])
    except Exception:
        pass
    # 2) fallback to tg_links
    try:
        link = sb.table("tg_links").select("telegram_id").eq("user_id", uid).maybe_single().execute().data or {}
        if link.get("telegram_id"):
            try:
                sb.table("hw_preferences").upsert({"uid": uid, "telegram_chat_id": link["telegram_id"]}).execute()
            except Exception:
                pass
            return int(link["telegram_id"])
    except Exception:
        pass
    return None

def deliver(uid: str, text: str) -> bool:
    chat_id = _resolve_chat_id(uid)
    if not chat_id:
        log.info(f"[deliver] skip: no chat_id for uid={uid}")
        return False
    log.info(f"[deliver] chat_id={chat_id} uid={uid}")
    try:
        async def _send():
            await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
        asyncio.run(_send())  # PTB v20+: send_message is async
        log.info(f"[deliver] sent to uid={uid}")
        return True
    except Exception as e:
        log.warning(f"[deliver] failed for uid={uid}: {e}")
        return False

def process_event(ev: dict):
    uid = ev["uid"]
    nudges = bad_decision_nudges(uid, ev)
    if not nudges:
        return
    blob = "".join(f"{n['title']}|{n['msg']}|{n.get('icon','')};" for n in nudges)
    h = hashlib.sha256(blob.encode("utf-8")).hexdigest()
    two_hours_ago = (now_utc() - timedelta(hours=2)).isoformat()
    prev = (sb.table("hw_nudges_log").select("id")
            .eq("uid", uid).eq("hash", h).gte("ts", two_hours_ago)
            .limit(1).execute().data)
    if prev:
        log.info(f"[events] dedup for uid={uid}")
        return

    text = "*Heads-up*\n\n" + "\n\n".join(f"{n['icon']} *{n['title']}*\n_{n['msg']}_" for n in nudges)
    if deliver(uid, text):
        sb.table("hw_nudges_log").insert({
            "uid": uid, "channel": "telegram",
            "payload": {"nudges": nudges}, "hash": h
        }).execute()
        log.info(f"[events] sent {len(nudges)} nudges to uid={uid}")

def tick_events():
    evs = fetch_pending_events(limit=50)
    if not evs: return
    done_ids = []
    for ev in evs:
        try:
            process_event(ev)
            done_ids.append(ev["id"])
        except Exception as e:
            log.exception(f"process_event failed: {e}")
    mark_processed(done_ids)

# ---------------- periodic baseline tick ----------------
def _candidate_user_ids() -> List[str]:
    uids = set()
    try:
        rows = (sb.table("hw_preferences").select("uid, telegram_chat_id")
                .not_.is_("telegram_chat_id", None).execute().data) or []
        for r in rows: uids.add(r["uid"])
    except Exception:
        pass
    try:
        rows = (sb.table("tg_links").select("user_id, telegram_id")
                .not_.is_("telegram_id", None).execute().data) or []
        for r in rows: uids.add(r["user_id"])
    except Exception:
        pass
    return list(uids)

def tick_periodic():
    for uid in _candidate_user_ids():
        nudges = build_nudges(uid)
        if not nudges:
            continue
        blob = "".join(f"{n['title']}|{n['msg']}|{n.get('icon','')};" for n in nudges)
        h = hashlib.sha256(blob.encode("utf-8")).hexdigest()
        two_hours_ago = (now_utc() - timedelta(hours=2)).isoformat()
        prev = (sb.table("hw_nudges_log").select("id")
                .eq("uid", uid).eq("hash", h).gte("ts", two_hours_ago)
                .limit(1).execute().data)
        if prev:
            log.info(f"[periodic] dedup for uid={uid}")
            continue
        text = "*Your smart nudges*\n\n" + "\n\n".join(f"{n['icon']} *{n['title']}*\n_{n['msg']}_" for n in nudges)
        if deliver(uid, text):
            sb.table("hw_nudges_log").insert({
                "uid": uid, "channel": "telegram",
                "payload": {"nudges": nudges}, "hash": h
            }).execute()
            log.info(f"[periodic] sent {len(nudges)} nudges to uid={uid}")

def main():
    last_periodic = 0
    log.info("Nudge worker started…")
    try:
        while True:
            tick_events()                 # react to user actions quickly
            now = time.time()
            if now - last_periodic > 600: # every 10 min, send pacing nudges
                tick_periodic()
                last_periodic = now
            time.sleep(60)                # poll each minute
    except KeyboardInterrupt:
        log.info("Nudge worker shutting down…")

if __name__ == "__main__":
    main()
