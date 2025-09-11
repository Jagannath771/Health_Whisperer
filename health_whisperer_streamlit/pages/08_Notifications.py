# pages/08_Notifications.py
import time
from datetime import datetime, timedelta, timezone
import streamlit as st
from supabase import create_client
from httpx import ReadError
from nav import top_nav

# ---------- Page config ----------
st.set_page_config(page_title="Notifications - Health Whisperer",
                   layout="wide",
                   initial_sidebar_state="collapsed")
st.markdown("<style>section[data-testid='stSidebarNav']{display:none;}</style>", unsafe_allow_html=True)

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

@st.cache_resource
def get_sb():
    url = st.secrets["supabase"]["url"]
    key = st.secrets["supabase"]["key"]
    return create_client(url, key)
sb = get_sb()

# ---------- Auth / Nav ----------
def on_sign_out():
    sb.auth.sign_out()
    st.session_state.pop("sb_session", None)

is_authed = "sb_session" in st.session_state
top_nav(is_authed, on_sign_out, current="Notifications")
if not is_authed:
    st.warning("Please sign in first.")
    st.switch_page("pages/02_Sign_In.py")
    st.stop()

uid = st.session_state["sb_session"]["user_id"]

# ---------- Styles (animation + clean cards) ----------
st.markdown("""
<style>
  .notif-wrap { display:grid; gap:12px; }
  .notif {
    border: 1px solid rgba(0,0,0,0.08);
    border-radius: 14px;
    padding: 14px 16px;
    background: linear-gradient(180deg, rgba(250,250,250,0.92), rgba(250,250,250,0.86));
    box-shadow: 0 4px 10px rgba(0,0,0,0.04);
    animation: slideIn .35s ease both;
  }
  .notif.read { opacity: .65; }
  .notif h3 { margin: 0 0 6px 0; }
  .notif .meta { color: #6b7280; font-size: 0.9em; }
  @keyframes slideIn {
    from { transform: translateY(6px); opacity: 0; }
    to   { transform: translateY(0); opacity: 1; }
  }
</style>
""", unsafe_allow_html=True)

st.title("Notifications")

# ---------- Controls ----------
colL, colR = st.columns([1,3])
with colL:
    days = st.selectbox("Show last", [1, 3, 7, 14, 30], index=2)
with colR:
    auto = st.toggle("Auto-refresh every 30s", value=True)
    if auto:
        last = st.session_state.get("notifs_last_refresh", 0.0)
        if time.time() - last > 30:
            st.session_state["notifs_last_refresh"] = time.time()
            st.rerun()

# ---------- Load data ----------
now_u = datetime.now(timezone.utc)
start_u = now_u - timedelta(days=days)

nudges_res = exec_with_retry(
    sb.table("hw_nudges_log")
      .select("*")
      .eq("uid", uid).eq("channel", "inapp")
      .gte("ts", start_u.isoformat())
      .order("ts", desc=True)
)
nudges = nudges_res.data or []

reads_res = exec_with_retry(
    sb.table("hw_inapp_reads").select("nudge_id").eq("uid", uid)
)
read_ids = {row["nudge_id"] for row in (reads_res.data or [])}

# ---------- Render ----------
if not nudges:
    st.info("No notifications yet. Set **Preferences → Channel = inapp** and keep using the app to see personalized nudges.")
else:
    st.markdown('<div class="notif-wrap">', unsafe_allow_html=True)
    for n in nudges:
        nid = n["id"]
        payload = n.get("payload") or {}
        icon = payload.get("icon", "✨")
        title = payload.get("title", "Nudge")
        msg = payload.get("msg", "")
        when = n.get("ts")
        ts_txt = when if isinstance(when, str) else str(when)
        read_cls = " read" if nid in read_ids else ""

        # HTML card for smoother styling
        st.markdown(f"""
        <div class="notif{read_cls}">
          <h3>{icon} {title}</h3>
          <div>{msg}</div>
          <div class="meta">{ts_txt}</div>
        </div>
        """, unsafe_allow_html=True)

        # Action row
        a1, a2 = st.columns([8,2])
        with a2:
            if nid in read_ids:
                st.button("Read ✅", key=f"read_{nid}", disabled=True)
            else:
                if st.button("Mark as read", key=f"btn_{nid}"):
                    exec_with_retry(sb.table("hw_inapp_reads").insert({"uid": uid, "nudge_id": nid}))
                    st.toast("Marked as read")
                    st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)
