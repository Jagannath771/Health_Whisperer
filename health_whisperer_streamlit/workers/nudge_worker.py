# workers/nudge_worker.py
import os, time, hashlib, logging, asyncio, json
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Tuple, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from supabase import create_client
from telegram import Bot
from telegram.constants import ParseMode

from icalendar import Calendar
import httpx

# =================== Env & clients ===================
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

# ------------------ Tunables (feel free to tweak) ------------------
STEP_BUCKET = 500            # de-dup bucket size for steps deficit
KCAL_BUCKET = 100            # de-dup bucket size for kcal deficit
WATER_BUCKET = 100           # de-dup bucket size for water ml deficit
COOLDOWN_MIN_PER_TYPE = 5    # minimum minutes between SAME TYPE nudges
RUN_EVERY_SECONDS = 60       # main loop frequency

# =================== Time helpers ===================
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def user_tz(uid: str) -> ZoneInfo:
    try:
        r = sb.table("hw_preferences").select("tz").eq("uid", uid).maybe_single().execute()
        tz = (r.data or {}).get("tz") or "America/New_York"
    except Exception:
        tz = "America/New_York"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("America/New_York")

def today_bounds_local(uid: str) -> Tuple[datetime, datetime]:
    tz = user_tz(uid)
    now_l = now_utc().astimezone(tz)
    start_l = datetime(now_l.year, now_l.month, now_l.day, tzinfo=tz)
    return start_l, now_l

def to_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)

# =================== Data helpers ===================
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

# =================== 7-day pace profile ===================
def median(vals: List[float]) -> float:
    s = sorted(vals); n = len(s)
    return (s[n//2] if n % 2 else 0.5*(s[n//2 - 1] + s[n//2])) if n else 12.0

def rolling_7d_profile(uid: str) -> List[Tuple[float, float]]:
    tz = user_tz(uid)
    now_l = now_utc().astimezone(tz)
    meals = meals_between(uid, to_utc(now_l - timedelta(days=7)).isoformat(), to_utc(now_l).isoformat())
    if not meals:
        return [(10.5, 0.25), (13.5, 0.60), (17.0, 0.75), (20.0, 0.95)]

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

# =================== Quiet hours & calendar suppression ===================
def is_quiet_hours(uid: str, now_l: datetime, pf: dict) -> bool:
    qs = (pf.get("quiet_start") or "22:00").strip()
    qe = (pf.get("quiet_end") or "07:00").strip()
    try:
        qh, qm = map(int, qs.split(":"))
        eh, em = map(int, qe.split(":"))
    except Exception:
        qh,qm,eh,em = 22,0,7,0
    start = now_l.replace(hour=qh, minute=qm, second=0, microsecond=0)
    end   = now_l.replace(hour=eh, minute=em, second=0, microsecond=0)
    if start <= end:
        return start <= now_l <= end
    else:
        return not (end < now_l < start)

async def busy_by_calendar(pf: dict, now_l: datetime) -> bool:
    ics_url = (pf.get("calendar_ics_url") or "").strip()
    if not ics_url:
        return False
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(ics_url)
            r.raise_for_status()
        cal = Calendar.from_ical(r.content)
        now_u = now_l.astimezone(timezone.utc)
        for comp in cal.walk('vevent'):
            dtstart = comp.get('dtstart').dt
            dtend   = comp.get('dtend').dt
            if hasattr(dtstart, "tzinfo"):
                s = dtstart.astimezone(timezone.utc)
                e = dtend.astimezone(timezone.utc)
            else:
                s = datetime(dtstart.year, dtstart.month, dtstart.day, tzinfo=timezone.utc)
                e = datetime(dtend.year, dtend.month, dtend.day, tzinfo=timezone.utc)
            if s <= now_u <= e:
                return True
        return False
    except Exception:
        return False

# =================== Nudges ===================
def _bucket(val: int, size: int) -> int:
    # e.g., 1379 with size=500 -> 1500
    if val <= 0: return 0
    return int(round(val / size) * size)

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

    # Calories pacing (message uses exact, hash uses bucket)
    exp_frac = expected_fraction(now_l, anchors)
    exp_kcal = int(kcal_goal * exp_frac)
    actual_kcal = sum(int(m.get("calories") or 0) for m in meals)
    kcal_def = max(0, exp_kcal - actual_kcal)
    if (kcal_def >= 150) and (not ate_recently(meals)) and now_l.hour >= 11:
        nudges.append({
            "type": "kcal_pace",
            "icon": "🍽️",
            "title": "Fuel up (pace)",
            "msg": f"~{kcal_def} kcal behind your usual pace. Try a protein mini-meal.",
            "hash_key": f"kcal_pace|{_bucket(kcal_def, KCAL_BUCKET)}"
        })

    # Steps pacing
    day_frac = (now_l.hour + now_l.minute/60.0)/24.0
    exp_steps = int(steps_goal * max(0.0, min(1.0, day_frac*1.05)))
    steps = int(latest.get("steps") or 0)
    step_def = max(0, exp_steps - steps)
    if (step_def >= 500) and now_l.hour >= 10:
        nudges.append({
            "type": "steps_pace",
            "icon": "🚶",
            "title": "Move a little",
            "msg": f"{step_def} steps to stay on pace. 10–15 min brisk walk.",
            "hash_key": f"steps_pace|{_bucket(step_def, STEP_BUCKET)}"
        })

    # Hydration blocks (9–19)
    if 9 <= now_l.hour <= 19:
        blocks = ((now_l.hour - 9)*60 + now_l.minute)//90
        exp_water = int(min(10, max(0, blocks)) * (water_goal/10))
    else:
        exp_water = 0
    water = int(latest.get("water_ml") or 0)
    water_def = max(0, exp_water - water)
    if water_def >= 150:
        nudges.append({
            "type": "water_pace",
            "icon": "💧",
            "title": "Hydrate",
            "msg": f"{water_def} ml to stay on track. Sip a glass now.",
            "hash_key": f"water_pace|{_bucket(water_def, WATER_BUCKET)}"
        })

    # Recovery/safety
    sleep = int(latest.get("sleep_minutes") or 0)
    if sleep and sleep < sleep_goal:
        nudges.append({
            "type": "sleep_recovery",
            "icon": "😴",
            "title": "Earlier wind-down",
            "msg": "Sleep was light. Try a 30-min earlier wind-down tonight.",
            "hash_key": "sleep_recovery|1"
        })
    mood = int(latest.get("mood") or 0)
    if mood and mood <= 2:
        nudges.append({
            "type": "mood_reset",
            "icon": "🌤️",
            "title": "Mental reset",
            "msg": "Low mood — 2-min box breathing or a 5-min walk can help.",
            "hash_key": "mood_reset|1"
        })

    # Default gentle touch (midday only)
    if not nudges and 10 <= now_l.hour <= 18:
        nudges.append({
            "type": "breathing",
            "icon": "🌬️",
            "title": "60-second breathing",
            "msg": "Inhale 4, hold 4, exhale 4, hold 4 — 8 cycles.",
            "hash_key": "breathing|1"
        })

    return nudges[:3] if nudges else [{
        "type": "on_track", "icon":"✨","title":"On track","msg":"Nice work! Keep the streak going.",
        "hash_key":"on_track|1"
    }]

def nudges_hash(nudges: List[Dict]) -> str:
    blob = "".join(f"{n['hash_key']};" for n in nudges)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

# =================== Event-reactive rules ===================
def react_to_event(uid: str, event: dict) -> List[Dict]:
    kind = event.get("kind")
    if kind == "metrics_saved":
        e = event.get("payload") or {}
        if int(e.get("water_ml") or 0) == 0 and 10 <= now_utc().astimezone(user_tz(uid)).hour <= 16:
            return [{
                "type": "water_first",
                "icon":"💧","title":"First water of the day?",
                "msg":"A quick glass now helps energy & focus.",
                "hash_key": "water_first|1"
            }]
    if kind == "meal_logged":
        e = event.get("payload") or {}
        mt = (e.get("meal_type") or "snacks").lower()
        if mt == "dinner" and int(e.get("calories") or 0) >= 800:
            return [{
                "type": "heavy_dinner",
                "icon":"🕗","title":"Heavy dinner",
                "msg":"Easy on portions & finish 2–3h before bed to aid sleep.",
                "hash_key": "heavy_dinner|1"
            }]
    return []

# =================== Dispatch & logging ===================
def insert_nudge_log(uid: str, payload: dict, h: str, channel: str):
    try:
        sb.table("hw_nudges_log").insert({
            "uid": uid,
            "channel": channel,
            "payload": payload,   # JSON (includes 'type')
            "hash": h
        }).execute()
    except Exception as e:
        log.info("nudge log failed (non-fatal): %s", e)

async def send_telegram(uid: str, text: str, pf: dict, payload: dict, h: str):
    chat_id = pf.get("telegram_chat_id")
    if not chat_id:
        u = sb.table("hw_users").select("tg_chat_id").eq("uid", uid).maybe_single().execute().data or {}
        chat_id = u.get("tg_chat_id")
    if not chat_id:
        return
    await bot.send_message(chat_id=chat_id, text=text, parse_mode=ParseMode.MARKDOWN)
    insert_nudge_log(uid, payload, h, channel="telegram")

def sent_same_type_recently(uid: str, nudge_type: str, minutes: int) -> bool:
    """Check hw_nudges_log for recent sends of the same type."""
    try:
        since = (now_utc() - timedelta(minutes=minutes)).isoformat()
        r = (sb.table("hw_nudges_log").select("ts,payload")
             .eq("uid", uid).eq("channel", "telegram")
             .gte("ts", since).order("ts", desc=True).limit(20).execute())
        for row in r.data or []:
            p = row.get("payload") or {}
            if isinstance(p, str):
                try: p = json.loads(p)
                except Exception: p = {}
            if p.get("type") == nudge_type:
                return True
    except Exception as e:
        log.info("recent-check failed (non-fatal): %s", e)
    return False

# =================== Periodic user processor ===================
async def process_user(uid: str):
    pf = prefs(uid)
    tz = user_tz(uid)
    now_l = now_utc().astimezone(tz)

    # Suppression
    if is_quiet_hours(uid, now_l, pf): return
    if await busy_by_calendar(pf, now_l): return

    nudges = build_nudges(uid)
    h = nudges_hash(nudges)

    # De-dup whole stack (content-bucket level)
    last_h = pf.get("last_nudge_hash")
    if last_h == h:
        return

    # Deliver the top nudge only, but respect short per-type cooldown
    n = nudges[0]
    if sent_same_type_recently(uid, n.get("type",""), COOLDOWN_MIN_PER_TYPE):
        return

    if (pf.get("nudge_channel") or "telegram") == "inapp":
        insert_nudge_log(uid, n, h, channel="inapp")
    else:
        text = f"{n.get('icon','✨')} *{n['title']}*\n{n['msg']}"
        await send_telegram(uid, text, pf, n, h)

    # Remember last hash for cheap de-dup
    sb.table("hw_preferences").update({"last_nudge_hash": h}).eq("uid", uid).execute()

# =================== Reactive event processor ===================
async def process_events():
    r = sb.table("hw_events").select("*").eq("processed", False).order("ts").limit(50).execute()
    for ev in r.data or []:
        uid = ev["uid"]
        pf = prefs(uid)
        tz = user_tz(uid)
        now_l = now_utc().astimezone(tz)

        if is_quiet_hours(uid, now_l, pf):
            sb.table("hw_events").update({"processed": True}).eq("id", ev["id"]).execute()
            continue
        if await busy_by_calendar(pf, now_l):
            sb.table("hw_events").update({"processed": True}).eq("id", ev["id"]).execute()
            continue

        msgs = react_to_event(uid, ev)
        for m in msgs:
            if sent_same_type_recently(uid, m.get("type",""), COOLDOWN_MIN_PER_TYPE):
                continue
            if (pf.get("nudge_channel") or "telegram") == "inapp":
                insert_nudge_log(uid, m, nudges_hash([m]), channel="inapp")
            else:
                await send_telegram(uid, f"{m.get('icon','✨')} *{m['title']}*\n{m['msg']}", pf, m, nudges_hash([m]))
        sb.table("hw_events").update({"processed": True}).eq("id", ev["id"]).execute()

# =================== Main loop ===================
async def main_loop():
    while True:
        # Who gets nudged? everyone with a prefs row
        r = sb.table("hw_preferences").select("uid").not_.is_("uid", None).limit(1000).execute()
        uids = [x["uid"] for x in (r.data or [])]

        await process_events()

        for uid in uids:
            try:
                await process_user(uid)
            except Exception as e:
                log.exception("process_user(%s) failed: %s", uid, e)

        await asyncio.sleep(RUN_EVERY_SECONDS)

if __name__ == "__main__":
    asyncio.run(main_loop())
