import streamlit as st
from supabase import create_client
from nav import top_nav
st.set_page_config(page_title="Sign In - Health Whisperer",  layout="wide", initial_sidebar_state="collapsed")
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

def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Sign In")


st.title("Welcome back")
with st.form("signin"):
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")
    submit = st.form_submit_button("Sign in", type="primary")

if submit:
    try:
        res = sb.auth.sign_in_with_password({"email": email, "password": password})
        if res.session:
            st.session_state["sb_session"] = {
                "access_token": res.session.access_token,
                "refresh_token": res.session.refresh_token,
                "user_id": res.user.id,
                "email": email
            }
            st.success("Signed in!")
            st.switch_page("pages/03_My_Profile.py")
        else:
            st.error("No session returned. Check your credentials.")
    except Exception as e:
        st.error(f"Sign-in failed: {e}")

st.page_link("pages/01_Sign_Up.py", label="Need an account? Sign up", icon="📝")
