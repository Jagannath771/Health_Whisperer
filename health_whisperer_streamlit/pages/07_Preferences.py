# pages/07_Preferences.py
import time
from datetime import datetime, time as dt_time, timezone
from zoneinfo import ZoneInfo

import streamlit as st
import pandas as pd
from supabase import create_client
from httpx import ReadError

from nav import top_nav

# ================= Page config =================
st.set_page_config(page_title="Preferences - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>section[data-testid='stSidebarNav']{display:none;}</style>", unsafe_allow_html=True)

# ================= Helpers =================
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

@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

sb = get_sb()

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

def _time_to_str(t: dt_time | None) -> str:
    if not t: return "22:00"
    return f"{t.hour:02d}:{t.minute:02d}"

def _str_to_time(s: str | None, fallback: str) -> dt_time:
    s = (s or fallback or "22:00").strip()
    try:
        hh, mm = s.split(":")[:2]
        return dt_time(int(hh), int(mm))
    except Exception:
        # fallback if stored as "22:00:00"
        try:
            hh, mm, _ss = s.split(":")[:3]
            return dt_time(int(hh), int(mm))
        except Exception:
            return dt_time(22, 0)

# ================= Auth / Nav =================
def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Preferences")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]

# ================= Load current prefs =================
pref_row = {}
try:
    r = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single())
    pref_row = r.data or {}
except Exception:
    pref_row = {}

st.title("Nudge Preferences")

# ================= Sections =================
c1, c2, c3 = st.columns(3)

# Channel
nudge_channel = c1.selectbox(
    "Channel",
    ["telegram", "inapp"],
    index=(["telegram", "inapp"].index(pref_row.get("nudge_channel", "telegram"))
           if pref_row.get("nudge_channel") in ["telegram", "inapp"] else 0)
)

# Cadence
nudge_cadence = c2.selectbox(
    "Cadence",
    ["smart", "hourly", "3_per_day"],
    index=(["smart", "hourly", "3_per_day"].index(pref_row.get("nudge_cadence", "smart"))
           if pref_row.get("nudge_cadence") in ["smart", "hourly", "3_per_day"] else 0)
)

# Tone
nudge_tone = c3.selectbox(
    "Tone",
    ["gentle", "coach", "strict"],
    index=(["gentle", "coach", "strict"].index(pref_row.get("nudge_tone", "gentle"))
           if pref_row.get("nudge_tone") in ["gentle", "coach", "strict"] else 0)
)

st.divider()

# Timezone & Quiet hours
st.subheader("Timing & Quiet Hours")
tz_options = [
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "Europe/London", "Europe/Berlin", "Asia/Kolkata", "Asia/Singapore", "Australia/Sydney"
]
tz_val = pref_row.get("tz") or "America/New_York"
tz = st.selectbox("Profile timezone", tz_options, index=tz_options.index(tz_val) if tz_val in tz_options else 0)

qc1, qc2 = st.columns(2)
quiet_start = _str_to_time(pref_row.get("quiet_start"), "22:00")
quiet_end   = _str_to_time(pref_row.get("quiet_end"), "07:00")
qs = qc1.time_input("Quiet hours start", value=quiet_start, step=300)
qe = qc2.time_input("Quiet hours end",   value=quiet_end,   step=300)
st.caption("During quiet hours, nudges are suppressed.")

st.divider()

# Goals
st.subheader("Goals")
gc1, gc2, gc3 = st.columns(3)

steps_goal = gc1.number_input(
    "Daily steps",
    min_value=1000, max_value=30000, step=500,
    value=int(pref_row.get("daily_step_goal") or (pref_row.get("goals", {}) or {}).get("steps") or 8000)
)

water_goal_l = gc2.number_input(
    "Daily water (liters)",
    min_value=0.5, max_value=6.0, step=0.25,
    value=float((pref_row.get("daily_water_ml") or (pref_row.get("goals", {}) or {}).get("water_ml") or 2000)/1000.0)
)

sleep_goal_h = gc3.number_input(
    "Sleep (hours)",
    min_value=4.0, max_value=12.0, step=0.5,
    value=float((pref_row.get("sleep_goal_min") or (pref_row.get("goals", {}) or {}).get("sleep_minutes") or 420)/60.0)
)

gc4, gc5, gc6 = st.columns(3)
calorie_goal = gc4.number_input(
    "Calories (kcal)",
    min_value=1000, max_value=5000, step=50,
    value=int(pref_row.get("daily_calorie_goal") or (pref_row.get("goals", {}) or {}).get("calories") or 2000)
)

protein_goal = gc5.number_input(
    "Protein (g)",
    min_value=20, max_value=300, step=5,
    value=int(pref_row.get("protein_target_g") or (pref_row.get("goals", {}) or {}).get("protein") or 80)
)

sugar_limit = gc6.number_input(
    "Added sugar (g) — soft target",
    min_value=0, max_value=150, step=5,
    value=int((pref_row.get("goals", {}) or {}).get("sugar") or 50)
)

st.divider()

# Calendar suppression
st.subheader("Calendar suppression")
ics_url = st.text_input(
    "Calendar ICS URL (optional)",
    value=(pref_row or {}).get("calendar_ics_url") or "",  # <= coalesce None to ""
    help="Paste a private ICS link from Google/Outlook/iCloud to silence nudges during events."
)


# Telegram (display only / optional override)
st.subheader("Telegram")
tg_display = (pref_row.get("telegram_chat_id") or "") or ""
tg_col1, tg_col2 = st.columns([3,1])
tg_col1.text_input("Linked Telegram chat id", value=str(tg_display), disabled=True)
with tg_col2:
    st.caption("Use /link in the bot to connect or re-link.")

st.divider()

# ================= Save =================
save = st.button("Save preferences", type="primary")
if save:
    # convert liters/hours back to ml/min
    water_ml = int(round(float(water_goal_l) * 1000))
    sleep_min = int(round(float(sleep_goal_h) * 60))

    # goals payload (kept for future expansion)
    goals_payload = {
        "steps": int(steps_goal),
        "water_ml": water_ml,
        "sleep_minutes": sleep_min,
        "calories": int(calorie_goal),
        "protein": int(protein_goal),
        "sugar": int(sugar_limit),
    }
    payload = {
        "uid": uid,
        "tz": tz,
        "nudge_channel": nudge_channel,
        "nudge_cadence": nudge_cadence,
        "nudge_tone": nudge_tone,
        "quiet_start": _time_to_str(qs),
        "quiet_end": _time_to_str(qe),
        "goals": goals_payload,
        "daily_step_goal": goals_payload["steps"],
        "daily_water_ml": goals_payload["water_ml"],
        "sleep_goal_min": goals_payload["sleep_minutes"],
        "daily_calorie_goal": goals_payload["calories"],
        "protein_target_g": goals_payload["protein"],
        "calendar_ics_url": (ics_url or "").strip() or None,
    }
    try:
        exec_with_retry(sb.table("hw_preferences").upsert(payload, on_conflict="uid"))
        st.success("Preferences saved! Nudges will respect quiet hours and your calendar.")
    except Exception as e:
        st.error(f"Failed to save preferences: {e}")

# ================= Info panel =================
with st.expander("What do these settings do?", expanded=False):
    st.markdown("""
- **Channel**: where you receive nudges (Telegram or in-app).
- **Cadence**: `smart` adapts to your day; `hourly` or `3_per_day` are fixed.
- **Tone**: choose the coaching vibe of your nudges.
- **Quiet hours**: nudges are muted (e.g., 22:00–07:00).
- **Calendar ICS**: if set, nudges are suppressed during events.
- **Goals**: drive streaks, badges, and pacing (steps/water/sleep/calories).
    """)
