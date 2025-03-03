import streamlit as st
import pdfplumber
from PIL import Image
import io
import os
import sqlite3
from datetime import datetime
import requests
import base64
import json
import hashlib

# Database file path
DB_PATH = "bank_onboarding.db"

# Directory to store uploaded documents
IMAGES_DIR = "images"
if not os.path.exists(IMAGES_DIR):
    os.makedirs(IMAGES_DIR)

# Ollama API endpoint
OLLAMA_API_URL = "http://localhost:11434/api/generate"

# Function to generate customer ID from first name and surname
def generate_customer_id(first_name, surname):
    return hashlib.md5((first_name + surname).encode()).hexdigest()[:8]

# Function to save file to disk and return its path
def save_uploaded_file(uploaded_file, customer_id, doc_type):
    file_ext = uploaded_file.name.split(".")[-1]
    file_name = f"{customer_id}_{doc_type}.{file_ext}"
    file_path = os.path.join(IMAGES_DIR, file_name)
    with open(file_path, "wb") as f:
        f.write(uploaded_file.getbuffer())
    return file_path

# Function to initialize database and create table
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            cid TEXT PRIMARY KEY,
            first_name TEXT,
            surname TEXT,
            residence_country TEXT,
            customer_type TEXT,
            occupation TEXT,
            time_at_address TEXT,
            street_address TEXT,
            city TEXT,
            state TEXT,
            postal_code TEXT,
            income_source TEXT,
            income_comments TEXT,
            expected_transaction_volume TEXT,
            file_paths TEXT,
            descriptions TEXT,
            created_at TEXT
        )
    """)
    
    cur.execute("PRAGMA table_info(customers)")
    columns = [column[1] for column in cur.fetchall()]
    expected_columns = [
        "cid", "first_name", "surname", "residence_country", "customer_type",
        "occupation", "time_at_address", "street_address", "city", "state",
        "postal_code", "income_source", "income_comments", "expected_transaction_volume",
        "file_paths", "descriptions", "created_at"
    ]
    for col in expected_columns:
        if col not in columns:
            cur.execute(f"ALTER TABLE customers ADD COLUMN {col} TEXT")
    
    conn.commit()
    cur.close()
    conn.close()

# Function to save customer data to database with file paths and descriptions
def save_customer(customer, file_paths, descriptions):
    try:
        values = (
            str(customer["CID"]),
            str(customer["First Name"]),
            str(customer["Surname"]),
            str(customer["Residence Country"]),
            str(customer["Customer Type"]),
            str(customer["Occupation"]),
            str(customer["Time at Address"]),
            str(customer["Street Address"]),
            str(customer["City/Suburb"]),
            str(customer["State/Province"]),
            str(customer["Postal/ZIP Code"]),
            str(customer["Income Source"]),
            str(customer["Income Comments"]),
            str(customer["Expected Transaction Volume"]),
            ",".join([str(fp) for fp in file_paths]) if file_paths else "",
            ",".join([str(desc) for desc in descriptions]) if descriptions else "",
            datetime.now().isoformat()
        )
        
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO customers (
                cid, first_name, surname, residence_country, customer_type,
                occupation, time_at_address, street_address, city, state,
                postal_code, income_source, income_comments, expected_transaction_volume,
                file_paths, descriptions, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, values)
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        st.error(f"Error saving to database: {str(e)}")
        return False

# Function to validate image using Ollama
def validate_image_with_ollama(image):
    try:
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        img_str = base64.b64encode(buffered.getvalue()).decode()

        payload = {
            "model": "llava:7b",
            "prompt": "Identify the document type in this image (e.g., passport, driver's license, national ID, income). Return only the document type as a single phrase, no additional text.",
            "images": [img_str],
            "stream": False
        }

        response = requests.post(OLLAMA_API_URL, json=payload, timeout=30)
        response.raise_for_status()
        
        raw_response = response.text
        try:
            result = json.loads(raw_response)
            doc_type_raw = result.get("response", "").strip().lower()
        except json.JSONDecodeError:
            st.error(f"Failed to parse Ollama response as JSON: {raw_response}")
            doc_type_raw = "unknown"

        doc_type_map = {
            "passport": ("passport", "The image appears to show a passport, which is an official document issued by a government, certifying the holder's identity and citizenship for international travel."),
            "national id": ("national_id", "The image appears to show a national ID card, specifically an Indian Aadhaar card, which is a 12-digit unique identity number issued by the Unique Identification Authority of India, serving as proof of residency and a biometric identifier."),
            "driver's license": ("drivers_license", "The image appears to show a driver's license, which is an official document permitting an individual to operate motorized vehicles."),
            "income": ("income", "The image appears to show an income verification document, typically used to confirm an individual's earnings or financial status.")
        }

        doc_type, description = doc_type_map.get(doc_type_raw, ("unknown", "The document type could not be identified."))
        
        return doc_type, description
            
    except requests.exceptions.RequestException as e:
        st.error(f"Error calling Ollama API: {str(e)}")
        return "unknown", str(e)
    except Exception as e:
        st.error(f"Error analyzing image with Ollama: {str(e)}")
        return "unknown", str(e)

# Function to extract images from PDF and validate them
def validate_pdf_with_ollama(pdf_file):
    try:
        doc_types = []
        descriptions = []
        first_doc_type = None
        
        with pdfplumber.open(pdf_file) as pdf:
            for i, page in enumerate(pdf.pages):
                for img in page.images:
                    img_data = img["stream"].get_data()
                    image = Image.open(io.BytesIO(img_data))
                    doc_type, description = validate_image_with_ollama(image)
                    if i == 0:
                        first_doc_type = doc_type
                    elif doc_type != first_doc_type:
                        st.warning(f"Page {i+1} detected as {doc_type}, but assuming {first_doc_type} for consistency in multi-page document.")
                        doc_type = first_doc_type
                    if doc_type != "unknown":
                        doc_types.append(doc_type)
                        descriptions.append(description)
                    st.image(image, caption=f"Extracted Image (Page {i+1}): {doc_type}", width=200)
                
                if not page.images:
                    page_img = page.to_image(resolution=150).original
                    doc_type, description = validate_image_with_ollama(page_img)
                    if i == 0:
                        first_doc_type = doc_type
                    elif doc_type != first_doc_type:
                        st.warning(f"Page {i+1} detected as {doc_type}, but assuming {first_doc_type} for consistency in multi-page document.")
                        doc_type = first_doc_type
                    if doc_type != "unknown":
                        doc_types.append(doc_type)
                        descriptions.append(description)
                    st.image(page_img, caption=f"Page Image (Page {i+1}): {doc_type}", width=200)
        
        return doc_types, descriptions
    except Exception as e:
        st.error(f"Error processing PDF with Ollama: {str(e)}")
        return [], []

# Page configuration
st.set_page_config(
    page_title="ABCD Bank - Customer Onboarding",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {font-size: 2.5rem; color: #1E3A8A; margin-bottom: 1rem;}
    .section-header {color: #1E3A8A; margin-top: 1.5rem; margin-bottom: 1rem;}
    .info-text {color: #4B5563; font-style: italic; font-size: 0.9rem;}
    .stButton>button {background-color: #1E3A8A; color: white; font-weight: bold; padding: 0.5rem 1rem; width: 100%;}
    .form-container {background-color: #F3F4F6; padding: 20px; border-radius: 10px;}
    .document-box {background-color: #E0E7FF; padding: 15px; border-radius: 5px;}
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'file_paths' not in st.session_state:
    st.session_state.file_paths = []
if 'documents_identified' not in st.session_state:
    st.session_state.documents_identified = []
if 'descriptions' not in st.session_state:
    st.session_state.descriptions = []

# Initialize database
init_db()

# Main header with bank logo
col_logo, col_title = st.columns([1, 4])
with col_logo:
    logo_path = os.path.join(os.getcwd(), "logo.png")
    if os.path.exists(logo_path):
        st.image(logo_path, width=100)
    else:
        st.warning("logo.png not found in current directory")
        st.image("https://via.placeholder.com/100", width=100)
with col_title:
    st.markdown("<div class='main-header'>ABCD Bank - Customer Onboarding</div>", unsafe_allow_html=True)

st.markdown("<p class='info-text'>Complete this form for new customer onboarding</p>", unsafe_allow_html=True)

# Create tabs
tab1, tab2 = st.tabs(["📋 Customer Information", "🔍 Document Upload"])

with tab1:
    st.markdown("<div class='section-header'>Personal Information</div>", unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        with st.container(border=True):
            first_name = st.text_input("First Name", "John")
            residence_country = st.selectbox("Residence Country", ["Australia (AUS)", "United States (USA)", "China (CHN)", "Russia (RUS)", "Offshore Financial Center (OFF)"], index=0)
            occupation = st.selectbox("Occupation", ["Engineer/Technical", "Retail/Cashier", "Government/Political", "Self-employed", "Finance/Banking", "Other/Unknown"], index=0)
    with col2:
        with st.container(border=True):
            surname = st.text_input("Surname", "Doe")
            customer_type = st.selectbox("Customer Type", ["Individual", "Company", "Trust", "Partnership"], index=0)
            time_at_address = st.selectbox("Time at Current Address", ["Less than 1 year", "1-3 years", "3-5 years", "More than 5 years"], index=1)

    st.markdown("<div class='section-header'>Address Information</div>", unsafe_allow_html=True)
    with st.container(border=True):
        col_addr1, col_addr2 = st.columns(2)
        with col_addr1:
            street_address = st.text_input("Street Address", "123 Main Street")
            city = st.text_input("City/Suburb", "Sydney")
        with col_addr2:
            state = st.text_input("State/Province", "NSW")
            postal_code = st.text_input("Postal/ZIP Code", "2000")
    
    st.markdown("<div class='section-header'>Source of Income</div>", unsafe_allow_html=True)
    with st.container(border=True):
        income_source = st.selectbox("Primary Source of Income", ["Employment", "Business", "Investments", "Inheritance/Gift", "Retirement/Pension", "Other"], index=0)
        income_comments = st.text_area("Additional Comments on Source of Income", "Customer claims income from freelance work and occasional consulting.")
        expected_transaction_volume = st.selectbox("Expected Monthly Transaction Volume", ["Less than $5,000", "$5,000 - $20,000", "$20,000 - $50,000", "More than $50,000"], index=1)

with tab2:
    st.markdown("<div class='section-header'>Document Upload</div>", unsafe_allow_html=True)
    st.markdown("<p class='info-text'>Upload identification and income verification documents (images or PDFs).</p>", unsafe_allow_html=True)
    
    with st.container(border=True):
        st.markdown('<div class="document-box">', unsafe_allow_html=True)
        st.subheader("Upload Documents")
        st.markdown("Upload identification (e.g., passport, driver's license, national ID) and income verification documents.")

        # Single uploader for all documents
        uploaded_files = st.file_uploader(
            "Upload Documents", 
            type=["png", "jpg", "jpeg", "pdf"], 
            accept_multiple_files=True,
            help="Upload images or PDFs of your identification and income verification documents"
        )

        customer_id = generate_customer_id(first_name, surname)
        file_paths = []
        documents_identified = []
        descriptions = []

        if uploaded_files:
            for uploaded_file in uploaded_files:
                if uploaded_file.type in ["image/png", "image/jpg", "image/jpeg"]:
                    image = Image.open(uploaded_file)
                    doc_type, description = validate_image_with_ollama(image)
                    file_path = save_uploaded_file(uploaded_file, customer_id, doc_type)
                    file_paths.append(file_path)
                    documents_identified.append(doc_type)
                    descriptions.append(description)
                    st.image(image, caption=f"Uploaded Image: {doc_type}", width=200)
                elif uploaded_file.type == "application/pdf":
                    doc_types, pdf_descriptions = validate_pdf_with_ollama(uploaded_file)
                    if doc_types:
                        file_path = save_uploaded_file(uploaded_file, customer_id, doc_types[0])  # Use first detected type for filename
                        file_paths.append(file_path)
                        documents_identified.extend(doc_types)
                        descriptions.extend(pdf_descriptions)
                    else:
                        file_path = save_uploaded_file(uploaded_file, customer_id, "unknown")
                        file_paths.append(file_path)
                        documents_identified.append("unknown")
                        descriptions.append("The document type could not be identified.")

            st.session_state.file_paths = file_paths
            st.session_state.documents_identified = documents_identified
            st.session_state.descriptions = descriptions

            if documents_identified:
                st.markdown("**Documents Identified:**")
                for doc, desc in zip(documents_identified, descriptions):
                    st.markdown(f"- **{doc.capitalize()}**: {desc}")

        st.markdown('</div>', unsafe_allow_html=True)

# Submit button
st.markdown("<div class='section-header'></div>", unsafe_allow_html=True)
col1, col2, col3 = st.columns([1, 2, 1])
with col2:
    if st.button("SUBMIT APPLICATION", use_container_width=True):
        customer = {
            "CID": customer_id,
            "First Name": first_name,
            "Surname": surname,
            "Residence Country": residence_country,
            "Customer Type": customer_type,
            "Occupation": occupation,
            "Time at Address": time_at_address,
            "Street Address": street_address,
            "City/Suburb": city,
            "State/Province": state,
            "Postal/ZIP Code": postal_code,
            "Income Source": income_source,
            "Income Comments": income_comments,
            "Expected Transaction Volume": expected_transaction_volume
        }
        
        if save_customer(customer, st.session_state.file_paths, st.session_state.descriptions):
            st.success("Application submitted and saved to database successfully!")
            st.json(customer)
        else:
            st.error("Application submitted but failed to save to database.")

# Sidebar
st.sidebar.markdown("# Instructions")
st.sidebar.markdown("""
### Customer Onboarding Process
1. Fill in all required customer information fields
2. Provide details about income sources
3. Upload identification and income verification documents (images or PDFs)
4. Submit application for processing
""")

st.sidebar.markdown("### Next Steps")
st.sidebar.markdown("""
After submission:
1. Customer data and documents will be stored
2. Further processing will be handled by the bank
""")