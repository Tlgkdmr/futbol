from __future__ import annotations

import io
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

# Sayfa Yapılandırması
st.set_page_config(
    page_title="FREKANS X - Profesyonel Oran Analizi",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Profesyonel Koyu Lacivert Tema & Turkuaz / Mavi Detaylar
st.markdown(
    """
    <style>
    .main {
        background-color: #0b131d;
        color: #e2e8f0;
    }
    .sidebar .sidebar-content {
   w     background-color: #
