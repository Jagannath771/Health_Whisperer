# services/nudges.py
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib

# ---------- Utilities ----------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _user_tz(sb, uid: str) -> ZoneInfo:
    try:
        r = sb.table("hw_preferences").select("tz").eq("uid", uid).maybe_single().execute()
        tz = (r.data or {}).get("tz") or "America/New_York"
    except Exception:
        tz = "America/New_York"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("America/New_York")

def local_today_bounds(sb, uid: str) -> Tuple[datetime, datetime]:
    tz = _user_tz(sb, uid)
    now_local = _now_utc().astimezone(tz)
    start_local = datetime(now_local.year, now_local.month, now_local.day, tzinfo=tz)
    return start_local, now_local

def to_utc(dt_local: datetime) -> datetime:
    return dt_local.astimezone(timezone.utc)

def _prefs(sb, uid: str) -> dict:
    try:
        r = sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single().execute()
        return r.data or {}
    except Exception:
        return {}

def _latest_metrics_today(sb, uid: str) -> dict:
    start_local, now_local = local_today_bounds(sb, uid)
    start_utc, now_utc = to_utc(start_local), to_utc(now_local)
    r = (sb.table("hw_metrics").select("*")
         .eq("uid", uid)
         .gte("ts", start_utc.isoformat())
         .lt("ts", now_utc.isoformat())
         .order("ts", desc=True).limit(1).execute())
    return (r.data[0] if r.data else {}) or {}

def _meals_since(sb, uid: str, start_utc_iso: str, end_utc_iso: str) -> List[dict]:
    r = (sb.table("hw_meals").select("*")
         .eq("uid", uid)
         .gte("ts", start_utc_iso)
         .lt("ts", end_utc_iso)
         .order("ts", desc=True).execute())
    return r.data or []

def _meals_today(sb, uid: str) -> List[dict]:
    start_local, now_local = local_today_bounds(sb, uid)
    start_utc, now_utc = to_utc(start_local), to_utc(now_local)
    return _meals_since(sb, uid, start_utc.isoformat(), now_utc.isoformat())

# ---------- Rolling 7-day pace profile ----------
@dataclass
class PaceProfile:
    # expected calorie fraction by anchors across the day (cumulative 0..1)
    anchors: List[Tuple[float, float]]  # [(hour_f, cumulative_fraction), ...]

def _median_time(hours: List[float]) -> float:
    s = sorted(hours)
    n = len(s)
    if n == 0: return 12.0
    mid = n // 2
    return (s[mid] if n % 2 == 1 else (s[mid-1] + s[mid]) / 2.0)

def _meal_hour(ts_iso: str) -> float:
    dt = datetime.fromisoformat(ts_iso.replace("Z","+00:00"))
    return dt.hour + dt.minute / 60.0

def _rolling_7d_profile(sb, uid: str) -> PaceProfile:
    tz = _user_tz(sb, uid)
    now_local = _now_utc().astimezone(tz)
    start7_local = now_local - timedelta(days=7)
    start7_utc, now_utc = to_utc(start7_local), to_utc(now_local)
    meals = _meals_since(sb, uid, start7_utc.isoformat(), now_utc.isoformat())

    if not meals:
        # default curve if no history
        return PaceProfile(anchors=[(10.5, 0.25), (14.0, 0.60), (17.0, 0.70), (20.0, 0.95)])

    by_type = {"breakfast": [], "lunch": [], "snacks": [], "dinner": []}
    kcal_by_type = {"breakfast": 0, "lunch": 0, "snacks": 0, "dinner": 0}
    total_kcal = 0
    for m in meals:
        mt = (m.get("meal_type") or "unknown").lower()
        if mt not in by_type: mt = "snacks"  # bucket unknown/light into snacks
        kcal = int(m.get("calories") or 0)
        total_kcal += kcal
        kcal_by_type[mt] += kcal
        h = _meal_hour(m["ts"])
        # convert to local hours for anchors
        h_local = datetime.fromisoformat(m["ts"].replace("Z","+00:00")).astimezone(tz)
        by_type[mt].append(h_local.hour + h_local.minute/60.0)

    # fractions by meal type
    def frac(mt): 
        return (kcal_by_type[mt] / max(1, total_kcal))

    b = frac("breakfast")
    l = frac("lunch")
    s = frac("snacks")
    d = frac("dinner")

    # median local anchors
    hb = _median_time(by_type["breakfast"]) if by_type["breakfast"] else 9.5
    hl = _median_time(by_type["lunch"])     if by_type["lunch"] else 13.0
    hs = _median_time(by_type["snacks"])    if by_type["snacks"] else 16.0
    hd = _median_time(by_type["dinner"])    if by_type["dinner"] else 19.5

    # build cumulative curve in chronological order
    points = sorted([(hb, b), (hl, l), (hs, s), (hd, d)], key=lambda x: x[0])
    anchors = []
    cum = 0.0
    for hour, fr in points:
        cum = min(1.0, cum + fr)
        anchors.append((hour, cum))

    return PaceProfile(anchors=anchors)

def _expected_fraction(now_local: datetime, profile: PaceProfile) -> float:
    h = now_local.hour + now_local.minute/60.0
    # piecewise step function: expected fraction is last anchor passed
    frac = 0.0
    for hour, cum in sorted(profile.anchors):
        if h >= hour:
            frac = cum
        else:
            break
    return min(1.0, max(0.0, frac))

# ---------- Nudges ----------
def _cooldown_since_last_meal(meals_today: List[dict], minutes: int = 75) -> bool:
    if not meals_today: return False
    last_ts = meals_today[0]["ts"]
    last = datetime.fromisoformat(last_ts.replace("Z","+00:00"))
    return (_now_utc() - last) < timedelta(minutes=minutes)

def _digest_calories(meals: List[dict]) -> int:
    return sum(int(m.get("calories") or 0) for m in meals)

def build_nudges(sb, uid: str, now: Optional[datetime] = None) -> List[Dict]:
    prefs = _prefs(sb, uid)
    tz = _user_tz(sb, uid)
    now_local = (now or _now_utc()).astimezone(tz)
    start_local, _ = local_today_bounds(sb, uid)
    start_utc = to_utc(start_local)

    meals_today = _meals_since(sb, uid, start_utc.isoformat(), to_utc(now_local).isoformat())
    latest_metrics = _latest_metrics_today(sb, uid)
    profile = _rolling_7d_profile(sb, uid)

    # Goals
    kcal_goal = int(prefs.get("daily_calorie_goal") or 2000)
    steps_goal = int(prefs.get("daily_step_goal") or 8000)
    water_goal = int(prefs.get("daily_water_ml") or 2000)
    sleep_goal = int(prefs.get("sleep_goal_min") or 420)

    nudges: List[Dict] = []

    # 1) Calories vs personalized pace
    expected_frac = _expected_fraction(now_local, profile)
    expected_cals = int(kcal_goal * expected_frac)
    actual_cals = _digest_calories(meals_today)
    recently_ate = _cooldown_since_last_meal(meals_today, minutes=75)

    if (actual_cals + 50 < expected_cals) and not recently_ate and now_local.hour >= 11:
        gap = expected_cals - actual_cals
        nudges.append({"icon":"🍽️","title":"Fuel up (pace)","msg":f"~{gap} kcal behind your usual pace. Try a protein-rich mini-meal."})

    # 2) Steps vs simple day-fraction pace
    day_frac = (now_local.hour + now_local.minute/60.0) / 24.0
    expected_steps = int(steps_goal * max(0.0, min(1.0, day_frac * 1.05)))
    steps = int(latest_metrics.get("steps") or 0)
    if steps + 400 < expected_steps and now_local.hour >= 10:
        nudges.append({"icon":"🚶","title":"Move a little","msg":f"{expected_steps - steps} steps to stay on pace. 10–15 min brisk walk should do it."})

    # 3) Hydration in 90-min blocks 9am–7pm local
    if 9 <= now_local.hour <= 19:
        blocks = ((now_local.hour - 9)*60 + now_local.minute) // 90
        expected_water = int(min(10, max(0, blocks)) * (water_goal / 10))
    else:
        expected_water = 0
    water = int(latest_metrics.get("water_ml") or 0)
    if water + 150 < expected_water:
        nudges.append({"icon":"💧","title":"Hydrate","msg":f"{expected_water - water} ml to stay on track. Sip a glass now."})

    # 4) Recovery / sanity nudges
    sleep = int(latest_metrics.get("sleep_minutes") or 0)
    if sleep and sleep < sleep_goal:
        nudges.append({"icon":"😴","title":"Earlier wind-down","msg":"Sleep was light. Try 30-min earlier wind-down tonight."})
    mood = int(latest_metrics.get("mood") or 0)
    if mood and mood <= 2:
        nudges.append({"icon":"🌤️","title":"Mental reset","msg":"Low mood—3-minute box breathing or a 5-minute walk can help."})
    hr = int(latest_metrics.get("heart_rate") or 0)
    if hr and (hr < 45 or hr > 110):
        nudges.append({"icon":"❤️","title":"Heart rate check","msg":f"Resting HR ~{hr} bpm looks unusual for you. If unwell, check in."})
    temp = float(latest_metrics.get("body_temp") or 0.0)
    if temp and (temp < 95.0 or temp > 100.4):
        nudges.append({"icon":"🌡️","title":"Temperature note","msg":f"{temp:.1f}°F is outside typical range. Rest & hydrate."})

    # limit to 3 to reduce noise
    return nudges[:3] if nudges else [{"icon":"✨","title":"On track","msg":"Nice work! Keep the streak going."}]

# ---------- Outbox de-dup (worker) ----------
def nudge_hash(nudges: List[Dict]) -> str:
    blob = "".join(f"{n['title']}|{n['msg']};" for n in nudges)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
