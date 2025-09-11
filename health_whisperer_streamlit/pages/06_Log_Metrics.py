# pages/06_Log_Metrics.py
import json, time, random
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
st.markdown("""
<style>
  section[data-testid='stSidebarNav']{display:none;}
  .chip { display:inline-block; padding:.25rem .6rem; border-radius:999px; border:1px solid rgba(0,0,0,.1); }
  .soft { background: linear-gradient(180deg, rgba(250,250,250,.95), rgba(245,245,245,.9)); border:1px solid rgba(0,0,0,.06); border-radius: 12px; padding: 12px 14px; }
</style>
""", unsafe_allow_html=True)

# ---------- Retry helper ----------
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

# ---------- KPI targets ----------
kcal_goal  = int(prefs.get("daily_calorie_goal") or 2000)
water_goal = int(prefs.get("daily_water_ml") or 2000)
steps_goal = int(prefs.get("daily_step_goal") or 8000)
sleep_goal = int(prefs.get("sleep_goal_min") or 420)

# ---------- Today totals ----------
kcal_today  = sum(int(m.get("calories") or 0) for m in meals)
water_today = int(today_row.get("water_ml") or 0)
steps_today = int(today_row.get("steps") or 0)
sleep_today = int(today_row.get("sleep_minutes") or 0)
mood_today  = int(today_row.get("mood") or 0)

a,b,c,d,e = st.columns(5)
a.metric("Calories", f"{kcal_today}/{kcal_goal}")
b.metric("Water",    f"{water_today}/{water_goal} ml")
c.metric("Steps",    f"{steps_today}/{steps_goal}")
d.metric("Sleep",    f"{sleep_today}/{sleep_goal} min")
e.metric("Mood",     f"{mood_today}" if mood_today else "—")

st.divider()

# ============================================================
#  A) DAILY METRICS + NUTRITION TOTALS + MENTAL HEALTH
# ============================================================
st.subheader("Daily health, nutrition & mental well-being")

with st.form("metrics"):
    # Health basics
    c1,c2,c3 = st.columns(3)
    heart_rate = c1.number_input("Heart rate (bpm)", 30, 220, int(today_row.get("heart_rate") or 70), 1)
    steps_in   = c2.number_input("Steps today", 0, value=steps_today, step=100)
    sleep_min  = c3.number_input("Sleep last night (min)", 0, 1000, int(today_row.get("sleep_minutes") or 0), 10)

    c4,c5,c6 = st.columns(3)
    mood_in   = c4.slider("Mood (1–5)", 1, 5, int(today_row.get("mood") or 3))
    meal_q    = c5.slider("Meal quality (1–5)", 1, 5, int(today_row.get("meal_quality") or 3))
    water     = c6.number_input("Water today (ml)", 0, value=water_today, step=100)

    # Nutrition totals
    n1, n2, n3 = st.columns(3)
    protein_g  = n1.number_input("Protein (g, total)", 0, 500, int(today_row.get("protein_g") or 0), 1)
    carbs_g    = n2.number_input("Carbs (g, total)",   0, 800, int(today_row.get("carbs_g") or 0), 1)
    fat_g      = n3.number_input("Fat (g, total)",     0, 300, int(today_row.get("fat_g") or 0), 1)

    n4, n5, n6 = st.columns(3)
    fiber_g    = n4.number_input("Fiber (g)",      0, 200, int(today_row.get("fiber_g") or 0), 1)
    sugar_g    = n5.number_input("Added sugar (g)",0, 300, int(today_row.get("sugar_g") or 0), 1)
    sodium_mg  = n6.number_input("Sodium (mg)",    0, 8000, int(today_row.get("sodium_mg") or 0), 50)

    calories   = st.number_input("Calories (manual add-ons)", 0, value=int(today_row.get("calories") or 0), step=50)

    # --- Mental health (all inside form, NO st.button here) ---
    st.markdown("#### 🧠 Mental well-being")
    m1, m2 = st.columns([2,3])
    stress = m1.slider("Stress level (1–5)", 1, 5, int(today_row.get("stress_level") or 3))
    with m2:
        st.caption("Stress meter")
        prog = int((stress/5)*100)
        st.progress(prog)
        if stress >= 4:
            st.info("High stress — try the breathing exercise below after saving.")
        elif stress == 3:
            st.info("Moderate stress — a short walk or tea may help.")
        else:
            st.success("Nice — keep that calm going ✨")

    prompts = [
        "What was the highlight of your day?",
        "One thing you’re grateful for:",
        "A small win you had today:",
        "A challenge you want to reflect on:",
    ]
    journaling_prompt = random.choice(prompts)
    st.caption(f"Reflection prompt: *{journaling_prompt}*")
    journal_text = st.text_area("Journal", value=today_row.get("journal") or "", placeholder="Write freely...")

    hobbies = st.multiselect(
        "Hobbies that recharged you today",
        ["Reading", "Music", "Walking", "Meditation", "Sports", "Art", "Gaming", "Family time"],
        default=today_row.get("hobbies") or []
    )

    notes = st.text_input("Other notes (optional)", value=today_row.get("notes") or "")
    submitted_metrics = st.form_submit_button("Save")

if submitted_metrics:
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
            "protein_g": int(protein_g),
            "carbs_g": int(carbs_g),
            "fat_g": int(fat_g),
            "fiber_g": int(fiber_g),
            "sugar_g": int(sugar_g),
            "sodium_mg": int(sodium_mg),
            "calories": int(calories),
            # Mental health
            "stress_level": int(stress),
            "journal": journal_text or None,
            "hobbies": hobbies or [],
            "notes": notes or None,
        }
        exec_with_retry(sb.table("hw_metrics").upsert(payload, on_conflict="uid,log_date,source"))

        # 🔔 Emit event so the worker can send an instant nudge (includes mood/stress context)
        exec_with_retry(sb.table("hw_events").insert({
            "uid": uid,
            "kind": "metrics_saved",
            "ts": now_iso,
            "processed": False,
            "payload": {
                "steps": int(steps_in),
                "water_ml": int(water),
                "heart_rate": int(heart_rate),
                "mood": int(mood_in),
                "stress_level": int(stress),
            }
        }))

        st.success("Daily metrics saved!")
        st.rerun()
    except Exception as e:
        st.error(f"Could not save metrics: {e}")

st.divider()

# ============================================================
#  B) WELLNESS ACTIONS (outside form; safe to use st.button)
# ============================================================
st.subheader("Quick wellness actions")
wa1, wa2 = st.columns(2)
with wa1:
    if st.button("🌬️ 60-sec box breathing"):
        st.info("Inhale 4s • Hold 4s • Exhale 4s • Hold 4s — repeat 8 cycles.")
with wa2:
    if st.button("🌤️ Write 3 good things"):
        prev = st.session_state.get("journal_prefill", "")
        st.session_state["journal_prefill"] = (prev + "\n• ") if prev else "• "
        st.toast("Added a bullet starter. Scroll up to Journal to continue.")

# If user clicked "3 good things", prefill journal box on next render (non-destructive)
if "journal_prefill" in st.session_state and st.session_state["journal_prefill"]:
    st.caption("Tip: Paste these bullets into your Journal above:")
    st.code(st.session_state["journal_prefill"])
    # do not clear automatically; user can use it as reference

# ============================================================
#  C) QUICK MEAL (AI PARSER)
# ============================================================
st.divider()
st.subheader("Quick meal (AI parse)")

meal_type_hint = st.selectbox("Type (optional)", ["auto","breakfast","lunch","snacks","dinner"], index=0)
meal_text = st.text_area("What did you eat?", placeholder="e.g., 1 dosa with chutney, 200 ml coffee with milk")

if st.button("Log meal with AI"):
    if not meal_text.strip():
        st.warning("Please type what you ate.")
    else:
        try:
            from services.nutrition_llm import parse_and_log  # must exist in your project
            hint = None if meal_type_hint == "auto" else meal_type_hint
            out = parse_and_log(uid, meal_text.strip(), meal_type_hint=hint)  # creates hw_meals + emits meal_logged
            saved = out.get("saved", {})
            st.success(
                f"Logged {saved.get('meal_type','unknown')} • {saved.get('calories',0)} kcal • "
                f"P{saved.get('protein_g',0)}/C{saved.get('carbs_g',0)}/F{saved.get('fat_g',0)}"
            )
            st.rerun()
        except Exception as e:
            st.error(f"Could not parse/log meal: {e}")

# ============================================================
#  D) MANUAL MEAL (optional)
# ============================================================
with st.expander("Log a meal manually (optional)"):
    mc1, mc2, mc3 = st.columns(3)
    manual_type = mc1.selectbox("Meal type", ["breakfast","lunch","snacks","dinner","unknown"], index=2)
    m_calories  = mc2.number_input("Calories (kcal)", 0, 3000, 0, 10)
    m_protein   = mc3.number_input("Protein (g)", 0, 300, 0, 1)
    md1, md2, md3 = st.columns(3)
    m_carbs     = md1.number_input("Carbs (g)", 0, 500, 0, 1)
    m_fat       = md2.number_input("Fat (g)",   0, 200, 0, 1)
    m_fiber     = md3.number_input("Fiber (g)", 0, 100, 0, 1)
    md4, md5 = st.columns(2)
    m_sugar     = md4.number_input("Added sugar (g)", 0, 200, 0, 1)
    m_sodium    = md5.number_input("Sodium (mg)",     0, 8000, 0, 50)
    items_text = st.text_area("Items (optional JSON or simple lines)", placeholder='e.g., [{"name":"dosa","qty_g":80},{"name":"chutney","qty_g":40}]')
    save_manual = st.button("Save manual meal")

    if save_manual:
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            items_payload = None
            t = items_text.strip()
            if t:
                try:
                    items_payload = json.loads(t)
                    if not isinstance(items_payload, list): items_payload = [items_payload]
                except Exception:
                    items_payload = [{"name": line.strip()} for line in t.splitlines() if line.strip()]
            payload = {
                "uid": uid, "ts": now_iso, "meal_type": manual_type,
                "calories": int(m_calories), "protein_g": int(m_protein),
                "carbs_g": int(m_carbs), "fat_g": int(m_fat), "fiber_g": int(m_fiber),
                "sugar_g": int(m_sugar), "sodium_mg": int(m_sodium),
                "items": items_payload or [], "raw_text": None,
            }
            exec_with_retry(sb.table("hw_meals").insert(payload))
            exec_with_retry(sb.table("hw_events").insert({
                "uid": uid, "kind": "meal_logged", "ts": now_iso, "processed": False,
                "payload": {"meal_type": manual_type, "calories": int(m_calories)}
            }))
            st.success("Meal saved!")
            st.rerun()
        except Exception as e:
            st.error(f"Could not save meal: {e}")

# ============================================================
#  E) TODAY’S MEALS
# ============================================================
st.divider()
st.subheader("Today’s meals")
meals = meals_today(uid)
groups = {"breakfast":[],"lunch":[],"snacks":[],"dinner":[],"unknown":[]}
for m in meals:
    mt = (m.get("meal_type") or "unknown").lower()
    groups.get(mt if mt in groups else "unknown").append(m)

for mt in ["breakfast","lunch","snacks","dinner","unknown"]:
    rows = groups[mt]
    if not rows:
        continue
    with st.expander(mt.capitalize(), expanded=False):
        for m in rows:
            ts_local = _fmt_ts(m.get("ts"))
            kcal = int(m.get("calories") or 0)
            p = int(m.get("protein_g") or 0)
            c = int(m.get("carbs_g") or 0)
            f = int(m.get("fat_g") or 0)
            fiber = m.get("fiber_g")
            fiber_txt = f" • Fiber:{int(fiber)}" if fiber not in (None,0,"") else ""
            sugar = m.get("sugar_g")
            sugar_txt = f" • Sugar:{int(sugar)}" if sugar not in (None,0,"") else ""
            sod = m.get("sodium_mg")
            sod_txt = f" • Na:{int(sod)} mg" if sod not in (None,0,"") else ""
            st.markdown(f"**{ts_local}** — **{kcal} kcal** (P:{p} C:{c} F:{f}){fiber_txt}{sugar_txt}{sod_txt}")
            for it in _safe_items_list(m.get("items")):
                name = it.get("name") if isinstance(it, dict) else str(it)
                qty  = (it.get("qty_g") if isinstance(it, dict) else None)
                note = (it.get("notes") if isinstance(it, dict) else None)
                qtxt = f" ({int(qty)} g)" if qty not in (None, "", 0) else ""
                ntxt = f" — {note}" if note else ""
                st.write(f"- {name}{qtxt}{ntxt}")
