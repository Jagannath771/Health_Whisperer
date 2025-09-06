# pages/05_Dashboard.py
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests
import pytz
import streamlit as st
from supabase import create_client

from nav import top_nav

# -------------------- Page & Styles --------------------
st.set_page_config(page_title="Dashboard - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("""
<style>
section[data-testid="stSidebarNav"] { display:none; }
</style>
""", unsafe_allow_html=True)

# -------------------- Supabase Client --------------------
@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)

sb = get_sb()

def render_sign_out():
    if "sb_session" in st.session_state and st.button("Sign out"):
        sb.auth.sign_out()
        st.session_state.pop("sb_session", None)
        st.switch_page("health_whisperer_streamlit/pages/05_Dashboard.py")    # or "06_Log_Metrics.py" on that page
        st.switch_page("health_whisperer_streamlit/pages/02_Sign_In.py")

# -------------------- Navbar & Auth Guard --------------------
top_nav(current="Dashboard", right_slot=render_sign_out)

if "sb_session" not in st.session_state:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]
email = st.session_state["sb_session"]["email"]

# -------------------- Profile --------------------
res = sb.table("profiles").select("*").eq("id", uid).maybe_single().execute()
profile = getattr(res, "data", None) or {}
name = profile.get("full_name") or email
user_tz_name = profile.get("timezone") or "America/New_York"
try:
    user_tz = pytz.timezone(user_tz_name)
except Exception:
    user_tz_name = "America/New_York"
    user_tz = pytz.timezone(user_tz_name)

st.title(f"Hi {name.split()[0] if name else ''}, here’s your snapshot")

# -------------------- KPIs --------------------
def _to_float(v, default=0.0):
    try:
        return float(v or 0)
    except:
        return default

height_cm = _to_float(profile.get("height_cm"))
weight_kg = _to_float(profile.get("weight_kg"))
bmi = round(weight_kg / (height_cm/100)**2, 1) if height_cm and weight_kg else None

def bmi_bucket(x):
    if x is None: return "—"
    if x < 18.5: return "Underweight"
    if x < 25:   return "Normal"
    if x < 30:   return "Overweight"
    return "Obese"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Weight (kg)", f"{weight_kg:.1f}" if weight_kg else "—")
c2.metric("Height (cm)", f"{height_cm:.1f}" if height_cm else "—")
c3.metric("BMI", f"{bmi:.1f}" if bmi else "—", help="BMI is a rough screening tool, not a diagnosis.")
c4.metric("Category", bmi_bucket(bmi))

st.divider()

# -------------------- Load Goals (prefer hw_preferences, else profiles.goals) --------------------
def load_goals_and_prefs(uid: str):
    # Try hw_preferences JSON goals + meta
    prefs = {}
    try:
        p = sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single().execute()
        prefs = getattr(p, "data", None) or {}
    except Exception:
        prefs = {}

    # goals default
    g = {"steps": 8000, "water_ml": 2500, "sleep_minutes": 420}
    # extra goals defaults (if present we’ll show them)
    defaults_extra = {"calories": 2000, "protein": 75, "sugar": 50, "fiber": 0,
                      "journaling": "None", "meditation": 10, "mood_target": 3.5}

    if prefs and isinstance(prefs.get("goals"), dict):
        gg = dict(prefs["goals"])
        g["steps"] = int(gg.get("steps", g["steps"]) or g["steps"])
        g["water_ml"] = int(gg.get("water_ml", g["water_ml"]) or g["water_ml"])
        g["sleep_minutes"] = int(gg.get("sleep_minutes", g["sleep_minutes"]) or g["sleep_minutes"])
        for k, v in defaults_extra.items():
            g[k] = gg.get(k, v)
    else:
        # Fallback to profiles.goals text (e.g., "steps_goal:8000; water_goal:8; sleep_goal:420")
        txt = (profile.get("goals") or "").strip()
        if txt:
            for tok in txt.split(";"):
                k, _, v = tok.strip().partition(":")
                v = v.strip()
                if not v:
                    continue
                if "step" in k:
                    try: g["steps"] = int(v)
                    except: pass
                elif "water" in k:
                    # interpret small values as glasses; convert (~250 ml/glass)
                    try:
                        val = int(v)
                        g["water_ml"] = val if val > 50 else val * 250
                    except: pass
                elif "sleep" in k:
                    try: g["sleep_minutes"] = int(v)
                    except: pass
        # fill extras with defaults
        for k, v in defaults_extra.items():
            g.setdefault(k, v)

    return g, prefs

GOALS, PREFS = load_goals_and_prefs(uid)

# -------------------- Nudges 101 (new) --------------------
st.subheader("Nudges 101")

def _localize(dt_utc_str: str | None):
    if not dt_utc_str:
        return "—"
    try:
        dt = datetime.fromisoformat(dt_utc_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(user_tz).strftime("%b %d, %Y %I:%M %p")
    except Exception:
        return "—"

# Last nudge
last_log = (
    sb.table("hw_nudge_logs")
      .select("decided_at, nudge_type, channel, delivered")
      .eq("uid", uid)
      .order("decided_at", desc=True)
      .limit(1)
      .execute()
      .data or []
)
last = last_log[0] if last_log else {}

# Estimate next nudge (best-effort)
def estimate_next_nudge():
    cadence = (PREFS.get("nudge_cadence") or "smart").lower()
    last_at = None
    if last:
        try:
            t = last["decided_at"].replace("Z", "+00:00")
            last_at = datetime.fromisoformat(t)
            if last_at.tzinfo is None:
                last_at = last_at.replace(tzinfo=timezone.utc)
        except Exception:
            last_at = None

    now = datetime.now(timezone.utc)
    if cadence == "hourly":
        base = (last_at or now) + timedelta(hours=1)
    elif cadence == "3_per_day":
        base = (last_at or now) + timedelta(hours=4)
    else:
        # smart: rough guess → ~6h spacing unless large gaps accelerate it
        base = (last_at or now) + timedelta(hours=6)

    # avoid quiet hours (approx; assumes quiet in user's local time)
    q_start = (PREFS.get("quiet_start") or "22:00")[:5]
    q_end   = (PREFS.get("quiet_end") or "07:00")[:5]
    try:
        sh, sm = [int(x) for x in q_start.split(":")]
        eh, em = [int(x) for x in q_end.split(":")]
    except Exception:
        sh, sm, eh, em = 22, 0, 7, 0

    base_local = base.astimezone(user_tz)
    # build same-day windows
    start_local = base_local.replace(hour=sh, minute=sm, second=0, microsecond=0)
    end_local   = base_local.replace(hour=eh, minute=em, second=0, microsecond=0)

    # If inside quiet hours, push to end of quiet window
    inside_quiet = (start_local <= base_local <= end_local) if start_local <= end_local else (base_local >= start_local or base_local <= end_local)
    if inside_quiet:
        if start_local <= end_local:
            base_local = end_local + timedelta(minutes=15)
        else:
            # wraps midnight
            if base_local >= start_local:
                # push to next day's end_local
                base_local = (end_local + timedelta(days=1)) + timedelta(minutes=15)
            else:
                # before end_local today → push to end_local today
                base_local = end_local + timedelta(minutes=15)
    return base_local.strftime("%b %d, %Y %I:%M %p")

colA, colB, colC, colD = st.columns(4)
colA.metric("Channel", (PREFS.get("nudge_channel") or "telegram").title())
colB.metric("Cadence", (PREFS.get("nudge_cadence") or "smart").replace("_", " ").title())
colC.metric("Quiet hours", f"{(PREFS.get('quiet_start') or '22:00')}–{(PREFS.get('quiet_end') or '07:00')}")
colD.metric("Last nudge", _localize(last.get("decided_at")))

st.caption(f"Next nudge (est.): {estimate_next_nudge()}  •  Timezone: {user_tz_name}")

# Compact goals view
g1, g2, g3, g4 = st.columns(4)
g1.metric("Steps goal", f"{int(GOALS['steps']):,}")
g2.metric("Water goal", f"{GOALS['water_ml']/1000:.1f} L")
g3.metric("Sleep goal", f"{GOALS['sleep_minutes']/60:.1f} h")
g4.metric("Calories goal", f"{int(GOALS.get('calories', 2000)):,}")

with st.expander("See full goals"):
    colx, coly, colz = st.columns(3)
    colx.write(f"**Protein**: {int(GOALS.get('protein', 75))} g")
    colx.write(f"**Sugar limit**: {int(GOALS.get('sugar', 50))} g")
    colx.write(f"**Fiber**: {int(GOALS.get('fiber', 0))} g")
    coly.write(f"**Journaling**: {GOALS.get('journaling', 'None')}")
    coly.write(f"**Meditation**: {int(GOALS.get('meditation', 10))} min/day")
    colz.write(f"**Mood target**: {float(GOALS.get('mood_target', 3.5)):.1f}/5")

st.divider()

# -------------------- Real Metrics (last 7 days) --------------------
st.subheader("Your last 7 days")

now_utc = datetime.now(timezone.utc)
start_7d = (now_utc - timedelta(days=7)).isoformat()

mres = (sb.table("hw_metrics")
          .select("ts, steps, calories, mood, sleep_minutes, water_ml")
          .eq("uid", uid)
          .gte("ts", start_7d)
          .order("ts")
          .execute())
metrics7 = getattr(mres, "data", []) or []

if metrics7:
    dfm = pd.DataFrame(metrics7)
    dfm["ts"] = pd.to_datetime(dfm["ts"])
    st.line_chart(dfm.set_index("ts")[["steps", "calories"]])

    # Daily aggregates for 7-day totals
    dfm["date"] = dfm["ts"].dt.date
    by_day = dfm.groupby("date", as_index=False).agg({
        "steps":"sum",
        "calories":"sum",
        "water_ml":"sum",
        "sleep_minutes":"sum",
        "mood":"mean"
    }).sort_values("date")

    # 7-day totals
    wk_steps = int(by_day["steps"].fillna(0).sum())
    wk_cal   = int(by_day["calories"].fillna(0).sum())
    wk_water = int(by_day["water_ml"].fillna(0).sum())
    wk_sleep = int(by_day["sleep_minutes"].fillna(0).sum())

    # Goals over 7 days
    tgt_steps = int(GOALS["steps"]) * 7
    tgt_water = int(GOALS["water_ml"]) * 7
    tgt_sleep = int(GOALS["sleep_minutes"]) * 7

    cA, cB, cC = st.columns(3)
    cA.metric("Avg steps", int(dfm["steps"].dropna().mean()) if dfm["steps"].notna().any() else "—")
    cB.metric("Avg calories", int(dfm["calories"].dropna().mean()) if dfm["calories"].notna().any() else "—")
    cC.metric("Avg mood", round(dfm["mood"].dropna().mean(), 1) if dfm["mood"].notna().any() else "—")

    st.markdown("#### Weekly totals vs. goals")
    cc1, cc2, cc3 = st.columns(3)

    with cc1:
        st.metric("Steps (7d)", f"{wk_steps:,}", f"Goal {tgt_steps:,}")
        st.progress(min(wk_steps / tgt_steps, 1.0) if tgt_steps else 0.0)

    with cc2:
        # Convert ml → L for readability
        st.metric("Water (7d)", f"{wk_water/1000:.1f} L", f"Goal {tgt_water/1000:.1f} L")
        st.progress(min(wk_water / tgt_water, 1.0) if tgt_water else 0.0)

    with cc3:
        # Convert minutes → hours
        st.metric("Sleep (7d)", f"{wk_sleep/60:.1f} h", f"Goal {tgt_sleep/60:.1f} h")
        st.progress(min(wk_sleep / tgt_sleep, 1.0) if tgt_sleep else 0.0)
else:
    st.info("No recent metrics yet. Use the bot’s /checkin or the **Log Metrics** page to add data.")

st.divider()

# -------------------- Per-meal breakdown (hw_meals) --------------------
st.subheader("Meals")

mres2 = (
    sb.table("hw_meals")
      .select("ts, meal_type, items, calories")
      .eq("uid", uid)
      .gte("ts", start_7d)
      .order("ts")
      .execute()
)
meals = getattr(mres2, "data", []) or []

if not meals:
    st.info("No meals logged yet. Use the bot’s /checkin or the **Log Metrics** page to add meals.")
else:
    mdf = pd.DataFrame(meals)
    mdf["ts"] = pd.to_datetime(mdf["ts"])
    mdf["date"] = mdf["ts"].dt.date

    # ---- Today cards ----
    today = datetime.utcnow().date()
    today_df = mdf[mdf["date"] == today]

    def meal_card(meal):
        sub = today_df[today_df["meal_type"] == meal]
        kcal = int(sub["calories"].fillna(0).sum())
        items = " • ".join(
            x for x in (sub["items"].dropna().astype(str).tolist() or [])
            if x.strip()
        )[:180]
        return kcal, (items if items else "—")

    st.caption("Today")
    cBf, cLu, cDn, cSn = st.columns(4)
    bkcal, bitems = meal_card("breakfast")
    lkcal, litems = meal_card("lunch")
    dkcal, ditems = meal_card("dinner")
    skcal, sitems = meal_card("snacks")

    with cBf:
        st.metric("Breakfast kcal", bkcal); st.caption(bitems)
    with cLu:
        st.metric("Lunch kcal", lkcal); st.caption(litems)
    with cDn:
        st.metric("Dinner kcal", dkcal); st.caption(ditems)
    with cSn:
        st.metric("Snacks kcal", skcal); st.caption(sitems)

    # ---- 7-day calories by meal (stacked vibe) ----
    pivot = (
        mdf.pivot_table(index="date", columns="meal_type",
                        values="calories", aggfunc="sum")
           .fillna(0)
           .sort_index()
    )
    st.markdown("##### Calories by meal (last 7 days)")
    st.bar_chart(pivot)

    # ---- Detailed table ----
    with st.expander("See detailed meal log"):
        show = mdf[["ts", "meal_type", "calories", "items"]].sort_values("ts", ascending=False)
        st.dataframe(show, use_container_width=True)

st.divider()

# -------------------- Goals quick-edit --------------------
st.subheader("Tune your goals")
colA, colB, colC = st.columns(3)

new_steps = colA.slider("Daily steps goal", 3000, 20000, int(GOALS["steps"]), 500)
new_water_glasses_est = max(1, round(int(GOALS["water_ml"]) / 250))
new_water_glasses = colB.slider("Daily hydration goal (glasses)", 4, 20, new_water_glasses_est, 1)
new_sleep = colC.slider("Daily sleep goal (minutes)", 240, 600, int(GOALS["sleep_minutes"]), 15)

if st.button("Save goals"):
    # Store simple goals in profile (re-using 'goals' text for backward compatibility)
    text = f"steps_goal:{int(new_steps)}; water_goal:{int(new_water_glasses)}; sleep_goal:{int(new_sleep)}"
    try:
        sb.table("profiles").upsert({"id": uid, "goals": text}).execute()
        st.success("Goals updated! Nudges will reflect this.")
    except Exception as e:
        st.error(f"Could not save goals: {e}")

st.divider()

# -------------------- Nudge Test (Telegram) --------------------
st.subheader("Nudge Test (Telegram)")
st.caption("Sends a sample message to your linked Telegram chat (reads your telegram_id from the tg_links table).")

link_row = sb.table("tg_links").select("telegram_id, link_code").eq("user_id", uid).maybe_single().execute()
link_data = getattr(link_row, "data", None) or {}
telegram_id = link_data.get("telegram_id")
link_code = link_data.get("link_code")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN") or (
    st.secrets.get("app", {}).get("telegram_token") if "app" in st.secrets else None
)

def _send_telegram_message(chat_id, text):
    if not TELEGRAM_TOKEN:
        return {"ok": False, "error": "Missing TELEGRAM_TOKEN"}
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True}, timeout=10)
        if r.ok:
            return {"ok": True, "resp": r.json()}
        return {"ok": False, "error": f"{r.status_code}: {r.text}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

with st.container():
    test_msg = st.text_input("Message", value="👋 Sample nudge from Health Whisperer! You’re doing great—sip some water?")
    colx, coly = st.columns([1, 2])
    if not telegram_id:
        colx.button("Send to Telegram", disabled=True)
        st.info(
            "No Telegram chat linked yet. Go to **Get Started** and send `/link {code}` to your bot."
            .format(code=link_code or "YOUR-CODE"),
            icon="ℹ️",
        )
    else:
        if colx.button("Send to Telegram"):
            result = _send_telegram_message(telegram_id, test_msg)
            if result.get("ok"):
                st.success("Sent! Check your Telegram.")
            else:
                st.error(f"Failed to send: {result.get('error')}")

st.divider()

# -------------------- Nudge Preview (optional Gemini) --------------------
try:
    import google.generativeai as genai
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") or (
        st.secrets.get("app", {}).get("gemini_api_key") if "app" in st.secrets else None
    )
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-1.5-flash")
        ask = st.text_input("Ask for a nudge (e.g., 'late afternoon slump tips')")
        if st.button("Generate nudge") and ask:
            prompt = f"""
You are Health Whisperer. Give concise, safe wellness suggestions (not medical advice).
User profile: {profile}
User message: {ask}
Return 2 short bullet points with practical next steps (<80 words total).
"""
            out = model.generate_content(prompt)
            st.success(out.text.strip() if hasattr(out, "text") else "I'm here for you.")
    else:
        st.info("Set GEMINI_API_KEY (env) or [app].gemini_api_key (secrets) to enable nudge previews here.")
except Exception as e:
    st.warning(f"Nudge preview unavailable: {e}")
# -------------------- End of Page --------------------
