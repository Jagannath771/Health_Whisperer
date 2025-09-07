# nav.py
import streamlit as st

def _render_links(current_key: str):
    # Order: Home → Sign Up/Sign In → Profile → Dashboard → Log Metrics → Preferences → Get Started
    st.page_link("app.py", label="🏠 Home")
    st.page_link("pages/01_Sign_Up.py", label="📝 Sign Up")
    st.page_link("pages/02_Sign_In.py", label="🔐 Sign In")
    st.page_link("pages/03_My_Profile.py", label="🧩 My Profile")
    st.page_link("pages/05_Dashboard.py", label="📊 Dashboard")
    st.page_link("pages/06_Log_Metrics.py", label="📝 Log Metrics")         # ← added
    st.page_link("pages/07_Preferences.py", label="⚙️ Preferences")         # ← added
    st.page_link("pages/04_Get_Started.py", label="🚀 Get Started")

def _default_signout():
    pass

def top_nav(*args, **kwargs):
    """
    Back-compat shim supporting both of your call styles:

    A) top_nav(is_authed: bool, on_sign_out: callable, current: str = "")
    B) top_nav(current: str = "", right_slot: callable = None)
    """
    # ---- Parse inputs flexibly ----
    is_authed = False
    current = ""
    on_sign_out = None
    right_slot = None

    # kwargs first
    if "current" in kwargs: current = kwargs.get("current") or ""
    if "on_sign_out" in kwargs: on_sign_out = kwargs.get("on_sign_out")
    if "right_slot" in kwargs: right_slot = kwargs.get("right_slot")
    if "is_authed" in kwargs: is_authed = bool(kwargs.get("is_authed"))

    # positional inference
    if args:
        if isinstance(args[0], bool):                   # style A
            is_authed = args[0]
            if len(args) >= 2 and callable(args[1]): on_sign_out = args[1]
            if len(args) >= 3 and isinstance(args[2], str): current = args[2]
        elif isinstance(args[0], str):                  # style B
            current = args[0]
            if len(args) >= 2 and callable(args[1]): right_slot = args[1]

    on_sign_out = on_sign_out or right_slot or _default_signout

    st.markdown(
        """
        <style>
          section[data-testid="stSidebarNav"] { display:none; }
          .hw-bar { display:flex; gap:12px; align-items:center; margin:6px 0 16px; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([8, 2])
    with col1:
        _render_links(current_key=current)
    with col2:
        if is_authed:
            if st.button("Sign out", use_container_width=True):
                try:
                    on_sign_out()
                finally:
                    st.switch_page("app.py")
