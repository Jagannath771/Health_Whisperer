# pages/06_Log_Metrics.py
import json, time
import streamlit as st
from supabase import create_client
from httpx import ReadError
from datetime import datetime, timedelta, timezone, date
from zoneinfo import ZoneInfo
from nav import top_nav

# ---------- Page config ----------
st.set_page_config(page_title="Log Metrics - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>section[data-testid='stSidebarNav']{display:none;}</style>", unsafe_allow_html=True)

# ---------- Retry helper (handles Windows non-blocking socket error) ----------
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

# ---------- Supabase client ----------
@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)
sb = get_sb()

# ---------- Navbar / Auth ----------
def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Log Metrics")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]

# ---------- Helpers ----------
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

def _fmt_ts(ts_iso: str | None) -> str:
    if not ts_iso: return "—"
    try:
        tz = _user_tz(uid)
        return (datetime.fromisoformat(ts_iso.replace("Z","+00:00"))
                .astimezone(tz).strftime("%b %d, %Y • %I:%M %p"))
    except Exception:
        return ts_iso

def _safe_items_list(items_field):
    if items_field is None: return []
    if isinstance(items_field, list): return items_field
    if isinstance(items_field, dict): return [items_field]
    if isinstance(items_field, str):
        try: return json.loads(items_field)
        except: return [{"name": items_field}]
    return [{"name": str(items_field)}]

def meals_today(uid: str):
    tz = _user_tz(uid)
    start_l = datetime.combine(_today(uid), datetime.min.time(), tzinfo=tz)
    end_l   = start_l + timedelta(days=1)
    req = (sb.table("hw_meals").select("*")
           .eq("uid", uid)
           .gte("ts", start_l.astimezone(timezone.utc).isoformat())
           .lt("ts",  end_l.astimezone(timezone.utc).isoformat())
           .order("ts", desc=True))
    r = exec_with_retry(req)
    return r.data or []

def get_prefs(uid: str) -> dict:
    try:
        r = exec_with_retry(sb.table("hw_preferences").select("*").eq("uid", uid).maybe_single())
        return r.data or {}
    except Exception:
        return {}

def get_today_manual(uid: str) -> dict:
    today = _today(uid).isoformat()
    req = (sb.table("hw_metrics").select("*")
           .eq("uid", uid).eq("source","manual").eq("log_date", today)
           .limit(1))
    r = exec_with_retry(req)
    return (r.data[0] if r.data else {}) or {}

# ---------- Load state ----------
prefs = get_prefs(uid)
today_row = get_today_manual(uid)
meals = meals_today(uid)

# ---------- KPIs ----------
kcal_goal  = int(prefs.get("daily_calorie_goal") or 2000)
water_goal = int(prefs.get("daily_water_ml") or 2000)
steps_goal = int(prefs.get("daily_step_goal") or 8000)
sleep_goal = int(prefs.get("sleep_goal_min") or 420)

kcal_today  = sum(int(m.get("calories") or 0) for m in meals)
water_today = int(today_row.get("water_ml") or 0)
steps_today = int(today_row.get("steps") or 0)
sleep_today = int(today_row.get("sleep_minutes") or 0)
mood_today  = int(today_row.get("mood") or 0)

cA,cB,cC,cD,cE = st.columns(5)
cA.metric("Calories", f"{kcal_today}/{kcal_goal}")
cB.metric("Water",    f"{water_today}/{water_goal} ml")
cC.metric("Steps",    f"{steps_today}/{steps_goal}")
cD.metric("Sleep",    f"{sleep_today}/{sleep_goal} min")
cE.metric("Mood",     f"{mood_today or '—'}")

st.divider()

# ---------- DAILY METRICS FORM ----------
with st.form("metrics"):
    c1,c2,c3 = st.columns(3)
    heart_rate = c1.number_input("Heart rate (bpm)", 30, 220, int(today_row.get("heart_rate") or 70), 1)
    steps_in   = c2.number_input("Steps today", 0, value=steps_today, step=100)
    sleep_min  = c3.number_input("Sleep last night (min)", 0, 1000, int(today_row.get("sleep_minutes") or 0), 10)
    c4,c5,c6 = st.columns(3)
    mood_in = c4.slider("Mood (1–5)", 1, 5, int(today_row.get("mood") or 3))
    meal_q  = c5.slider("Meal quality (1–5)", 1, 5, int(today_row.get("meal_quality") or 3))
    water   = c6.number_input("Water today (ml)", 0, value=water_today, step=100)
    c7,c8 = st.columns(2)
    temp_f   = c7.number_input("Body temperature (°F)", 90.0, 110.0, float(today_row.get("body_temp") or 98.6), 0.1, format="%.1f")
    calories = c8.number_input("Calories (manual add-ons)", 0, value=int(today_row.get("calories") or 0), step=50)
    notes = st.text_input("Notes (optional)", value=today_row.get("notes") or "")
    submitted = st.form_submit_button("Save")

if submitted:
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        today_d = _today(uid).isoformat()
        payload = {
            "uid": uid, "source": "manual", "log_date": today_d, "ts": now_iso,
            "heart_rate": int(heart_rate),
            "steps": int(steps_in),
            "sleep_minutes": int(sleep_min),
            "mood": int(mood_in),
            "meal_quality": int(meal_q),
            "water_ml": int(water),
            "body_temp": float(temp_f),
            "calories": int(calories),
            "notes": notes or None,
        }
        exec_with_retry(sb.table("hw_metrics").upsert(payload, on_conflict="uid,log_date,source"))

        # 🔔 Emit event so the worker can send an *instant* nudge
        exec_with_retry(sb.table("hw_events").insert({
            "uid": uid,
            "kind": "metrics_saved",
            "ts": now_iso,
            "processed": False,
            "payload": {
                "steps": int(steps_in),
                "water_ml": int(water),
                "heart_rate": int(heart_rate),
                "body_temp": float(temp_f),
            }
        }))

        st.success("Saved!")
        st.rerun()
    except Exception as e:
        st.error(f"Could not save metrics: {e}")

st.divider()

# ---------- QUICK MEAL (AI PARSE) ----------
st.subheader("Quick meal (AI parse)")
meal_type_hint = st.selectbox("Type (optional)", ["auto","breakfast","lunch","snacks","dinner"], index=0)
meal_text = st.text_area("What did you eat?", placeholder="e.g., 1 dosa with chutney, 200 ml coffee with milk")

if st.button("Log meal with AI"):
    if not meal_text.strip():
        st.warning("Please type what you ate.")
    else:
        try:
            # uses your existing service; parses to items with qty_g + totals; saves + emits event
            from services.nutrition_llm import parse_and_log
            hint = None if meal_type_hint == "auto" else meal_type_hint
            out = parse_and_log(uid, meal_text.strip(), meal_type_hint=hint)  # emits meal_logged for RT nudge.  # :contentReference[oaicite:7]{index=7}
            saved = out["saved"]
            st.success(f"Logged {saved.get('meal_type','unknown')} • {saved.get('calories',0)} kcal • P{saved.get('protein_g',0)}/C{saved.get('carbs_g',0)}/F{saved.get('fat_g',0)}")
            st.rerun()
        except Exception as e:
            st.error(f"Could not parse/log meal: {e}")

# ---------- TODAY'S MEALS ----------
st.divider()
st.subheader("Today’s meals")
meals = meals_today(uid)
groups = {"breakfast":[],"lunch":[],"snacks":[],"dinner":[],"unknown":[]}
for m in meals:
    mt = (m.get("meal_type") or "unknown").lower()
    groups.get(mt if mt in groups else "unknown").append(m)

for mt in ["breakfast","lunch","snacks","dinner","unknown"]:
    rows = groups[mt]
    if not rows: continue
    with st.expander(mt.capitalize(), expanded=False):
        for m in rows:
            ts_local = _fmt_ts(m.get("ts"))
            kcal = int(m.get("calories") or 0)
            macro = f"P:{m.get('protein_g',0)} C:{m.get('carbs_g',0)} F:{m.get('fat_g',0)}"
            fiber = m.get("fiber_g")
            fiber_txt = f" • Fiber:{fiber}" if fiber not in (None,0,"") else ""
            st.markdown(f"**{ts_local}** — **{kcal} kcal** ({macro}){fiber_txt}")
            for it in _safe_items_list(m.get("items")):
                name = it.get("name") if isinstance(it, dict) else str(it)
                qty  = (it.get("qty_g") if isinstance(it, dict) else None)
                note = (it.get("notes") if isinstance(it, dict) else None)
                qtxt = f" ({int(qty)} g)" if qty not in (None, "", 0) else ""
                ntxt = f" — {note}" if note else ""
                st.write(f"- {name}{qtxt}{ntxt}")
