import streamlit as st
import gspread
from google.oauth2.service_account import Credentials

def test_google_sheets():
    st.title("🔧 Test Google Sheets Connection")
    
    try:
        # วิธีที่ 1: ใช้ Streamlit Secrets
        if 'gsheet_creds' in st.secrets:
            st.success("✅ Found Google Sheets credentials in secrets")
            credentials_dict = st.secrets["gsheet_creds"]
            credentials = Credentials.from_service_account_info(credentials_dict)
        else:
            # วิธีที่ 2: ใช้ JSON file
            try:
                credentials = Credentials.from_service_account_file("credentials.json")
                st.success("✅ Found credentials.json file")
            except FileNotFoundError:
                st.error("❌ No credentials found")
                return

        client = gspread.authorize(credentials)
        st.success("✅ Google Sheets client authorized")
        
        # ทดสอบเปิด sheet
        SHEET_ID = "1EMuK_cXYR2kk_Gb_i7MIOpnmfhC4Q2c9Uh5dUqpz7cc"
        spreadsheet = client.open_by_key(SHEET_ID)
        st.success(f"✅ Successfully opened spreadsheet: {spreadsheet.title}")
        
        # ทดสอบ worksheet
        worksheet = spreadsheet.worksheet("device status")
        st.success("✅ Successfully accessed worksheet")
        
        # ทดสอบอ่านข้อมูล
        data = worksheet.get_all_records()
        st.info(f"📊 Found {len(data)} records in sheet")
        
    except Exception as e:
        st.error(f"❌ Error: {str(e)}")

if __name__ == "__main__":
    test_google_sheets()
