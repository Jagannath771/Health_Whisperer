# pages/05_Dashboard.py
import time
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
from supabase import create_client
from httpx import ReadError

from nav import top_nav

# ===== Page config =====
st.set_page_config(page_title="Dashboard - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>section[data-testid='stSidebarNav']{display:none;}</style>", unsafe_allow_html=True)

# ===== Retry helper for Supabase (handles WinError 10035 ReadError) =====
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

# ===== Robust timestamp parsing (fix for your error) =====
def _to_dt(series: pd.Series) -> pd.Series:
    # Parse ISO8601 variants like "...Z" or "+00:00"; keep tz-aware in UTC
    return pd.to_datetime(series, format="ISO8601", utc=True, errors="coerce")

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
    # --- THE FIX: robust datetime parse ---
    df["ts"] = _to_dt(df["ts"])
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
    return df

def get_prefs(uid: str) -> dict:
    try:
        r = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single())
        return r.data or {}
    except Exception:
        return {}

# ===== Load everything =====
prefs = get_prefs(uid)
meals_df = load_meals(uid, days_back=14)
metrics_df = load_metrics(uid, days_back=14)

# ===== Today overview =====
tz = _user_tz(uid)
start_today_l = datetime.combine(_today(uid), datetime.min.time(), tzinfo=tz)
end_today_l = start_today_l + timedelta(days=1)
start_today_u = start_today_l.astimezone(timezone.utc)
end_today_u = end_today_l.astimezone(timezone.utc)

today_meals = meals_df[(meals_df["ts"] >= start_today_u) & (meals_df["ts"] < end_today_u)]
today_metrics = metrics_df[(metrics_df["ts"] >= start_today_u) & (metrics_df["ts"] < end_today_u)]

kcal_goal  = int(prefs.get("daily_calorie_goal") or 2000)
water_goal = int(prefs.get("daily_water_ml") or 2000)
steps_goal = int(prefs.get("daily_step_goal") or 8000)
sleep_goal = int(prefs.get("sleep_goal_min") or 420)

today_kcal  = int(today_meals.get("calories", pd.Series([0])).fillna(0).sum())
today_water = int(today_metrics.get("water_ml", pd.Series([0])).fillna(0).max())
today_steps = int(today_metrics.get("steps", pd.Series([0])).fillna(0).max())
today_sleep = int(today_metrics.get("sleep_minutes", pd.Series([0])).fillna(0).max())
today_mood  = int(today_metrics.get("mood", pd.Series([0])).fillna(0).max())

a,b,c,d,e = st.columns(5)
a.metric("Calories", f"{today_kcal}/{kcal_goal}")
b.metric("Water",    f"{today_water}/{water_goal} ml")
c.metric("Steps",    f"{today_steps}/{steps_goal}")
d.metric("Sleep",    f"{today_sleep}/{sleep_goal} min")
e.metric("Mood",     today_mood or "—")

st.divider()

# ===== Calories by day (last 14) =====
st.subheader("Calories (last 14 days)")
if not meals_df.empty:
    meals_df["date"] = meals_df["ts"].dt.tz_convert("UTC").dt.date
    cal_day = meals_df.groupby("date", as_index=False)["calories"].sum()
    st.bar_chart(cal_day.set_index("date")["calories"])
else:
    st.info("No meals logged yet.")

# ===== Steps & Water by day =====
c1, c2 = st.columns(2)
with c1:
    st.subheader("Steps (last 14 days)")
    if not metrics_df.empty:
        m = metrics_df.copy()
        m["date"] = m["ts"].dt.tz_convert("UTC").dt.date
        steps_day = m.groupby("date", as_index=False)["steps"].max(numeric_only=True)
        st.line_chart(steps_day.set_index("date")["steps"])
    else:
        st.info("No metrics yet.")

with c2:
    st.subheader("Water (ml, last 14 days)")
    if not metrics_df.empty:
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
        kcal = int(row.get("calories") or 0)
        p = int(row.get("protein_g") or 0)
        c = int(row.get("carbs_g") or 0)
        f = int(row.get("fat_g") or 0)
        fiber = row.get("fiber_g")
        fiber_txt = f" • Fiber:{int(fiber)}" if fiber not in (None, 0, "") else ""
        st.markdown(f"**{ts_local}** — **{kcal} kcal** (P:{p} C:{c} F:{f}){fiber_txt}")
        items = row.get("items") or []
        if isinstance(items, str):
            # try to show parsed items if json was stored as string
            try:
                import json
                items = json.loads(items)
            except Exception:
                items = []
        for it in (items or []):
            name = it.get("name") if isinstance(it, dict) else str(it)
            qty  = (it.get("qty_g") if isinstance(it, dict) else None)
            note = (it.get("notes") if isinstance(it, dict) else None)
            qtxt = f" ({int(qty)} g)" if qty not in (None, "", 0) else ""
            ntxt = f" — {note}" if note else ""
            st.write(f"- {name}{qtxt}{ntxt}")
