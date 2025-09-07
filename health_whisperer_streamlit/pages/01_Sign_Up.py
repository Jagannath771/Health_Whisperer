import streamlit as st
from supabase import create_client
import re
from nav import top_nav

st.set_page_config(page_title="Sign Up - Health Whisperer",  layout="wide", initial_sidebar_state="collapsed")
st.markdown("""
<style>
/* hide page links in sidebar */
section[data-testid="stSidebarNav"] { display:none; }
</style>
""", unsafe_allow_html=True)
# st.set_page_config(page_title="Sign Up - Health Whisperer", page_icon="📝")
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
# Replace your old on_sign_out() with this renderer:
def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Sign Up")


st.title("Create your account")

with st.form("signup"):
    full_name = st.text_input("Full name")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    agree = st.checkbox("I agree this is educational only and **not** medical advice.")
    submit = st.form_submit_button("Sign up", type="primary")

if submit:
    email_clean = (email or "").strip().lower()
    if not agree:
        st.error("You must accept the disclaimer to continue.")
    elif not re.match(r"[^@]+@[^@]+\.[^@]+", email_clean):
        st.error("Please enter a valid email.")
    elif len(password) < 8:
        st.error("Password must be at least 8 characters.")
    else:
        try:
            res = sb.auth.sign_up({
                "email": email_clean,
                "password": password,
                "options": {"data": {"full_name": full_name}}
            })
            user = res.user
            # No client insert here (DB trigger or self-heal in profile page will handle it)
            if user:
                st.success("Sign-up successful! Please sign in now.")
                st.page_link("pages/02_Sign_In.py", label="Go to Sign In →", icon="➡️")
            else:
                st.warning("Sign-up initiated. Check your email if confirmation is required.")
        except Exception as e:
            msg = str(e)
            if "already registered" in msg.lower():
                st.info("You already have an account. Please sign in.")
                st.page_link("pages/02_Sign_In.py", label="Go to Sign In →", icon="➡️")
            else:
                st.error(f"Sign-up failed: {e}")
