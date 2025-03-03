import streamlit as st
import sqlite3
import pandas as pd
import numpy as np
from sklearn.preprocessing import LabelEncoder
import xgboost as xgb
import pdfplumber
import os
import requests
import re

# Database path
DB_PATH = "bank_onboarding.db"

# Ollama API endpoint
OLLAMA_API = "http://localhost:11434/api/generate"

# Function to fetch all customers from database
def fetch_all_customers():
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql_query("SELECT * FROM customers", conn)
    conn.close()
    return df

# Synthetic training data for XGBoost
def create_synthetic_data():
    np.random.seed(42)
    data = {
        "residence_country": np.random.choice(["Australia (AUS)", "United States (USA)", "China (CHN)", "Russia (RUS)", "Offshore Financial Center (OFF)"], 100),
        "customer_type": np.random.choice(["Individual", "Company", "Trust", "Partnership"], 100),
        "occupation": np.random.choice(["Engineer/Technical", "Retail/Cashier", "Government/Political", "Self-employed", "Finance/Banking", "Other/Unknown"], 100),
        "time_at_address": np.random.choice(["Less than 1 year", "1-3 years", "3-5 years", "More than 5 years"], 100),
        "income_source": np.random.choice(["Employment", "Business", "Investments", "Inheritance/Gift", "Retirement/Pension", "Other"], 100),
        "Risk_Score": np.random.uniform(0, 375, 100)
    }
    return pd.DataFrame(data)

# Train XGBoost model
def train_xgboost():
    df = create_synthetic_data()
    X = df.drop("Risk_Score", axis=1)
    y = df["Risk_Score"]
    
    encoders = {}
    for col in X.columns:
        encoders[col] = LabelEncoder()
        X[col] = encoders[col].fit_transform(X[col])
    
    model = xgb.XGBRegressor(objective="reg:squarederror", random_state=42)
    model.fit(X, y)
    return model, encoders

# Predict risk score (XGBoost)
def predict_risk(customer, model, encoders):
    X = pd.DataFrame({
        "residence_country": [customer["residence_country"]],
        "customer_type": [customer["customer_type"]],
        "occupation": [customer["occupation"]],
        "time_at_address": [customer["time_at_address"]],
        "income_source": [customer["income_source"]]
    })
    for col in X.columns:
        X[col] = encoders[col].transform(X[col])
    return model.predict(X)[0]

# Get LLM risk adjustment for income comments
def get_llm_risk_adjustment(income_comments):
    prompt = f"""
    You are a financial risk assessment expert. Given the following customer income comment: "{income_comments}", evaluate the potential risk to the bank. Consider factors like stability, legitimacy, and clarity of the income source. Provide:
    1. A risk adjustment score (0 to 50 points) to add to the base risk score.
    2. A brief explanation for your adjustment.
    Return your response in this exact format:
    Risk Adjustment: [number]
    Explanation: [text]
    """
    try:
        response = requests.post(
            OLLAMA_API,
            json={"model": "granite3.2:latest", "prompt": prompt, "stream": False},
            timeout=200
        )
        response.raise_for_status()
        raw_result = response.json().get("response", "").strip()
        if not raw_result:
            return 0, "LLM Error: Empty response from Ollama"
        
        # Default values
        adjustment = 0
        explanation = "No explanation provided"
        
        # Try structured parsing
        if "Risk Adjustment:" in raw_result and "Explanation:" in raw_result:
            try:
                adjustment_str = raw_result.split("Risk Adjustment: ")[1].split("\n")[0].strip()
                adjustment = int(adjustment_str)
                explanation = raw_result.split("Explanation: ")[1].strip()
            except (IndexError, ValueError):
                pass  # Fallback to defaults if parsing fails
        
        # Fallback: Look for numbers and assume rest is explanation
        if adjustment == 0:
            match = re.search(r"(\d+)", raw_result)
            if match:
                adjustment = min(int(match.group(0)), 50)  # Cap at 50
                explanation = raw_result
        
        return adjustment, explanation
    except requests.exceptions.ConnectionError:
        return 0, "LLM Error: Cannot connect to Ollama at localhost:11434. Ensure 'ollama serve' is running."
    except requests.exceptions.HTTPError as e:
        return 0, f"LLM Error: HTTP {e.response.status_code} - {e.response.text}"
    except Exception as e:
        return 0, f"LLM Error: Unexpected issue - {str(e)}"

# Risk category determination
def get_risk_category(score, max_score=375):
    if score < 100:
        return "Low Risk", "#1E8449"
    elif score < 250:
        return "Medium Risk", "#F39C12"
    else:
        return "High Risk", "#C0392B"

# Extract text from ID files (if any)
def extract_text_from_files(file_paths, descriptions):
    if not file_paths or not descriptions:
        return "No files uploaded."
    texts = []
    file_list = file_paths.split(",")
    desc_list = descriptions.split(",")
    for file_path, desc in zip(file_list, desc_list):
        if os.path.exists(file_path) and file_path.endswith(".pdf"):
            try:
                with pdfplumber.open(file_path) as pdf:
                    text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    texts.append(f"File: {file_path}\nDescription: {desc}\n{text[:500] + '...' if len(text) > 500 else text}")
            except Exception as e:
                texts.append(f"File: {file_path}\nError extracting text: {str(e)}")
        else:
            texts.append(f"File: {file_path}\nNot found or not a PDF.")
    return "\n\n".join(texts)

# Streamlit UI
st.set_page_config(
    page_title="ABCD Bank - CDD Risk Scoring", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {font-size: 1.8rem; color: #1E3A8A; margin-bottom: 1rem; font-weight: 600;}
    .section-header {color: #1E3A8A; margin-top: 1rem; margin-bottom: 0.5rem; font-weight: 500; font-size: 1.2rem;}
    .stButton>button {
        background-color: #1E3A8A; 
        color: white; 
        border-radius: 4px;
        border: none;
    }
    .card {
        border: 1px solid #e5e7eb;
        border-radius: 8px;
        padding: 1rem;
        margin-bottom: 1rem;
    }
    .risk-badge {
        display: inline-block;
        padding: 4px 8px;
        border-radius: 4px;
        color: white;
        font-weight: 500;
    }
    .risk-bar {
        height: 24px;
        border-radius: 4px;
        margin-top: 4px;
        margin-bottom: 12px;
    }
    .risk-label {
        font-weight: 500;
        margin-bottom: 2px;
    }
</style>
""", unsafe_allow_html=True)

# App header
st.markdown('<div class="main-header">ABCD Bank - CDD Risk Scoring</div>', unsafe_allow_html=True)

# Train XGBoost model
xgb_model, encoders = train_xgboost()

# Initialize session state
if 'selected_customer_id' not in st.session_state:
    st.session_state.selected_customer_id = None
if 'risk_display' not in st.session_state:
    st.session_state.risk_display = None

# Sidebar
with st.sidebar:
    st.header("Filters")
    customers_df = fetch_all_customers()
    
    if not customers_df.empty:
        countries = ["All"] + sorted(customers_df["residence_country"].unique().tolist())
        customer_types = ["All"] + sorted(customers_df["customer_type"].unique().tolist())
        
        selected_country = st.selectbox("Country", countries)
        selected_type = st.selectbox("Customer Type", customer_types)
        
        st.markdown("---")
        st.caption("Instructions")
        st.write("""
        1. Filter customers using options above
        2. Click 'Select' on a customer
        3. Use buttons to view Structured or Unstructured risk results
        """)

# Main content
if "customers_df" in locals() and not customers_df.empty:
    # Apply filters
    filtered_df = customers_df.copy()
    if selected_country != "All":
        filtered_df = filtered_df[filtered_df["residence_country"] == selected_country]
    if selected_type != "All":
        filtered_df = filtered_df[filtered_df["customer_type"] == selected_type]
    
    # Search feature
    st.markdown('<div class="section-header">Customer Search</div>', unsafe_allow_html=True)
    search_term = st.text_input("Search by name or ID", "")
    
    if search_term:
        search_condition = (
            filtered_df["first_name"].str.contains(search_term, case=False) | 
            filtered_df["surname"].str.contains(search_term, case=False) |
            filtered_df["cid"].astype(str).str.contains(search_term)
        )
        filtered_df = filtered_df[search_condition]
    
    # Customer grid
    st.markdown('<div class="section-header">Customers</div>', unsafe_allow_html=True)
    
    if len(filtered_df) > 0:
        st.write(f"Showing {len(filtered_df)} customers")
        columns = st.columns([3, 2, 2, 2, 1])
        headers = ["Name", "Country", "Type", "Occupation", "Action"]
        for i, col in enumerate(columns):
            col.markdown(f"**{headers[i]}**")
            
        for i, row in filtered_df.iterrows():
            cols = st.columns([3, 2, 2, 2, 1])
            cols[0].write(f"{row['first_name']} {row['surname']}")
            cols[1].write(f"{row['residence_country']}")
            cols[2].write(f"{row['customer_type']}")
            cols[3].write(f"{row['occupation']}")
            if cols[4].button("Select", key=f"btn_{row['cid']}"):
                st.session_state.selected_customer_id = row['cid']
                st.session_state.risk_display = None  # Reset risk display
        
        # Show selected customer details
        if st.session_state.selected_customer_id:
            customer_matches = filtered_df[filtered_df['cid'] == st.session_state.selected_customer_id]
            if not customer_matches.empty:
                selected_customer = customer_matches.iloc[0]
                
                st.markdown('<div class="section-header">Customer Details</div>', unsafe_allow_html=True)
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown(f"""
                    <div class="card">
                        <h3>{selected_customer['first_name']} {selected_customer['surname']}</h3>
                        <p>ID: {selected_customer['cid']}</p>
                        <p>Type: {selected_customer['customer_type']}</p>
                        <p>Occupation: {selected_customer['occupation']}</p>
                        <p>Address Time: {selected_customer['time_at_address']}</p>
                        <p>Income Source: {selected_customer['income_source']}</p>
                    </div>
                    """, unsafe_allow_html=True)
                    
                with col2:
                    st.markdown(f"""
                    <div class="card">
                        <h3>Address Information</h3>
                        <p>{selected_customer['street_address']}, {selected_customer['city']}, {selected_customer['state']} {selected_customer['postal_code']}</p>
                        <p>Country: {selected_customer['residence_country']}</p>
                    </div>
                    """, unsafe_allow_html=True)
                
                # Unstructured income data
                st.markdown('<div class="section-header">Unstructured Income Data</div>', unsafe_allow_html=True)
                st.markdown(f"""
                <div class="card">
                    <h3>Income Comments</h3>
                    <p>{selected_customer['income_comments'] or 'No comments provided.'}</p>
                </div>
                """, unsafe_allow_html=True)
                
                # ID documents (if any)
                if selected_customer['file_paths']:
                    st.markdown('<div class="section-header">Identity Documents</div>', unsafe_allow_html=True)
                    file_text = extract_text_from_files(selected_customer['file_paths'], selected_customer['descriptions'])
                    st.markdown(f"""
                    <div class="card">
                        <h3>Uploaded ID Documents</h3>
                        <pre>{file_text}</pre>
                    </div>
                    """, unsafe_allow_html=True)
                
                # Risk calculation buttons
                st.markdown('<div class="section-header">Risk Assessment Options</div>', unsafe_allow_html=True)
                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    if st.button("Calculate Structured Risk", key="struct_btn"):
                        st.session_state.risk_display = "structured"
                with col_btn2:
                    if st.button("Calculate Unstructured Risk", key="unstruct_btn"):
                        st.session_state.risk_display = "unstructured"
                
                # Display risk results based on button clicked
                if st.session_state.risk_display:
                    st.markdown('<div class="section-header">Risk Assessment</div>', unsafe_allow_html=True)
                    if st.session_state.risk_display == "structured":
                        base_score = predict_risk(selected_customer, xgb_model, encoders)
                        risk_category, risk_color = get_risk_category(base_score, max_score=375)
                        st.markdown(f"""
                        <div class="card">
                            <p>Structured Risk Score (XGBoost): <strong style="font-size: 1.5rem; color: {risk_color}">{base_score:.1f}</strong> / 375</p>
                            <p>Category: <span class="risk-badge" style="background-color: {risk_color}">{risk_category}</span></p>
                            <div style="width: 100%; background-color: #f0f0f0; border-radius: 4px;">
                                <div style="width: {(base_score/375)*100}%; background-color: {risk_color}; height: 20px; border-radius: 4px;"></div>
                            </div>
                        </div>
                        """, unsafe_allow_html=True)
                    elif st.session_state.risk_display == "unstructured":
                        base_score = predict_risk(selected_customer, xgb_model, encoders)
                        adjustment, explanation = get_llm_risk_adjustment(selected_customer['income_comments'] or "No comments provided.")
                        total_score = base_score + adjustment
                        risk_category, risk_color = get_risk_category(total_score, max_score=425)
                        st.markdown(f"""
                        <div class="card">
                            <p>Base Score (XGBoost): {base_score:.1f} / 375</p>
                            <p>Income Adjustment (LLM): <strong style="color: {risk_color}">+{adjustment}</strong></p>
                            <p>Total Score: <strong style="font-size: 1.5rem; color: {risk_color}">{total_score:.1f}</strong> / 425</p>
                            <p>Category: <span class="risk-badge" style="background-color: {risk_color}">{risk_category}</span></p>
                            <div style="width: 100%; background-color: #f0f0f0; border-radius: 4px;">
                                <div style="width: {(total_score/425)*100}%; background-color: {risk_color}; height: 20px; border-radius: 4px;"></div>
                            </div>
                            <p>LLM Explanation: {explanation}</p>
                        </div>
                        """, unsafe_allow_html=True)
                
                # Risk factors
                st.markdown('<div class="section-header">Risk Factor Analysis</div>', unsafe_allow_html=True)
                country_impact = 25 if "Russia" in selected_customer["residence_country"] or "Offshore" in selected_customer["residence_country"] else 10
                type_impact = 20 if selected_customer["customer_type"] in ["Trust", "Partnership"] else 5
                occupation_impact = 15 if selected_customer["occupation"] in ["Government/Political", "Other/Unknown"] else 5
                address_impact = 20 if selected_customer["time_at_address"] == "Less than 1 year" else 5
                income_impact = 20 if selected_customer["income_source"] in ["Inheritance/Gift", "Other"] else 5
                
                st.markdown('<div class="card">', unsafe_allow_html=True)
                st.markdown('<div class="risk-label">Country Risk</div>', unsafe_allow_html=True)
                country_color = "#C0392B" if country_impact > 20 else "#F39C12" if country_impact > 15 else "#1E8449"
                st.markdown(f'<div class="risk-bar" style="width: {country_impact*4}%; background-color: {country_color};"></div>', unsafe_allow_html=True)
                
                st.markdown('<div class="risk-label">Customer Type Risk</div>', unsafe_allow_html=True)
                type_color = "#C0392B" if type_impact > 20 else "#F39C12" if type_impact > 15 else "#1E8449"
                st.markdown(f'<div class="risk-bar" style="width: {type_impact*4}%; background-color: {type_color};"></div>', unsafe_allow_html=True)
                
                st.markdown('<div class="risk-label">Occupation Risk</div>', unsafe_allow_html=True)
                occupation_color = "#C0392B" if occupation_impact > 20 else "#F39C12" if occupation_impact > 15 else "#1E8449"
                st.markdown(f'<div class="risk-bar" style="width: {occupation_impact*4}%; background-color: {occupation_color};"></div>', unsafe_allow_html=True)
                
                st.markdown('<div class="risk-label">Address Stability Risk</div>', unsafe_allow_html=True)
                address_color = "#C0392B" if address_impact > 20 else "#F39C12" if address_impact > 15 else "#1E8449"
                st.markdown(f'<div class="risk-bar" style="width: {address_impact*4}%; background-color: {address_color};"></div>', unsafe_allow_html=True)
                
                st.markdown('<div class="risk-label">Income Source Risk</div>', unsafe_allow_html=True)
                income_color = "#C0392B" if income_impact > 20 else "#F39C12" if income_impact > 15 else "#1E8449"
                st.markdown(f'<div class="risk-bar" style="width: {income_impact*4}%; background-color: {income_color};"></div>', unsafe_allow_html=True)
                
                st.markdown('</div>', unsafe_allow_html=True)
            else:
                st.warning("The selected customer is no longer available in the filtered results.")
    else:
        st.info("No customers match your search criteria.")
else:
    st.warning("No customer data found in the database. Please submit data using the onboarding form first.")