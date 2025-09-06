# nav.py
import streamlit as st

# Map label -> Streamlit page path
PAGES = {
    "Home": "app.py",
    "Sign Up": "pages/01_Sign_Up.py",
    "Sign In": "pages/02_Sign_In.py",
    "My Profile": "pages/03_My_Profile.py",
    "Get Started": "pages/04_Get_Started.py",
    "Dashboard": "pages/05_Dashboard.py",
    "Log Metrics": "pages/06_Log_Metrics.py",   # make sure this file exists
    "Preferences": "pages/07_Preferences.py",   # make sure this file exists
}

def top_nav(current: str = "", right_slot=None):
    """
    Render a sticky top bar with buttons that call st.switch_page().
    - current: label from PAGES to highlight (e.g., "Dashboard")
    - right_slot: optional callable that renders content on the right (e.g., Sign out button)
    """
    st.markdown("""
    <style>
      .hw-topbar { position: sticky; top: 0; z-index: 100; background:#0f1117;
                   padding:10px 12px; border-bottom:1px solid #222; }
      .hw-wrap { display:flex; gap:10px; align-items:center; }
      .hw-spacer { flex: 1 1 auto; }
      .hw-btn { border:1px solid rgba(255,255,255,.08); background:rgba(255,255,255,.02);
                padding:6px 10px; border-radius:10px; font-weight:600; color:#e5e7eb; }
      .hw-btn.active { border-color:rgba(96,165,250,.6); color:#60a5fa; }
      .stButton>button { all: unset; } /* remove default button styling */
    </style>
    <div class="hw-topbar"><div class="hw-wrap">""", unsafe_allow_html=True)

    # left cluster (render as inline buttons that switch pages)
    cols = st.columns([0.12] * len(PAGES) + [1])  # last one is spacer/right area
    # nav.py  (replace the for-loop body)
    for i, (label, path) in enumerate(PAGES.items()):
        with cols[i]:
            is_active = (label == current)
            btn_cls = "hw-btn active" if is_active else "hw-btn"
            # style the actual Streamlit button
            st.markdown(f"<style>div[data-testid='stButton']>button#{'nav_'+label}{{}}</style>", unsafe_allow_html=True)
            if st.button(label, key=f"nav_{label}"):
                if not is_active:
                    st.switch_page(path)
        # remove the extra markdown label you had before


    # right slot (e.g., sign-out button)
    with cols[-1]:
        st.markdown("<div class='hw-spacer'></div>", unsafe_allow_html=True)
        if callable(right_slot):
            right_slot()

    st.markdown("</div></div>", unsafe_allow_html=True)
