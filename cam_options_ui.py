"""NestoCAM options: the settings that belong to all operations (and all projects), not to one operation. For now the tool library;
more settings will be added here as tabs."""
from __future__ import annotations

import streamlit as st


def cam_options_page(db):
    """The CAM options screen: a Back button and one tab for every group of global settings."""
    from tools_ui import tool_library

    def _back():
        st.session_state.cam_options_open = False

    st.button("← Back to the operations", on_click=_back, key="cam_options_back")
    st.subheader("CAM options")
    st.caption("Settings for all CAM operations. They are shared by every operation and every project.")
    tabs = st.tabs(["Tools"])
    with tabs[0]:
        tool_library(db)
