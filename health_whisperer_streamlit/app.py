# app.py
from dotenv import load_dotenv
load_dotenv()
import streamlit as st
from supabase import create_client
from nav import top_nav
st.set_page_config(page_title="Health Whisperer", page_icon="💬",  layout="wide", initial_sidebar_state="collapsed")
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


# --- Supabase ---
@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)
sb = get_sb()

# --- Top Nav (paste this on every page) ---
# def top_nav():
#     left, mid, right = st.columns([1,2,2])
#     with left:
#         st.page_link("app.py", label="🏠 Home")
#     with mid:
#         st.page_link("pages/01_Sign_Up.py", label="📝 Sign Up")
#         st.page_link("pages/02_Sign_In.py", label="🔐 Sign In")
#         st.page_link("pages/03_My_Profile.py", label="🧩 My Profile")
#         st.page_link("pages/05_Dashboard.py", label="📊 Dashboard")
#         st.page_link("pages/04_Get_Started.py", label="🚀 Get Started")
#     with right:
#         if "sb_session" in st.session_state:
#             if st.button("Sign out"):
#                 sb.auth.sign_out()
#                 st.session_state.pop("sb_session", None)
#                 st.switch_page("app.py")



# Replace your old on_sign_out() with this renderer:
def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Home")



# --- Dynamic hero (changes if signed in) ---
name = None
if "sb_session" in st.session_state:
    uid = st.session_state["sb_session"]["user_id"]
    r = sb.table("profiles").select("full_name").eq("id", uid).maybe_single().execute()
    name = (getattr(r, "data", {}) or {}).get("full_name") or None

headline = f"Welcome back{', ' + name if name else ''} 👋"
sub = "Your personal AI health companion. Turn scattered data into **timely, caring nudges** delivered on Telegram."

st.markdown(f"""
<div style="
  background: radial-gradient(1000px 600px at 5% 0%, rgba(110,231,183,0.20), transparent 60%),
              radial-gradient(600px 400px at 95% 20%, rgba(96,165,250,0.15), transparent 60%);
  border: 1px solid rgba(255,255,255,0.06);
  padding: 48px; border-radius: 20px; margin-top: 8px;">
  <h1 style="margin:0 0 8px 0; font-size:40px;">Health Whisperer</h1>
  <h3 style="margin:0 0 4px 0; font-weight:500;">{headline}</h3>
  <p style="margin:0; font-size:18px; opacity:0.9;">{sub}</p>
</div>
""", unsafe_allow_html=True)
st.info("⚠️ Educational only — not medical advice.", icon="⚠️")

# --- Cards (change CTA if signed in) ---
c1, c2, c3 = st.columns(3)
with c1:
    st.markdown("### 📝 Create Account" if not name else "### 🧩 Update Profile")
    st.write("Start your journey in under a minute." if not name else "Keep your details fresh for better nudges.")
    st.page_link("pages/01_Sign_Up.py" if not name else "pages/03_My_Profile.py",
                 label="Sign Up →" if not name else "My Profile →", icon="📝" if not name else "🧩")
with c2:
    st.markdown("### 🚀 Connect Telegram")
    st.write("Get personalized nudges when they matter.")
    st.page_link("pages/04_Get_Started.py", label="Get Started →", icon="🚀")
with c3:
    st.markdown("### 📊 Dashboard")
    st.write("See insights at a glance.")
    st.page_link("pages/05_Dashboard.py", label="Open Dashboard →", icon="📊")

st.divider()
st.divider()
n1, n2 = st.columns(2)
with n1:
    st.markdown("### 🔔 Notifications")
    st.write("See your in-app nudges and mark them as read.")
    st.page_link("pages/08_Notifications.py", label="Open Notifications →", icon="🔔")
p, s = st.columns(2)
with p:
    st.subheader("The Problem")
    st.write(
        "- Health info lives in separate apps and devices.\n"
        "- Insights arrive after the moment passed.\n"
        "- Consistency is hard without timely cues."
    )
with s:
    st.subheader("Our Solution")
    st.write(
        "- One private profile capturing your context.\n"
        "- **Context-aware nudges** on Telegram.\n"
        "- You control what you share."
    )

st.divider()
st.caption("© 2025 Health Whisperer — For education only, not a medical device.")
