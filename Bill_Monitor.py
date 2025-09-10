# app.py — Single-file Streamlit dashboard with manual add + keyword search + party Waffle
import os
import re
import requests
import pandas as pd
import streamlit as st
import plotly.express as px
import matplotlib.pyplot as plt

try:
    from pywaffle import Waffle
    WAFFLE_AVAILABLE = True
except Exception:
    WAFFLE_AVAILABLE = False

# -------------------------
# Config
# -------------------------
st.set_page_config(layout="wide", page_title="Bills Tracker")

API_KEY = os.getenv("LEGISCAN_API_KEY", "9a70e5bd23669a67bc73afda549f698f")
BASE_URL = "https://api.legiscan.com/"

STATE_ABBR = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
    "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
    "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
    "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
    "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI",
    "South Carolina": "SC", "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX",
    "Utah": "UT", "Vermont": "VT", "Virginia": "VA", "Washington": "WA",
    "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
    "District of Columbia": "DC", "United States": "US"
}

STATE_PREFIX_MAP = {
    "Iowa": {"HB": "HF", "SB": "SF"},
    "Maine": {"HB": "LD", "SB": "LD"},
    "Nebraska": {"HB": "LB", "SB": "LB"},
    "New Jersey": {"HB": "A", "SB": "S"},
    "New Hampshire": {"HB": "H", "SB": "S"},
    "Massachusetts": {"HB": "H", "SB": "S"},
}

# -------------------------
# Session state
# -------------------------
if "flat_data" not in st.session_state:
    st.session_state["flat_data"] = pd.DataFrame()
if "summary_data" not in st.session_state:
    st.session_state["summary_data"] = pd.DataFrame()
if "search_results" not in st.session_state:
    st.session_state["search_results"] = pd.DataFrame()

# -------------------------
# Helper functions
# -------------------------
def normalize_bill(bill: str, state_full: str) -> str:
    bill = str(bill or "").strip().upper()
    bill = bill.replace(".", "").replace("(", "").replace(")", "")
    bill = re.sub(r"\s+", " ", bill)
    if state_full in STATE_PREFIX_MAP:
        for wrong, correct in STATE_PREFIX_MAP[state_full].items():
            if bill.startswith(wrong):
                bill = bill.replace(wrong, correct, 1)
    bill = re.sub(r"([A-Z]+)\s*0*([0-9]+[A-Z]*)", r"\1 \2", bill)
    return bill.strip()

def clean_bill(bill_raw: str, state_full: str) -> str:
    pattern = rf"^{re.escape(state_full)}\s*(\([A-Z]{{2}}\))?\s*"
    bill_clean = re.sub(pattern, "", str(bill_raw or "").strip(), flags=re.IGNORECASE)
    return normalize_bill(bill_clean, state_full)

def _normalize(num: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(num or "").upper())

def _iter_search_items(sr: dict):
    results = sr.get("results")
    if isinstance(results, list):
        return results
    if isinstance(results, dict):
        return list(results.values())
    return [v for k, v in sr.items() if k.isdigit() and isinstance(v, dict)]

def search_bill_single(state_abbr: str, query: str, year=1):
    params = {"key": API_KEY, "op": "getSearch", "state": state_abbr, "query": query, "year": year}
    r = requests.get(BASE_URL, params=params, timeout=30)
    data = r.json()
    if data.get("status") != "OK":
        return None, None
    sr = data.get("searchresult", {})
    items = _iter_search_items(sr)
    if not items:
        return None, None
    tgt = _normalize(query)
    exact = [it for it in items if _normalize(it.get("bill_number", "")) == tgt]
    cand_pool = exact or items
    cand = sorted(cand_pool, key=lambda x: (x.get("relevance", 0), x.get("last_action_date", "")), reverse=True)[0]
    return cand.get("bill_id"), cand

def search_bill_id(state_abbr: str, bill_num: str, state_full: str, year=1):
    normalized = normalize_bill(bill_num, state_full)
    variants = list({normalized, bill_num, bill_num.replace(" ", ""), re.sub(r"[^0-9]", "", bill_num)})
    for variant in variants:
        bill_id, picked = search_bill_single(state_abbr, variant, year)
        if bill_id:
            return bill_id, picked
    return None, None

def search_bills_by_keyword(state_abbr: str | None, query: str, year: int | None = None, max_results=200):
    params = {"key": API_KEY, "op": "getSearch", "query": query}
    if state_abbr:
        params["state"] = state_abbr
    params["year"] = year if year else 1
    r = requests.get(BASE_URL, params=params, timeout=30)
    try:
        data = r.json()
    except Exception:
        return []
    if data.get("status") != "OK":
        return []
    sr = data.get("searchresult", {})
    items = _iter_search_items(sr)
    out = []
    for it in items[:max_results]:
        out.append({
            "bill_id": it.get("bill_id"),
            "state": (it.get("state") or state_abbr or "").upper(),
            "bill_number": it.get("bill_number") or it.get("number"),
            "title": it.get("title") or it.get("short_title") or "",
            "relevance": it.get("relevance"),
            "last_action_date": it.get("last_action_date"),
        })
    return out

def get_bill(bill_id: int):
    params = {"key": API_KEY, "op": "getBill", "id": bill_id}
    r = requests.get(BASE_URL, params=params, timeout=30)
    data = r.json()
    if data.get("status") == "OK" and "bill" in data:
        return data["bill"]
    return None

def process_bill(bill_detail):
    s = bill_detail.get("session", {}) or {}
    flat_data = {
        "bill_id": bill_detail.get("bill_id"),
        "state": bill_detail.get("state"),
        "bill_number": bill_detail.get("bill_number"),
        "title": bill_detail.get("title"),
        "last_action": bill_detail.get("last_action"),
        "last_action_date": bill_detail.get("last_action_date"),
        "session_start": s.get("year_start"),
        "session_end": s.get("year_end"),
    }
    dem_count = sum(1 for sp in bill_detail.get("sponsors", []) if sp.get("party") == "D")
    rep_count = sum(1 for sp in bill_detail.get("sponsors", []) if sp.get("party") == "R")
    summary_data = {
        "state": bill_detail.get("state"),
        "bill_number": bill_detail.get("bill_number"),
        "title": bill_detail.get("title"),
        "dem_sponsors": dem_count,
        "rep_sponsors": rep_count,
        "session_start": s.get("year_start"),
        "session_end": s.get("year_end"),
        "last_action_date": bill_detail.get("last_action_date"),
        "last_action": bill_detail.get("last_action"),
        "completed": bill_detail.get("completed"),
    }
    return flat_data, summary_data

def derive_dates(df):
    df["start_date"] = pd.to_datetime(df["session_start"].astype(str) + "-01-01", errors="coerce")
    df["end_date"] = pd.to_datetime(df["last_action_date"], errors="coerce")
    df["completion_label"] = df["completed"].map({1: "Completed", 0: "Not Completed"})
    return df

# -------------------------
# Sidebar: Add/Search + Reset
# -------------------------
st.sidebar.title("Add & Search Bills")

if st.sidebar.button("Reset All Data"):
    st.session_state["flat_data"] = pd.DataFrame()
    st.session_state["summary_data"] = pd.DataFrame()
    st.session_state["search_results"] = pd.DataFrame()
    st.sidebar.success("All data reset for this session.")

# with st.sidebar.expander("➕ Add Bill Manually", expanded=True):
#     state_full = st.selectbox("State", options=[""] + sorted(STATE_ABBR.keys()))
#     bill_input = st.text_input("Bill (e.g., HB123)")
#     if st.button("Add this Bill"):
#         if not state_full or not bill_input:
#             st.sidebar.error("Please specify both a state and a bill number.")
#         else:
#             state_abbr = STATE_ABBR.get(state_full)
#             bill_id, _ = search_bill_id(state_abbr, clean_bill(bill_input, state_full), state_full)
#             if not bill_id:
#                 st.sidebar.warning(f"No results found for {bill_input}")
#             else:
#                 detail = get_bill(bill_id)
#                 if detail:
#                     flat_row, summary_row = process_bill(detail)
#                     st.session_state["flat_data"] = pd.concat(
#                         [st.session_state["flat_data"], pd.DataFrame([flat_row])], ignore_index=True
#                     )
#                     st.session_state["summary_data"] = pd.concat(
#                         [st.session_state["summary_data"], pd.DataFrame([summary_row])], ignore_index=True
#                     )
#                     st.sidebar.success(f"Added {state_abbr} {summary_row['bill_number']}")

with st.sidebar.expander("🔎 Search by Keyword"):
    kw_state = st.selectbox("Filter state", options=["All"] + sorted(STATE_ABBR.keys()))
    kw_state_abbr = None if kw_state == "All" else STATE_ABBR.get(kw_state)
    kw = st.text_input("Keyword(s)")
    if st.button("Search"):
        if not kw.strip():
            st.sidebar.error("Enter a keyword to search.")
        else:
            results = search_bills_by_keyword(kw_state_abbr, kw.strip())
            st.session_state["search_results"] = pd.DataFrame(results)
            st.sidebar.success(f"Found {len(results)} candidate(s).")

# -------------------------
# Main content
# -------------------------
st.title("Bill Tracker")

summary_df = derive_dates(st.session_state["summary_data"].copy()) if not st.session_state["summary_data"].empty else pd.DataFrame()

if not st.session_state["search_results"].empty:
    st.subheader("Search Results")
    results = st.session_state["search_results"]
    st.dataframe(results, use_container_width=True)
    chosen = st.multiselect(
        "Select results to add",
        results.index,
        format_func=lambda i: f"{results.loc[i,'state']} {results.loc[i,'bill_number']} — {results.loc[i,'title'][:60]}"
    )
    if st.button("Add selected results"):
        for i in chosen:
            row = results.loc[i]
            detail = get_bill(row["bill_id"])
            if detail:
                flat_row, summary_row = process_bill(detail)
                st.session_state["flat_data"] = pd.concat(
                    [st.session_state["flat_data"], pd.DataFrame([flat_row])], ignore_index=True
                )
                st.session_state["summary_data"] = pd.concat(
                    [st.session_state["summary_data"], pd.DataFrame([summary_row])], ignore_index=True
                )
        st.success(f"Added {len(chosen)} bills.")
        st.session_state["search_results"] = pd.DataFrame()
        summary_df = derive_dates(st.session_state["summary_data"].copy())

if summary_df.empty:
    st.info("No bills yet. Add manually or via search.")
    st.stop()

# -------------------------
# KPIs
# -------------------------
col1, col2, col3 = st.columns(3)
col1.metric("Total Bills", len(summary_df))
col2.metric("Completed", int(summary_df["completed"].sum()))
col3.metric("Not Completed", int(len(summary_df) - summary_df["completed"].sum()))

st.markdown("---")

# -------------------------
# Timeline
# -------------------------
st.subheader("Bill Timelines")
tl = summary_df.dropna(subset=["start_date", "end_date"]).copy()
if not tl.empty:
    tl["bill_label"] = tl["state"] + " — " + tl["bill_number"].astype(str)
    fig_tl = px.timeline(
        tl,
        x_start="start_date",
        x_end="end_date",
        y="bill_label",
        color="completion_label",
        color_discrete_map={"Completed": "#2ca02c", "Not Completed": "#d62728"}
    )
    fig_tl.update_yaxes(autorange="reversed")
    st.plotly_chart(fig_tl, use_container_width=True)

# -------------------------
# Waffle chart — party distribution
# -------------------------
if WAFFLE_AVAILABLE and not summary_df.empty:
    st.subheader("Sponsor Breakdown — Democrats vs Republicans")
    
    dem_total = int(summary_df["dem_sponsors"].sum())
    rep_total = int(summary_df["rep_sponsors"].sum())
    
    if dem_total + rep_total > 0:
        party_data = {"Democrats": dem_total, "Republicans": rep_total}
        labels = list(party_data.keys())
        values = list(party_data.values())
        colors = ["#1f77b4", "#d62728"]
        
        fig = plt.figure(
            FigureClass=Waffle,
            rows=10,
            values=values,
            colors=colors,
            labels=[f"{lbl} ({val})" for lbl, val in zip(labels, values)],
            legend={"loc": "upper left", "bbox_to_anchor": (1, 1)},
            title={"label": "Total Sponsors by Party", "loc": "center"},
        )
        st.pyplot(fig)
    else:
        st.info("No sponsor data available for the current bills.")
else:
    st.info("Install pywaffle to see the party breakdown waffle chart.")

# -------------------------
# Bill Explorer
# -------------------------
st.subheader("Bill Explorer")
st.dataframe(summary_df, use_container_width=True)
