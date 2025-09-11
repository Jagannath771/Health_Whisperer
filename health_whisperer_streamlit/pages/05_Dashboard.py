# pages/05_Dashboard.py
import time
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from supabase import create_client
from httpx import ReadError
import matplotlib.pyplot as plt

from nav import top_nav

# ===== Page config =====
st.set_page_config(page_title="Dashboard - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>section[data-testid='stSidebarNav']{display:none;}</style>", unsafe_allow_html=True)

# ===== Retry helper =====
def exec_with_retry(req, tries: int = 3, base_delay: float = 0.4):
    for i in range(tries):
        try:
            return req.execute()
        except Exception as e:
            msg = str(e)
            if "10035" in msg or isinstance(e, ReadError):
                time.sleep(base_delay * (i + 1))
                continue
            raise
    return req.execute()

# ===== Supabase client =====
@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)
sb = get_sb()

# ===== Auth / Nav =====
def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Dashboard")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]

# ===== Time helpers =====
def _user_tz(uid: str) -> ZoneInfo:
    try:
        r = exec_with_retry(sb.table("hw_preferences").select("tz").eq("uid", uid).maybe_single())
        tz = (r.data or {}).get("tz") or "America/New_York"
    except Exception:
        tz = "America/New_York"
    try:
        return ZoneInfo(tz)
    except Exception:
        return ZoneInfo("America/New_York")

def _today(uid: str) -> date:
    return datetime.now(timezone.utc).astimezone(_user_tz(uid)).date()

def _start_end_days(uid: str, days_back: int = 14):
    tz = _user_tz(uid)
    now_l = datetime.now(timezone.utc).astimezone(tz)
    start_l = (now_l - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start_l.astimezone(timezone.utc), now_l.astimezone(timezone.utc)

def _fmt_ts(ts_iso: str | None) -> str:
    if not ts_iso: return "—"
    try:
        tz = _user_tz(uid)
        return (datetime.fromisoformat(ts_iso.replace("Z","+00:00"))
                .astimezone(tz).strftime("%b %d, %Y • %I:%M %p"))
    except Exception:
        return ts_iso

# ===== Robust parsing & numeric helpers =====
def _to_dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")

def _to_num(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")

def _safe_max(series: pd.Series | None, default: int | float = 0):
    if series is None:
        return default
    s = _to_num(series)
    if s.empty:
        return default
    m = s.max(skipna=True)
    return default if pd.isna(m) else m

def _safe_sum(series: pd.Series | None, default: int | float = 0):
    if series is None:
        return default
    s = _to_num(series).fillna(0)
    if s.empty:
        return default
    return s.sum()

# ===== Data loaders =====
def load_meals(uid: str, days_back: int = 14) -> pd.DataFrame:
    start_u, end_u = _start_end_days(uid, days_back)
    req = (sb.table("hw_meals").select("*")
           .eq("uid", uid)
           .gte("ts", start_u.isoformat())
           .lt("ts",  end_u.isoformat())
           .order("ts", desc=True))
    r = exec_with_retry(req)
    rows = r.data or []
    if not rows:
        return pd.DataFrame(columns=["ts","meal_type","calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg","items","raw_text"])
    df = pd.DataFrame(rows)
    df["ts"] = _to_dt(df["ts"])
    for col in ["calories","protein_g","carbs_g","fat_g","fiber_g","sugar_g","sodium_mg"]:
        if col in df.columns:
            df[col] = _to_num(df[col])
    return df

def load_metrics(uid: str, days_back: int = 14) -> pd.DataFrame:
    start_u, end_u = _start_end_days(uid, days_back)
    req = (sb.table("hw_metrics").select("*")
           .eq("uid", uid)
           .gte("ts", start_u.isoformat())
           .lt("ts",  end_u.isoformat())
           .order("ts", desc=True))
    r = exec_with_retry(req)
    rows = r.data or []
    if not rows:
        return pd.DataFrame(columns=["ts","source","steps","water_ml","sleep_minutes","heart_rate","mood","meal_quality","calories"])
    df = pd.DataFrame(rows)
    df["ts"] = _to_dt(df["ts"])
    for col in ["steps","water_ml","sleep_minutes","heart_rate","mood","meal_quality","calories"]:
        if col in df.columns:
            df[col] = _to_num(df[col])
    return df

def get_prefs(uid: str) -> dict:
    try:
        r = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single())
        return r.data or {}
    except Exception:
        return {}

# ===== Load everything =====
prefs = get_prefs(uid)
meals_df = load_meals(uid, days_back=30)
metrics_df = load_metrics(uid, days_back=30)
profile = (sb.table("profiles").select("*").eq("id", uid).maybe_single().execute().data or {})

# ===== Today overview =====
tz = _user_tz(uid)
start_today_l = datetime.combine(_today(uid), datetime.min.time(), tzinfo=tz)
end_today_l = start_today_l + timedelta(days=1)
start_today_u = start_today_l.astimezone(timezone.utc)
end_today_u = end_today_l.astimezone(timezone.utc)

today_meals = meals_df[(meals_df["ts"] >= start_today_u) & (meals_df["ts"] < end_today_u)] if not meals_df.empty else pd.DataFrame(columns=meals_df.columns)
today_metrics = metrics_df[(metrics_df["ts"] >= start_today_u) & (metrics_df["ts"] < end_today_u)] if not metrics_df.empty else pd.DataFrame(columns=metrics_df.columns)

kcal_goal  = int(prefs.get("daily_calorie_goal") or 2000)
water_goal = int(prefs.get("daily_water_ml") or 2000)
steps_goal = int(prefs.get("daily_step_goal") or 8000)
sleep_goal = int(prefs.get("sleep_goal_min") or 420)

today_kcal  = int(_safe_sum(today_meals.get("calories")))
today_water = int(_safe_max(today_metrics.get("water_ml")))
today_steps = int(_safe_max(today_metrics.get("steps")))
today_sleep = int(_safe_max(today_metrics.get("sleep_minutes")))
_mood_val = _safe_max(today_metrics.get("mood"))
today_mood = None if _mood_val in (0, None) else int(_mood_val)

a,b,c,d,e = st.columns(5)
a.metric("Calories", f"{today_kcal}/{kcal_goal}")
b.metric("Water",    f"{today_water}/{water_goal} ml")
c.metric("Steps",    f"{today_steps}/{steps_goal}")
d.metric("Sleep",    f"{today_sleep}/{sleep_goal} min")
e.metric("Mood",     today_mood if today_mood is not None else "—")

st.divider()

# ===== Calories by day (last 30) =====
st.subheader("Calories (last 30 days)")
if not meals_df.empty and "calories" in meals_df.columns:
    meals_df["date"] = meals_df["ts"].dt.tz_convert("UTC").dt.date
    cal_day = (meals_df.groupby("date", as_index=False)["calories"]
               .sum(numeric_only=True))
    st.bar_chart(cal_day.set_index("date")["calories"])
else:
    st.info("No meals logged yet.")

# ===== Steps & Water by day =====
c1, c2 = st.columns(2)
with c1:
    st.subheader("Steps (last 30 days)")
    if not metrics_df.empty and "steps" in metrics_df.columns:
        m = metrics_df.copy()
        m["date"] = m["ts"].dt.tz_convert("UTC").dt.date
        steps_day = m.groupby("date", as_index=False)["steps"].max(numeric_only=True)
        st.line_chart(steps_day.set_index("date")["steps"])
    else:
        st.info("No metrics yet.")

with c2:
    st.subheader("Water (ml, last 30 days)")
    if not metrics_df.empty and "water_ml" in metrics_df.columns:
        m = metrics_df.copy()
        m["date"] = m["ts"].dt.tz_convert("UTC").dt.date
        water_day = m.groupby("date", as_index=False)["water_ml"].max(numeric_only=True)
        st.line_chart(water_day.set_index("date")["water_ml"])
    else:
        st.info("No metrics yet.")

st.divider()

# ===== Today’s meals =====
st.subheader("Today’s meals")
if today_meals.empty:
    st.info("No meals today yet.")
else:
    for _, row in today_meals.sort_values("ts", ascending=False).iterrows():
        ts_local = row["ts"].astimezone(tz).strftime("%b %d, %Y • %I:%M %p")
        kcal = int((row.get("calories") if row.get("calories") is not None else 0) or 0)
        p = int((row.get("protein_g") if row.get("protein_g") is not None else 0) or 0)
        c = int((row.get("carbs_g") if row.get("carbs_g") is not None else 0) or 0)
        f = int((row.get("fat_g") if row.get("fat_g") is not None else 0) or 0)
        fiber = row.get("fiber_g")
        fiber_val = 0 if fiber is None or pd.isna(fiber) else int(fiber)
        fiber_txt = f" • Fiber:{fiber_val}" if fiber_val else ""
        st.markdown(f"**{ts_local}** — **{kcal} kcal** (P:{p} C:{c} F:{f}){fiber_txt}")

# ======================================================================
# ===================  Gamification & Engagement  ======================
# ======================================================================

def goal_hits_by_day(metrics_df: pd.DataFrame, prefs: dict) -> pd.DataFrame:
    if metrics_df.empty:
        return pd.DataFrame(columns=["day","steps_hit","water_hit","sleep_hit","any_hit","steps","water_ml","sleep_minutes"])
    df = metrics_df.copy()
    df["day"] = df["ts"].dt.tz_convert(timezone.utc).dt.date
    agg = df.groupby("day").agg({
        "steps":"max","water_ml":"max","sleep_minutes":"max"
    }).reset_index()
    steps_goal = int(prefs.get("daily_step_goal") or 8000)
    water_goal = int(prefs.get("daily_water_ml") or 2000)
    sleep_goal = int(prefs.get("sleep_goal_min") or 420)
    agg["steps_hit"] = (agg["steps"] >= steps_goal)
    agg["water_hit"] = (agg["water_ml"] >= water_goal)
    agg["sleep_hit"] = (agg["sleep_minutes"] >= sleep_goal)
    agg["any_hit"]   = agg[["steps_hit","water_hit","sleep_hit"]].any(axis=1)
    return agg.sort_values("day")

def current_streak(hit_series: pd.Series) -> int:
    cnt = 0
    for ok in reversed(list(hit_series)):
        if ok: cnt += 1
        else: break
    return cnt

BADGE_RULES = [
    ("WATER_7D", "Hydration Hero (7-day)", lambda hits: int(hits["water_hit"].tail(7).sum()) >= 7),
    ("STEPS_10K", "10k Steps Day",        lambda hits: ((hits["steps"] >= 10000).tail(1).any()) or (hits["steps_hit"].tail(1).any())),
    ("SLEEP_7x",  "Sleep Consistency",    lambda hits: int(hits["sleep_hit"].tail(7).sum()) >= 5),
]

def evaluate_badges(uid: str, hits: pd.DataFrame):
    earned = []
    for code, label, rule in BADGE_RULES:
        try:
            if not hits.empty and rule(hits):
                earned.append((code,label))
        except Exception:
            pass
    for code, label in earned:
        try:
            sb.table("hw_badges").upsert({
                "uid": uid, "code": code, "earned_on": datetime.now(timezone.utc).date()
            }, on_conflict="uid,code").execute()
        except Exception:
            pass
    return earned

def weekly_summary(hits: pd.DataFrame) -> dict:
    last7 = hits.tail(7)
    if last7.empty:
        return {"water": 0, "steps": 0, "sleep": 0}
    return {
        "water": int(last7["water_hit"].sum()),
        "steps": int(last7["steps_hit"].sum()),
        "sleep": int(last7["sleep_hit"].sum())
    }

st.divider()
st.subheader("Engagement")

hits = goal_hits_by_day(metrics_df, prefs)
streak_any = current_streak(hits["any_hit"]) if not hits.empty else 0
w = weekly_summary(hits)
g1, g2, g3 = st.columns(3)
g1.success(f"🔥 Streak (any goal): {streak_any} days")
g2.info(f"💧 Hydration days (7d): {w['water']}/7")
g3.info(f"🚶 Steps days (7d): {w['steps']}/7")

earned = evaluate_badges(uid, hits)
if earned:
    st.balloons()
    st.success("New badges unlocked: " + ", ".join(lbl for _, lbl in earned))
else:
    st.caption("Keep going to unlock badges like **Hydration Hero**, **Sleep Consistency**, and a **10k Steps Day**!")

# ======================================================================
# ===================    Digital Twin: Future You     ==================
# ======================================================================

st.divider()
st.subheader("Future You — 6-month projection (multi-factor)")

def activity_factor(level: str) -> float:
    m = {
        "Sedentary": 1.2, "Lightly active": 1.375, "Moderately active": 1.55,
        "Very active": 1.725, "Athlete": 1.9
    }
    if not level:
        return 1.2
    for k,v in m.items():
        if k.lower() in str(level).lower():
            return v
    return 1.2

# ---- Build 14–30d baselines
kcal_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["calories"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
steps_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["steps"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
water_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["water_ml"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
sleep_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["sleep_minutes"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
mood_daily  = metrics_df.groupby(metrics_df["ts"].dt.date)["mood"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)
mealq_daily = metrics_df.groupby(metrics_df["ts"].dt.date)["meal_quality"].max(numeric_only=True) if not metrics_df.empty else pd.Series(dtype=float)

kcal_avg  = float(kcal_daily.mean())  if not kcal_daily.empty  else 2000.0
steps_avg = float(steps_daily.mean()) if not steps_daily.empty else 6000.0
water_avg = float(water_daily.mean()) if not water_daily.empty else 1200.0
sleep_avg = float(sleep_daily.mean()) if not sleep_daily.empty else 360.0  # 6h default
mood_avg  = float(mood_daily.mean())  if not mood_daily.empty  else 3.0
mealq_avg = float(mealq_daily.mean()) if not mealq_daily.empty else 3.0

# ---- Multi-factor adjustments
def estimate_tdee(profile: dict, steps_avg: float, sleep_avg: float, water_avg: float) -> float:
    # Mifflin-St Jeor BMR
    age = int(profile.get("age") or 30)
    h = float(profile.get("height_cm") or 170.0)
    w = float(profile.get("weight_kg") or 75.0)
    gender = (profile.get("gender") or "").lower()
    bmr = 10*w + 6.25*h - 5*age + (5 if gender.startswith("m") else -161 if gender.startswith("f") else -78)
    af = activity_factor(profile.get("activity_level"))

    # Steps bonus (~+80 kcal per +2k steps/day)
    steps_bonus = 80.0 * max(0.0, (steps_avg - 6000.0) / 2000.0)

    # Sleep penalty (sleep debt reduces energy expenditure / activity)
    sleep_pen = -100.0 if sleep_avg < 360 else (-50.0 if sleep_avg < 420 else 0.0)

    # Hydration effect (mild): if very low, small penalty
    water_pen = -40.0 if water_avg < 1000 else 0.0

    return bmr * af + steps_bonus + sleep_pen + water_pen

def adherence_multiplier(mood_avg: float, mealq_avg: float, streak_any: int) -> float:
    """
    Models how well you stick to a plan:
      - Lower mood tends to reduce adherence
      - Better meal quality improves it
      - Streak momentum helps
    """
    mood_term = 0.92 if mood_avg < 3 else (1.0 if mood_avg < 4 else 1.03)
    meal_term = 0.96 if mealq_avg < 3 else (1.0 if mealq_avg < 4 else 1.04)
    streak_term = min(1.08, 1.0 + 0.01 * min(30, streak_any))  # +1% per day up to +8%
    return mood_term * meal_term * streak_term

def project_weight_series(profile: dict, kcal_intake: float, steps_avg: float,
                          sleep_avg: float, water_avg: float,
                          delta_steps: int = 2000, days: int = 180,
                          adherence: float = 1.0) -> list[float]:
    w0 = float(profile.get("weight_kg") or 75.0)
    tdee0 = estimate_tdee(profile, steps_avg, sleep_avg, water_avg)
    kcal_extra = 80.0 * (delta_steps / 2000.0)  # rough steps→kcal mapping
    series = []
    w = w0
    for _ in range(days+1):
        # adherence applies to both intake AND activity deltas (behavior realism)
        tdee = tdee0 + (kcal_extra * adherence)
        intake = kcal_intake * adherence + kcal_intake * (1 - adherence)  # same intake; multiplier influences deltas above
        delta_kg = (intake - tdee) / 7700.0
        w = max(35.0, w + delta_kg)
        series.append(w)
    return series

def bmi_series(kg_series: list[float], height_cm: float) -> list[float]:
    m2 = (height_cm/100.0)**2
    return [round(w/m2, 1) for w in kg_series]

delta_steps = st.slider("Additional steps per day", 0, 5000, 2000, 500)
adherence = adherence_multiplier(mood_avg, mealq_avg, streak_any)

series_base = project_weight_series(profile, kcal_avg, steps_avg, sleep_avg, water_avg,
                                    delta_steps=0, days=180, adherence=adherence)
series_bump = project_weight_series(profile, kcal_avg, steps_avg, sleep_avg, water_avg,
                                    delta_steps=delta_steps, days=180, adherence=adherence)

height_cm = float(profile.get("height_cm") or 170.0)
bmi_base = bmi_series(series_base, height_cm)
bmi_bump = bmi_series(series_bump, height_cm)

fig, ax = plt.subplots()
ax.plot(range(181), series_base, label="Current routine (kg)")
ax.plot(range(181), series_bump, label=f"+{delta_steps} steps/day (kg)")
ax.set_xlabel("Days")
ax.set_ylabel("Weight (kg)")
ax.legend()
st.pyplot(fig)

b0, b1 = bmi_base[-1], bmi_bump[-1]
w0, w1 = series_base[-1], series_bump[-1]

st.info(
    f"**Multi-factor model** — uses your average calories ({kcal_avg:.0f}), steps ({steps_avg:.0f}/day), "
    f"sleep ({sleep_avg:.0f} min), water ({water_avg:.0f} ml), mood ({mood_avg:.1f}/5), meal quality ({mealq_avg:.1f}/5) and streak momentum.\n\n"
    f"At current pace → ~**{w0:.1f} kg** (BMI {b0}).  "
    f"With +{delta_steps} steps/day → ~**{w1:.1f} kg** (BMI {b1}).  "
    f"_This is a simplified trend model — use direction, not absolutes._"
)
