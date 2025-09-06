# pages/06_Log_Metrics.py
import streamlit as st
from supabase import create_client
from nav import top_nav

st.set_page_config(page_title="Log Metrics - Health Whisperer",  layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
/* hide page links in sidebar */
section[data-testid="stSidebarNav"] { display:none; }
</style>
""", unsafe_allow_html=True)

# --- Supabase client (same style as other pages) ---
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
        st.switch_page("health_whisperer_streamlit/pages/06_Log_Metrics.py")     # or "06_Log_Metrics.py" on that page
        st.switch_page("pages/02_Sign_In.py")

top_nav(current="Log Metrics", right_slot=render_sign_out)

# --- Require login ---
if "sb_session" not in st.session_state:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]

st.title("Quick Log")

with st.form("metrics"):
    c1, c2, c3 = st.columns(3)
    heart_rate = c1.number_input("Heart rate (bpm)", min_value=30, max_value=220, step=1)
    steps = c2.number_input("Steps today", min_value=0, step=100)
    sleep_minutes = c3.number_input("Sleep last night (min)", min_value=0, max_value=1000, step=10)

    c4, c5, c6 = st.columns(3)
    mood = c4.slider("Mood (1–5)", 1, 5, 3)
    meal_quality = c5.slider("Meal quality (1–5)", 1, 5, 3)
    water_ml = c6.number_input("Water today (ml)", min_value=0, step=100)

    c7, c8 = st.columns(2)
    body_temp = c7.number_input("Body temperature (°F)", min_value=90.0, max_value=110.0, step=0.1, format="%.1f")
    calories = c8.number_input("Calories consumed today", min_value=0, step=50)

    notes = st.text_input("Notes (optional)")

    submitted = st.form_submit_button("Save")
    if submitted:
        try:
            # IMPORTANT: keep zeros — cast directly, no truthy checks
            payload = {
                "uid": uid,
                "source": "manual",
                "heart_rate": int(heart_rate),
                "steps": int(steps),
                "sleep_minutes": int(sleep_minutes),
                "mood": int(mood),
                "meal_quality": int(meal_quality),
                "water_ml": int(water_ml),
                "body_temp": float(body_temp),
                "calories": int(calories),
                "notes": notes or None,
            }
            sb.table("hw_metrics").insert(payload).execute()
            st.success("Saved! Your nudge engine can now use today’s metrics.")
        except Exception as e:
            st.error(f"Could not save metrics: {e}")

# --- AI Meal Logger (free-text) ---
st.divider()
st.subheader("Quick meal log (AI)")

meal_text = st.text_input("What did you eat?", placeholder="e.g., 2 pesarattu with peanut chutney made with olive oil")
ai_col1, ai_col2 = st.columns([1,3])
if ai_col1.button("Parse & Save (AI)", use_container_width=True) and meal_text.strip():
    try:
        from services.nutrition_llm import parse_and_log
        result = parse_and_log(uid, meal_text)
        saved = result["saved"]
        st.success(
            f"Logged {saved.get('calories',0)} kcal. "
            f"P:{saved.get('protein_g',0)} C:{saved.get('carbs_g',0)} F:{saved.get('fat_g',0)}"
        )
        # Tiny confirmation toast
        st.toast("Meal saved ✓", icon="✅")
    except Exception as e:
        st.error(f"Could not parse/log meal: {e}")
