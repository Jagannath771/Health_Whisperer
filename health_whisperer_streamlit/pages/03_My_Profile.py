import streamlit as st
from supabase import create_client
import pytz
from nav import top_nav

st.set_page_config(page_title="My Profile - Health Whisperer", layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
/* hide page links in sidebar */
section[data-testid="stSidebarNav"] { display:none; }
</style>
""", unsafe_allow_html=True)
# st.markdown("""
# <style>
# /* center the main block and widen to a nice max */
# .block-container { max-width: 1100px; padding-top: 1rem; padding-bottom: 4rem; }
# /* subtle section divider spacing */
# hr { margin: 1.2rem 0; opacity: .25; }
# </style>
# """, unsafe_allow_html=True)


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
        st.switch_page("health_whisperer_streamlit/pages/03_My_Profile.py")    # or "06_Log_Metrics.py" on that page
        st.switch_page("health_whisperer_streamlit/pages/02_Sign_In.py")


top_nav(current="My Profile", right_slot=render_sign_out)

# pages/03_My_Profile.py  (guard fix)
def require_login():
    if "sb_session" not in st.session_state:
        st.warning("Please sign in first.")
        st.switch_page("pages/02_Sign_In.py")
        st.stop()  # ⬅️ important

require_login()

user_id = st.session_state["sb_session"]["user_id"]
user_email = st.session_state["sb_session"]["email"]

st.title("My Profile")

# 1) Try to get the profile without throwing on 0 rows
res = sb.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
row = getattr(res, "data", None)

# 2) Self-heal: if missing, create a skeleton row (RLS policy 'insert own' must exist)
if not row:
    try:
        sb.table("profiles").insert({
            "id": user_id,
            "email": user_email,
            "full_name": ""
        }).execute()
        # fetch again
        res = sb.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
        row = getattr(res, "data", None)
    except Exception as e:
        st.error(f"Couldn't create your profile record automatically: {e}")
        st.stop()

# Safe defaults
row = row or {}
with st.form("profile"):
    full_name = st.text_input("Full name", value=row.get("full_name", ""))
    age = st.number_input("Age", min_value=0, max_value=120, value=int(row.get("age") or 0))
    gender = st.selectbox("Gender", ["Prefer not to say", "Female", "Male", "Non-binary", "Other"],
                          index=0 if not row.get("gender") else
                          ["Prefer not to say", "Female", "Male", "Non-binary", "Other"].index(row.get("gender","Prefer not to say")))
    height_cm = st.number_input("Height (cm)", min_value=0.0, max_value=300.0, value=float(row.get("height_cm") or 0.0))
    weight_kg = st.number_input("Weight (kg)", min_value=0.0, max_value=500.0, value=float(row.get("weight_kg") or 0.0))
    activity_level = st.selectbox("Activity level",
        ["Sedentary", "Lightly active", "Moderately active", "Very active", "Athlete"],
        index=0 if not row.get("activity_level") else
        ["Sedentary", "Lightly active", "Moderately active", "Very active", "Athlete"].index(row.get("activity_level","Sedentary")))
    goals = st.text_area("Goals", value=row.get("goals",""))
    conditions = st.text_area("Conditions (optional)", value=row.get("conditions",""),
                              help="Only share what you're comfortable with.")
    medications = st.text_area("Medications/Supplements (optional)", value=row.get("medications",""))
    import pytz
    tz_list = pytz.all_timezones
    tz_value = row.get("timezone") or ("America/New_York" if "America/New_York" in tz_list else tz_list[0])
    timezone = st.selectbox("Your time zone", tz_list, index=tz_list.index(tz_value))

    save = st.form_submit_button("Save profile", type="primary")

if save:
    payload = {
        "id": user_id,
        "email": user_email,
        "full_name": full_name,
        "age": int(age),
        "gender": gender,
        "height_cm": float(height_cm),
        "weight_kg": float(weight_kg),
        "activity_level": activity_level,
        "goals": goals,
        "conditions": conditions,
        "medications": medications,
        "timezone": timezone
    }
    try:
        sb.table("profiles").upsert(payload).execute()
        st.success("Profile saved!")
        st.page_link("pages/04_Get_Started.py", label="Next: Connect Telegram →", icon="➡️")
    except Exception as e:
        st.error(f"Could not save profile: {e}")
