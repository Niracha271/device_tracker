import streamlit as st
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
import os
import time
from enum import Enum
from typing import Optional, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================
# CONFIGURATION & CONSTANTS
# ============================================
SHEET_ID = "1EMuK_cXYR2kk_Gb_i7MIOpnmfhC4Q2c9Uh5dUqpz7cc"
SHEET_NAME = "devicestatus"
REQUIRED_COLUMNS = ["Serial Number", "Device Name", "Status", "Last Scanned/Added", "Scanned/Added By"]

class DeviceStatus(Enum):
    READY = "Ready"
    RETURN = "Return"
    DESTROY = "Destroy"

class StatusIcon(Enum):
    READY = "🟢"
    RETURN = "🔴"
    DESTROY = "💥"

# ============================================
# GOOGLE SHEETS SETUP
# ============================================
@st.cache_resource
def get_google_sheets_client():
    try:
        # อ่านจาก Streamlit secrets (Streamlit Cloud)
        creds_dict = st.secrets.get("gsheet_creds")
        if not creds_dict:
            raise RuntimeError("gsheet_creds not found in st.secrets")

        credentials = Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        client = gspread.authorize(credentials)
        return client
    except Exception as e:
        st.error(f"Failed to load Google Sheets credentials: {e}")
        return None

def get_worksheet():
    """Get the worksheet object for main device sheet (create if missing)"""
    try:
        client = get_google_sheets_client()
        if not client:
            return None

        spreadsheet = client.open_by_key(SHEET_ID)

        try:
            worksheet = spreadsheet.worksheet(SHEET_NAME)
            return worksheet

        except gspread.exceptions.WorksheetNotFound:
            # ถ้า worksheet ยังไม่มี ให้สร้างใหม่
            worksheet = spreadsheet.add_worksheet(title=SHEET_NAME, rows=1000, cols=20)
            worksheet.append_row(REQUIRED_COLUMNS)
            return worksheet

    except Exception as e:
        st.error(f"❌ Failed to access Google Sheet: {str(e)}")
        return None

# ============================================
# SESSION STATE INITIALIZATION
# ============================================
def init_session_state():
    """Initialize all session state variables"""
    if 'data_refresh' not in st.session_state:
        st.session_state.data_refresh = 0
    if 'last_scan' not in st.session_state:
        st.session_state.last_scan = None
    if 'scan_count' not in st.session_state:
        st.session_state.scan_count = 0
    if 'username' not in st.session_state:
        st.session_state.username = "Scanner"

init_session_state()

# ============================================
# DATA MANAGEMENT (Google Sheets)
# ============================================
@st.cache_data(ttl=10)
def load_data():
    """Load data from Google Sheets with caching"""
    worksheet = get_worksheet()
    if not worksheet:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)

    try:
        data = worksheet.get_all_records()

        if not data:
            worksheet.append_row(REQUIRED_COLUMNS)
            return pd.DataFrame(columns=REQUIRED_COLUMNS)

        df = pd.DataFrame(data)

        for col in REQUIRED_COLUMNS:
            if col not in df.columns:
                df[col] = ""

        return df

    except Exception as e:
        st.error(f"❌ Error loading data from sheets: {str(e)}")
        return pd.DataFrame(columns=REQUIRED_COLUMNS)


def save_data(df: pd.DataFrame) -> bool:
    """Save DataFrame to Google Sheets"""
    worksheet = get_worksheet()
    if not worksheet:
        return False

    try:
        worksheet.clear()
        worksheet.append_row(REQUIRED_COLUMNS)
        if not df.empty:
            data = df[REQUIRED_COLUMNS].values.tolist()

            batch_size = 100
            for i in range(0, len(data), batch_size):
                batch = data[i:i + batch_size]
                worksheet.append_rows(batch, value_input_option='RAW')

        st.cache_data.clear()
        st.session_state.data_refresh += 1

        return True

    except Exception as e:
        st.error(f"❌ Error saving data to sheets: {str(e)}")
        return False

# ============================================
# DESTROY LOG: store history of destroyed devices
# ============================================
def log_destroy(serial):
    """Save destroyed serial into destroy_log sheet"""
    try:
        client = get_google_sheets_client()
        if not client:
            return False

        spreadsheet = client.open_by_key(SHEET_ID)

        # เปิดชีท destroy_log ถ้าไม่มีให้สร้างใหม่พร้อม header
        try:
            ws = spreadsheet.worksheet("destroy_log")
        except gspread.exceptions.WorksheetNotFound:
            ws = spreadsheet.add_worksheet("destroy_log", rows=1000, cols=5)
            ws.append_row(["Serial Number", "Destroyed At", "By"])

        # บันทึกลง log
        ws.append_row([
            serial,
            datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S"),
            st.session_state.get("username", "unknown")
        ])
        return True

    except Exception as e:
        st.error(f"❌ Failed to log destroy: {e}")
        return False

def count_destroyed():
    """Count all logged destroys in destroy_log sheet"""
    try:
        client = get_google_sheets_client()
        if not client:
            return 0
        spreadsheet = client.open_by_key(SHEET_ID)
        try:
            ws = spreadsheet.worksheet("destroy_log")
            data = ws.get_all_records()
            return len(data)
        except gspread.exceptions.WorksheetNotFound:
            return 0
    except Exception:
        return 0

# ============================================
# UTILITY FUNCTIONS
# ============================================
def get_status_icon(status: str) -> str:
    """Return appropriate icon for status"""
    status_map = {
        DeviceStatus.READY.value: StatusIcon.READY.value,
        DeviceStatus.RETURN.value: StatusIcon.RETURN.value,
        DeviceStatus.DESTROY.value: StatusIcon.DESTROY.value,
    }
    return status_map.get(status, "❓")


def find_device_by_serial(df: pd.DataFrame, serial: str) -> Optional[Tuple[pd.Series, int]]:
    """Find device by serial number (case-insensitive)"""
    if df.empty or not serial.strip():
        return None

    matching = df[df["Serial Number"].astype(str).str.upper() == serial.upper()]
    if matching.empty:
        return None

    idx = matching.index[0]
    return matching.iloc[0], idx


def find_similar_serials(df: pd.DataFrame, search_term: str) -> pd.DataFrame:
    """Find similar serial numbers"""
    if df.empty or not search_term.strip():
        return pd.DataFrame()

    return df[df["Serial Number"].astype(str).str.contains(search_term, case=False, na=False)]


def validate_device_input(serial: str, name: str) -> Tuple[bool, str]:
    """Validate device input fields"""
    if not serial.strip():
        return False, "❌ Please enter Serial Number"
    if not name.strip():
        return False, "❌ Please enter Device Name"
    return True, ""


def check_duplicate_serial(df: pd.DataFrame, serial: str, exclude_idx: Optional[int] = None) -> bool:
    """Check if serial number already exists"""
    if df.empty:
        return False

    matches = df[df["Serial Number"].astype(str).str.upper() == serial.upper()]

    if exclude_idx is not None:
        matches = matches[matches.index != exclude_idx]

    return not matches.empty


def display_device_info(device: pd.Series):
    """Display device information in formatted columns"""
    col1, col2, col3 = st.columns(3)
    with col1:
        st.write(f"**Serial:** {device['Serial Number']}")
    with col2:
        st.write(f"**Device:** {device['Device Name']}")
    with col3:
        status_icon = get_status_icon(device['Status'])
        st.write(f"**Status:** {status_icon} {device['Status']}")

# ============================================
# BARCODE SCANNER FUNCTIONS
# ============================================
def cycle_status(current_status: str) -> str:
    if current_status == DeviceStatus.READY.value:
        return DeviceStatus.RETURN.value
    elif current_status == DeviceStatus.RETURN.value:
        return DeviceStatus.READY.value
    else:  # DESTROY
        return DeviceStatus.DESTROY.value


def process_barcode_scan(barcode_data: str, df: pd.DataFrame, default_status: str = "Ready") -> Tuple[
    bool, str, pd.DataFrame]:
    barcode_data = barcode_data.strip()

    if not barcode_data:
        return False, "❌ Empty barcode", df

    timestamp = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")

    result = find_device_by_serial(df, barcode_data)

    if result is not None:
        device, idx = result
        current_status = device['Status']

        # ถ้า status ปัจจุบันเป็น Destroy -> log แล้วลบ
        if current_status == DeviceStatus.DESTROY.value:
            # log ก่อนลบ
            log_destroy(barcode_data)
            df = df.drop(idx).reset_index(drop=True)
            if save_data(df):
                message = f"💥 Device Destroyed & Removed: {barcode_data}"
                return True, message, df
            else:
                return False, "❌ Failed to delete device", df

        # cycle status (Ready <-> Return)
        new_status = cycle_status(current_status)
        df.at[idx, "Status"] = new_status
        df.at[idx, "Last Scanned/Added"] = timestamp
        df.at[idx, "Scanned/Added By"] = st.session_state.username

        if save_data(df):
            status_icon = get_status_icon(new_status)
            message = f"🔄 Status Updated: {barcode_data} - {status_icon} {new_status}"
            return True, message, df
        else:
            return False, "❌ Failed to update scan info", df
    else:
        new_row = pd.DataFrame({
            "Serial Number": [barcode_data],
            "Device Name": ["Legacy pro"],
            "Status": [default_status],
            "Last Scanned/Added": [timestamp],
            "Scanned/Added By": [st.session_state.username]
        })
        df = pd.concat([df, new_row], ignore_index=True)

        if save_data(df):
            message = f"➕ New Device Added: {barcode_data}"
            return True, message, df
        else:
            return False, f"❌ Failed to save device: {barcode_data}", df

# ============================================
# MENU: BARCODE SCANNER
# ============================================
def menu_barcode_scanner(df: pd.DataFrame) -> pd.DataFrame:
    st.set_page_config(page_title="Medical Device Tracker", layout="wide")
    col1, col2 = st.columns([3, 1])
    with col1:
        st.info("**🔴 LIVE SCANNER MODE** - Auto-save on scan")
    with col2:
        st.text_input("User", value=st.session_state.username, label_visibility="collapsed", key="user_field")

    st.divider()
    default_status = st.selectbox(
        "Default Status for New Devices",
        [s.value for s in DeviceStatus],
        index=0, label_visibility="collapsed"
    )

    with st.form("scanner_form", clear_on_submit=True):
        st.markdown("**Scan barcode or paste data:**")
        barcode_input = st.text_input(
            "Barcode Scanner Input",
            key="scanner_input",
            placeholder="Tap scanner here or paste barcode...",
            label_visibility="collapsed"
        )

        st.markdown("""
            <style>
            div[data-testid="stFormSubmitButton"] button {
                display: none;
            }
            </style>
            """, unsafe_allow_html=True)

        submitted = st.form_submit_button("Submit")

        if submitted and barcode_input:
            st.session_state.scan_count += 1

            success, message, df = process_barcode_scan(barcode_input, df, default_status)

            if success:
                st.success(message)
                time.sleep(1.2)
                st.rerun()
            else:
                st.error(message)
                time.sleep(1.2)
                st.rerun()

    st.divider()
    # Statistics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("📊 Scans Today", st.session_state.scan_count)
    with col2:
        st.metric("🔧 Total Devices", len(df))
    with col3:
        st.metric("✅ Ready", (df["Status"] == DeviceStatus.READY.value).sum())
    with col4:
        st.metric("🔄 Return", (df["Status"] == DeviceStatus.RETURN.value).sum())

    st.subheader("📜 Recently Scanned")
    if not df.empty:
        recent = df.dropna(subset=["Last Scanned/Added"]).sort_values("Last Scanned/Added", ascending=False).head(20)
        if not recent.empty:
            display_cols = ["Serial Number", "Device Name", "Status", "Last Scanned/Added", "Scanned/Added By"]
            st.dataframe(recent[display_cols], use_container_width=True, hide_index=True)
        else:
            st.info("📭 No scans yet")
    else:
        st.info("📭 No devices")

        st.components.v1.html("""
            <script>
                setTimeout(() => {
                    const input = document.querySelector('input[placeholder*="Tap scanner here"]');
                    if (input) {
                        input.focus();
                        input.select();
                    }
                }, 800);
            </script>
            """, height=0)

    return df

# ============================================
# MENU: VIEW ALL DEVICES
# ============================================
def menu_view_all(df: pd.DataFrame):
    st.subheader("📋 All Devices")

    if df.empty:
        st.info("📭 No device data. Please add a new device or use barcode scanner.")
    else:
        display_cols = ["Serial Number", "Device Name", "Status", "Last Scanned/Added", "Scanned/Added By"]
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

        # Statistics
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("🔧 Total", len(df))
        with col2:
            st.metric("✅ Ready", (df["Status"] == DeviceStatus.READY.value).sum())
        with col3:
            st.metric("🔄 Return", (df["Status"] == DeviceStatus.RETURN.value).sum())
        with col4:
            # show historical destroyed count from log
            st.metric("💥 Destroyed (Total)", count_destroyed())

# ============================================
# MENU: SEARCH DEVICE
# ============================================
def menu_search(df: pd.DataFrame):
    st.subheader("🔍 Search Device")

    search_serial = st.text_input("Enter Serial Number", placeholder="Search...")

    if not search_serial:
        return

    # ไม่มีข้อมูลใน main sheet
    if df.empty:
        st.error("❌ No devices in system")
        return

    # ----- 1) ค้นหาใน main sheet -----
    result = find_device_by_serial(df, search_serial)

    if result is not None:
        device, _ = result
        st.success(f"✅ Found: {search_serial}")
        display_device_info(device)

        st.write("---")
        col1, col2 = st.columns(2)
        with col1:
            st.write(
                f"**Last Scanned/Added:** {device['Last Scanned/Added'] if device['Last Scanned/Added'] else 'Never'}")
        with col2:
            st.write(f"**Scanned/Added By:** {device['Scanned/Added By'] if device['Scanned/Added By'] else '-'}")
        return

    # ----- 2) ถ้าไม่เจอ → ค้นหาใน destroy_log -----
def display_destroy_device_info(device: dict):
    col1, col2, col3 = st.columns(3)
    with col1:
        st.write(f"**Serial:** {device.get('Serial Number','-')}")
    with col2:
        st.write(f"**Device:** {device.get('Device Name','-')}")
    with col3:
        st.write(f"**Status:** 💥 Destroy")
   
    client = get_google_sheets_client()

    try:
        spreadsheet = client.open_by_key(SHEET_ID)
        ws_destroy = spreadsheet.worksheet("destroy_log")
        destroy_data = ws_destroy.get_all_records()

        for row in destroy_data:
            if str(row["Serial Number"]).upper() == search_serial.upper():

                st.warning(f"💥 This device has been DESTROYED")
                
                # UI เหมือน device ปกติ
                display_destroy_device_info(row)

                st.write("---")
                col1, col2 = st.columns(2)
                with col1:
                    st.write(f"**Destroyed At:** {row.get('Destroyed At','-')}")
                with col2:
                    st.write(f"**Destroyed By:** {row.get('By','-')}")

                return

    except Exception as e:
        st.error(f"⚠ Error reading destroy_log: {e}")

    # ----- 3) ไม่เจอทั้ง main และ destroy -----
    st.error(f"❌ Serial Number '{search_serial}' not found")

    similar = find_similar_serials(df, search_serial)
    if not similar.empty:
        st.info("🔍 Similar Serial Numbers:")
        st.dataframe(similar[["Serial Number", "Device Name"]],
                     use_container_width=True, hide_index=True)

# ============================================
# MENU: ADD DEVICE MANUALLY
# ============================================
def menu_add_device(df: pd.DataFrame) -> pd.DataFrame:
    st.subheader("➕ Add New Device")

    with st.form("add_device_form"):
        new_serial = st.text_input("Serial Number *", placeholder="Enter Serial Number")
        new_name = st.text_input("Device Name *", placeholder="Enter Device Name")
        new_status = st.selectbox("Status *", [s.value for s in DeviceStatus])

        submitted = st.form_submit_button("Save", use_container_width=True)

        if submitted:
            is_valid, error_msg = validate_device_input(new_serial, new_name)
            if not is_valid:
                st.error(error_msg)
                return df

            if check_duplicate_serial(df, new_serial):
                st.warning("⚠️ Serial Number already exists in system")
                return df

            if new_status == DeviceStatus.DESTROY.value:
                st.warning("⚠️ 'Destroy' status will not add device to system")
                if st.checkbox("Confirm device destruction"):
                    # log destruction even if not added to main sheet
                    log_destroy(new_serial)
                    st.info(f"ℹ️ Device {new_serial} - {new_name} marked for destruction (logged)")
                    return df
                else:
                    st.info("Please confirm destruction")
                    return df

            timestamp = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
            new_row = pd.DataFrame({
                "Serial Number": [new_serial.strip()],
                "Device Name": [new_name.strip()],
                "Status": [new_status],
                "Last Scanned/Added": [timestamp],
                "Scanned/Added By": [st.session_state.username]
            })
            df = pd.concat([df, new_row], ignore_index=True)

            if save_data(df):
                st.success(f"✅ Device added: {new_serial} - {new_name}")
                st.rerun()

    return df

# ============================================
# MENU: EDIT DEVICE
# ============================================
def menu_edit_device(df: pd.DataFrame) -> pd.DataFrame:
    st.subheader("✏️ Edit Device")

    if df.empty:
        st.info("📭 No devices available. Please add a device first.")
        return df

    edit_serial = st.text_input("🔍 Search Serial Number to edit", key="edit_serial", placeholder="Search...")

    if not edit_serial:
        return df

    result = find_device_by_serial(df, edit_serial)

    if result is None:
        st.error("❌ Serial Number not found")
        if len(df) <= 20:
            st.info("📋 Available Serial Numbers:")
            for serial in df["Serial Number"].astype(str):
                st.write(f"- {serial}")
        return df

    device, idx = result
    st.success(f"✅ Found: {edit_serial}")
    st.divider()

    st.write("**Current Information:**")
    display_device_info(device)
    st.divider()

    with st.form("edit_device_form"):
        new_serial = st.text_input("New Serial Number *", value=device['Serial Number'])
        new_name = st.text_input("New Device Name *", value=device['Device Name'])
        new_status = st.selectbox(
            "New Status",
            [s.value for s in DeviceStatus],
            index=[s.value for s in DeviceStatus].index(device['Status']) if device['Status'] in [s.value for s in DeviceStatus] else 0
        )

        col1, col2 = st.columns(2)
        with col1:
            save_btn = st.form_submit_button("💾 Save Changes", use_container_width=True)
        with col2:
            cancel_btn = st.form_submit_button("❌ Cancel", use_container_width=True)

        if cancel_btn:
            st.info("Edit cancelled")
            return df

        if save_btn:
            is_valid, error_msg = validate_device_input(new_serial, new_name)
            if not is_valid:
                st.error(error_msg)
                return df

            if check_duplicate_serial(df, new_serial, exclude_idx=idx):
                st.warning("⚠️ New Serial Number already exists")
                return df

            if new_status == DeviceStatus.DESTROY.value:
                st.error("⚠️ Changing to 'Destroy' will delete device from system!")
                if st.checkbox("Confirm destruction", key="confirm_destroy_edit"):
                    # log destroy then remove from df
                    log_destroy(edit_serial)
                    df = df.drop(idx).reset_index(drop=True)
                    if save_data(df):
                        st.success(f"✅ Device destroyed and removed: {edit_serial}")
                        st.balloons()
                        st.rerun()
                return df

            df.at[idx, 'Serial Number'] = new_serial.strip()
            df.at[idx, 'Device Name'] = new_name.strip()
            df.at[idx, 'Status'] = new_status
            df.at[idx, 'Last Scanned/Added'] = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
            df.at[idx, 'Scanned/Added By'] = st.session_state.username

            if save_data(df):
                st.success("✅ Device updated successfully!")
                st.balloons()
                st.rerun()

    return df

# ============================================
# MENU: UPDATE STATUS
# ============================================
def menu_update_status(df: pd.DataFrame) -> pd.DataFrame:
    st.subheader("🔄 Update Device Status")

    if df.empty:
        st.info("📭 No devices available.")
        return df

    update_serial = st.text_input("Enter Serial Number", placeholder="Search...")

    if not update_serial:
        return df

    result = find_device_by_serial(df, update_serial)

    if result is None:
        st.error("❌ Serial Number not found")
        if len(df) <= 10:
            st.info("📋 Available Serial Numbers:")
            st.write(", ".join(df["Serial Number"].astype(str)))
        return df

    device, idx = result
    current_status = device["Status"]

    st.info(f"📱 **Device:** {device['Device Name']}")
    st.write(f"**Current Status:** {get_status_icon(current_status)} {current_status}")

    with st.form("update_status_form"):
        new_status = st.selectbox(
            "Select New Status",
            [s.value for s in DeviceStatus]
        )

        submitted = st.form_submit_button("Update Status", use_container_width=True)

        if submitted:
            if new_status == current_status:
                st.warning("⚠️ New status is same as current status")
                return df

            if new_status == DeviceStatus.DESTROY.value:
                st.error("⚠️ Changing to 'Destroy' will delete device from system!")

                if st.checkbox("Confirm destruction", key="confirm_destroy_update"):
                    # log destroy then remove
                    log_destroy(update_serial)
                    df = df[df["Serial Number"].astype(str).str.upper() != update_serial.upper()].reset_index(drop=True)
                    if save_data(df):
                        st.success(f"✅ Device destroyed and removed: {update_serial}")
                        st.rerun()
                return df

            df.loc[idx, "Status"] = new_status
            df.loc[idx, "Last Scanned/Added"] = datetime.now(ZoneInfo("Asia/Bangkok")).strftime("%Y-%m-%d %H:%M:%S")
            df.loc[idx, "Scanned/Added By"] = st.session_state.username

            if save_data(df):
                st.success(f"✅ Status updated: {get_status_icon(new_status)} {new_status}")
                st.rerun()

    return df

# ============================================
# SIDEBAR STATISTICS
# ============================================
def display_sidebar_stats(df: pd.DataFrame):
    st.sidebar.markdown("---")
    st.sidebar.markdown("📊 **System Information**")

    if not df.empty:
        st.sidebar.metric("Total Devices", len(df))
        st.sidebar.metric("✅ Ready", (df["Status"] == DeviceStatus.READY.value).sum())
        st.sidebar.metric("🔄 Return", (df["Status"] == DeviceStatus.RETURN.value).sum())
        st.sidebar.metric("💥 Destroyed (Total)", count_destroyed())
    else:
        st.sidebar.write("📭 No data in system")

    st.sidebar.markdown("---")
    st.sidebar.write(f"**Scans Today:** {st.session_state.scan_count}")

# ============================================
# MAIN APPLICATION
# ============================================
def main():
    st.set_page_config(
        page_title="APD Device Tracker",
        layout="wide",
        page_icon="🧰"
    )

    st.title("🧰 APD Device Tracker")
    st.subheader("Google Sheets Backend - Automatic Device Tracking")

    if not get_worksheet():
        st.error("""
        ⚠️ **Google Sheets not configured**

        Please setup:
        1. Google Service Account with Sheets API enabled
        2. Create Google Sheet and share with service account email
        3. Set SHEET_ID in the code (line 14)
        4. Add credentials to Streamlit Secrets
        """)
        return

    df = load_data()

    menu = st.sidebar.radio(
        "Menu",
        ["📱 Scanner Mode", "View All", "Search", "Add Device", "Edit Device", "Update Status"]
    )

    if menu == "📱 Scanner Mode":
        df = menu_barcode_scanner(df)
    elif menu == "View All":
        menu_view_all(df)
    elif menu == "Search":
        menu_search(df)
    elif menu == "Add Device":
        df = menu_add_device(df)
    elif menu == "Edit Device":
        df = menu_edit_device(df)
    elif menu == "Update Status":
        df = menu_update_status(df)

    display_sidebar_stats(df)

if __name__ == "__main__":
    main()








