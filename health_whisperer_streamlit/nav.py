import streamlit as st

def _render_links(current_key: str):
    st.page_link("app.py", label="🏠 Home")
    st.page_link("pages/01_Sign_Up.py", label="📝 Sign Up")
    st.page_link("pages/02_Sign_In.py", label="🔐 Sign In")
    st.page_link("pages/03_My_Profile.py", label="🧩 My Profile")
    st.page_link("pages/05_Dashboard.py", label="📊 Dashboard")
    st.page_link("pages/04_Get_Started.py", label="🚀 Get Started")
    st.page_link("pages/06_Log_Metrics.py", label="📝 Log Metrics")
    st.page_link("pages/07_Preferences.py", label="⚙️ Preferences")
    st.page_link("pages/08_Notifications.py", label="🔔 Notifications")  # ← NEW

def _default_signout():
    pass

def top_nav(*args, **kwargs):
    is_authed = False
    current = ""
    on_sign_out = None
    right_slot = None
    if "current" in kwargs: current = kwargs.get("current") or ""
    if "on_sign_out" in kwargs: on_sign_out = kwargs.get("on_sign_out")
    if "right_slot" in kwargs: right_slot = kwargs.get("right_slot")
    if "is_authed" in kwargs: is_authed = bool(kwargs.get("is_authed"))
    if args:
        if isinstance(args[0], bool):
            is_authed = args[0]
            if len(args) >= 2 and callable(args[1]): on_sign_out = args[1]
            if len(args) >= 3 and isinstance(args[2], str): current = args[2]
        elif isinstance(args[0], str):
            current = args[0]
            if len(args) >= 2 and callable(args[1]): right_slot = args[1]
    on_sign_out = on_sign_out or right_slot or _default_signout

    st.markdown("""
    <style>
      section[data-testid="stSidebarNav"] { display:none; }
      .hw-bar-wrap { position: sticky; top: 0; z-index: 999; background: rgba(255,255,255,0.75);
                     -webkit-backdrop-filter: blur(8px); backdrop-filter: blur(8px);
                     border-bottom: 1px solid rgba(0,0,0,0.05); }
      .hw-bar { display:flex; gap:12px; align-items:center; padding: 10px 4px; overflow-x:auto; }
      .block-container { padding-top: 8px; }
      /* Make buttons in the header look compact */
      .hw-bar button { margin-left: auto; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="hw-bar-wrap">', unsafe_allow_html=True)
    col1, col2 = st.columns([8,2])
    with col1:
        _render_links(current_key=current)
    with col2:
        if is_authed:
            if st.button("Sign out", use_container_width=True):
                try:
                    on_sign_out()
                finally:
                    st.switch_page("app.py")
    st.markdown('</div>', unsafe_allow_html=True)
