# pages/07_Preferences.py
import math
import streamlit as st
from supabase import create_client
from nav import top_nav

# -------------------- Page & Styles --------------------
st.set_page_config(
    page_title="Preferences - Health Whisperer",
    layout="wide",
    initial_sidebar_state="collapsed",
)
st.markdown("""
<style>
/* hide page links in sidebar */
section[data-testid="stSidebarNav"] { display:none; }
</style>
""", unsafe_allow_html=True)

# -------------------- Supabase --------------------
@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)
sb = get_sb()

def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Preferences")


# -------------------- Auth Guard --------------------
if "sb_session" not in st.session_state:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]

# -------------------- Helpers --------------------
DEFAULT_GOALS = {
    # activity
    "steps": 8000,
    # hydration (stored in ml)
    "water_ml": 2500,
    # sleep (stored in minutes)
    "sleep_minutes": 420,
    # diet
    "calories": 2000,         # kcal/day
    "protein": 75,            # g/day
    "sugar": 50,              # g/day (added sugar limit)
    # mental health
    "journaling": "None",     # None | Daily | 3x/week | Weekly
    "meditation": 10,         # minutes/day
    "mood_target": 3.5,       # 1.0–5.0 average target
}

def _safe_goals_merge(raw: dict | None) -> dict:
    """Merge stored goals with defaults and normalize keys."""
    g = dict(DEFAULT_GOALS)
    if isinstance(raw, dict):
        # known numeric keys
        for k in ["steps", "water_ml", "sleep_minutes", "calories", "protein", "sugar", "meditation"]:
            if k in raw and raw[k] is not None:
                try:
                    g[k] = int(raw[k])
                except:
                    pass
        # float target for mood (allow 1 decimal)
        if "mood_target" in raw and raw["mood_target"] is not None:
            try:
                g["mood_target"] = float(raw["mood_target"])
            except:
                pass
        # journaling frequency
        if "journaling" in raw and raw["journaling"]:
            g["journaling"] = str(raw["journaling"])
    return g

def read_hw_preferences(uid: str):
    """Read full hw_preferences row if exists; else None."""
    try:
        res = sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single().execute()
        return getattr(res, "data", None)
    except Exception:
        return None

def read_profile_goals_text(uid: str) -> dict:
    """
    Fallback: parse profiles.goals like 'steps_goal:8000; water_goal:8; sleep_goal:420'
    We'll interpret a small water value as glasses and convert → ml.
    """
    try:
        res = sb.table("profiles").select("goals").eq("id", uid).maybe_single().execute()
        txt = ((getattr(res, "data", None) or {}).get("goals") or "").strip()
    except Exception:
        txt = ""

    g = dict(DEFAULT_GOALS)
    if not txt:
        return g

    for tok in txt.split(";"):
        k, _, v = tok.strip().partition(":")
        k = k.strip().lower()
        v = v.strip()
        if not v:
            continue
        try:
            iv = int(v)
        except:
            iv = None

        if "step" in k and iv is not None:
            g["steps"] = iv
        elif "water" in k and iv is not None:
            # If small number, treat as glasses (≈250 ml per glass)
            g["water_ml"] = iv if iv > 50 else iv * 250
        elif "sleep" in k and iv is not None:
            g["sleep_minutes"] = iv
        elif "cal" in k and iv is not None:
            g["calories"] = iv
    return g

# -------------------- Load Preferences --------------------
pref_row = read_hw_preferences(uid)

pref = {
    "nudge_channel": "telegram",
    "nudge_cadence": "smart",
    "nudge_tone": "gentle",
    "quiet_start": "22:00",
    "quiet_end": "07:00",
    "goals": dict(DEFAULT_GOALS),
}

if pref_row:
    pref["nudge_channel"] = pref_row.get("nudge_channel", pref["nudge_channel"])
    pref["nudge_cadence"] = pref_row.get("nudge_cadence", pref["nudge_cadence"])
    pref["nudge_tone"] = pref_row.get("nudge_tone", pref["nudge_tone"])
    pref["quiet_start"] = pref_row.get("quiet_start") or pref["quiet_start"]
    pref["quiet_end"] = pref_row.get("quiet_end") or pref["quiet_end"]
    pref["goals"] = _safe_goals_merge(pref_row.get("goals") or {})
else:
    # fallback: get basic goals from profiles.goals
    pref["goals"] = _safe_goals_merge(read_profile_goals_text(uid))

# -------------------- UI --------------------
st.title("Nudge Preferences")

st.caption("Answer in everyday units. We’ll store the right format for the nudge engine.")

# --- Channel / cadence / tone ---
c1, c2, c3 = st.columns(3)
nudge_channel = c1.selectbox(
    "Where should we nudge you?",
    ["telegram", "inapp"],
    index=["telegram", "inapp"].index(pref.get("nudge_channel", "telegram")),
    help="Telegram is great for timely nudges. In-app shows hints when you open the dashboard."
)
nudge_cadence = c2.selectbox(
    "How often?",
    ["smart", "hourly", "3_per_day"],
    index=["smart", "hourly", "3_per_day"].index(pref.get("nudge_cadence", "smart")),
    help="Smart tries to nudge only when it matters (gaps, time-of-day)."
)
nudge_tone = c3.selectbox(
    "Tone",
    ["gentle", "coachy", "fun"],
    index=["gentle", "coachy", "fun"].index(pref.get("nudge_tone", "gentle"))
)

# --- Quiet hours ---
st.subheader("Quiet hours")
q1, q2 = st.columns(2)
qs = q1.time_input("Start", value=None, step=300, help="Defaults to 22:00 if blank.")
qe = q2.time_input("End", value=None, step=300, help="Defaults to 07:00 if blank.")
st.caption("We won’t nudge you during quiet hours (sleep/focus time).")

st.divider()

# --- Goals (interactive & practical units) ---
st.subheader("Daily Goals")

# Activity / Hydration / Sleep
gA, gB, gC = st.columns(3)
steps_goal = gA.slider("Steps", 1000, 20000, int(pref["goals"]["steps"]), step=500,
                       help="A practical daily movement target.")
water_goal_l = gB.slider(
    "Water (litres)",
    0.5, 5.0,
    round(pref["goals"]["water_ml"] / 1000, 1),
    step=0.1,
    help="We’ll convert to ml internally."
)
# quick tip: show equivalent glasses (≈250 ml per glass)
approx_glasses = int(round((water_goal_l * 1000) / 250))
gB.caption(f"≈ {approx_glasses} glasses (250 ml each)")

sleep_goal_h = gC.slider(
    "Sleep (hours)",
    4.0, 12.0,
    round(pref["goals"]["sleep_minutes"] / 60, 1),
    step=0.5,
    help="We’ll convert to minutes internally."
)

st.markdown("#### Diet")
d1, d2, d3, d4 = st.columns(4)
calorie_goal = d1.number_input("Calories (kcal)", min_value=1000, max_value=5000, value=int(pref["goals"]["calories"]))
protein_goal = d2.number_input("Protein (g)", min_value=0, max_value=300, value=int(pref["goals"]["protein"]))
sugar_limit = d3.number_input("Added sugar limit (g)", min_value=0, max_value=200, value=int(pref["goals"]["sugar"]))
fiber_goal = d4.number_input("Fiber (g) — optional", min_value=0, max_value=100,
                             value=int(pref["goals"].get("fiber", 0)))
st.caption("Tip: Balance your plate — prioritise protein and fiber, limit added sugar.")

st.markdown("#### Mental health")
m1, m2, m3 = st.columns(3)
journaling = m1.selectbox(
    "Journaling frequency",
    ["None", "Daily", "3x/week", "Weekly"],
    index=["None", "Daily", "3x/week", "Weekly"].index(pref["goals"].get("journaling", "None"))
)
meditation_min = m2.slider("Meditation (min/day)", 0, 120, int(pref["goals"]["meditation"]), 5)
mood_target = m3.slider("Target average mood (1–5)", 1.0, 5.0, float(pref["goals"].get("mood_target", 3.5)), 0.1)

st.divider()

# --- Optional smart reminders (stored alongside goals; used later by your rules engine) ---
with st.expander("Optional smart reminders"):
    r1, r2, r3 = st.columns(3)
    remind_hydration = r1.checkbox("Hydration: ping if <50% by 5pm",
                                   value=bool(pref.get("remind_hydration", True)))
    remind_steps = r2.checkbox("Steps: ping if <60% by 7pm",
                               value=bool(pref.get("remind_steps", True)))
    remind_sleep = r3.checkbox("Sleep: bedtime nudge 60 min before target",
                               value=bool(pref.get("remind_sleep", False)))

# -------------------- Save --------------------
if st.button("Save", type="primary"):
    quiet_start = qs.strftime("%H:%M") if qs else (pref.get("quiet_start") or "22:00")
    quiet_end   = qe.strftime("%H:%M") if qe else (pref.get("quiet_end") or "07:00")

    goals_json = {
        # required by current nudge engine
        "steps": int(steps_goal),
        "water_ml": int(water_goal_l * 1000),
        "sleep_minutes": int(round(sleep_goal_h * 60)),
        # diet
        "calories": int(calorie_goal),
        "protein": int(protein_goal),
        "sugar": int(sugar_limit),
        "fiber": int(fiber_goal),
        # mental health
        "journaling": journaling,
        "meditation": int(meditation_min),
        "mood_target": round(float(mood_target), 1),
    }

    payload = {
        "uid": uid,
        "nudge_channel": nudge_channel,
        "nudge_cadence": nudge_cadence,
        "nudge_tone": nudge_tone,
        "quiet_start": quiet_start,
        "quiet_end": quiet_end,
        "goals": goals_json,
        "remind_hydration": bool(remind_hydration),
        "remind_steps": bool(remind_steps),
        "remind_sleep": bool(remind_sleep),
    }

    try:
        # if row exists → update; else insert
        existing = sb.table("hw_preferences").select("uid").eq("uid", uid).maybe_single().execute()
        if getattr(existing, "data", None):
            sb.table("hw_preferences").update(payload).eq("uid", uid).execute()
        else:
            sb.table("hw_preferences").insert(payload).execute()
        st.success("Preferences saved! Nudges will use these updated goals and settings.")
    except Exception as e:
        # Fallback: save minimal goals to profiles.goals (text)
        text = f"steps_goal:{goals_json['steps']}; water_goal:{goals_json['water_ml']}; sleep_goal:{goals_json['sleep_minutes']}; calories_goal:{goals_json['calories']}"
        try:
            sb.table("profiles").upsert({"id": uid, "goals": text}).execute()
            st.info("Saved basic goals in profiles.goals. For full preferences, create the hw_preferences table.")
        except Exception as e2:
            st.error(f"Could not save preferences: {e2}")
