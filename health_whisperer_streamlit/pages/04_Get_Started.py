import streamlit as st
from supabase import create_client
import secrets
import string
from nav import top_nav
st.set_page_config(page_title="Get Started - Health Whisperer",  layout="wide", initial_sidebar_state="collapsed")
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
top_nav(is_authed, on_sign_out, current="Get Started")


def require_login():
    if "sb_session" not in st.session_state:
        st.warning("Please sign in first.")
        st.switch_page("pages/02_Sign_In.py")

require_login()
user_id = st.session_state["sb_session"]["user_id"]
bot_username = st.secrets["app"].get("bot_username", "HealthWhispererBot")

st.title("Connect to the Telegram Bot")
st.write("Follow these steps to link your Telegram with your Health Whisperer profile.")

# Ensure a link code exists for the user
def get_or_create_link_code(user_id: str) -> str:
    sel = sb.table("tg_links").select("link_code, telegram_id").eq("user_id", user_id).maybe_single().execute()
    data = getattr(sel, "data", None)
    if data and data.get("link_code"):
        return data["link_code"]
    code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
    sb.table("tg_links").upsert({"user_id": user_id, "link_code": code}).execute()
    return code

code = get_or_create_link_code(user_id)

st.markdown(f'''
1. Open Telegram and start a chat with **@{bot_username}** → [t.me/{bot_username}](https://t.me/{bot_username})  
2. Send: `/link {code}` to connect your account.  
3. After linking, simply chat with the bot to receive **personalized nudges**.
''')

st.info("If you change your profile here later, nudges will use the updated info.")
