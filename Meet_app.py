# app.py
import os
import io
import json
import smtplib
import glob
import urllib.parse
from typing import Optional, List, Tuple
from datetime import datetime

import pandas as pd
import streamlit as st
import pytz
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from PIL import Image


# For embedding image into PDF
try:
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from PIL import Image
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

# -------------------------
# Configuration / files
# -------------------------
MENU_EXCEL = "Product_Details_Cleaned.xlsx"
SALES_CSV = "sales_records.csv"
ORDER_CSV = "orderdetails.csv"
SETTINGS_JSON = "settings.json"
INVOICES_DIR = "invoices"
ADMIN_PASSWORD = "admin123"

# Path to your QR image (uploaded earlier). Adjust if your file path differs.
QR_IMAGE_PATH = "Payment_QR code.jpg"

os.makedirs(INVOICES_DIR, exist_ok=True)

# -------------------------
# Helpers
# -------------------------
def tz_now() -> datetime:
    return datetime.now(pytz.timezone("Asia/Kolkata"))

def create_sample_menu() -> pd.DataFrame:
    return pd.DataFrame({
        "Item": ["Kurti", "Saree", "Lehenga"],
        "Size": ["L-XL", "Free", "M,L,XL"],
        "Price": [615, 1200, 2500],
        "Images": ["", "", ""]
    })

def ensure_menu_exists():
    if not os.path.exists(MENU_EXCEL):
        create_sample_menu().to_excel(MENU_EXCEL, index=False, engine="openpyxl")

def find_image_columns(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns if c.lower().startswith("image")]

def load_menu() -> pd.DataFrame:
    ensure_menu_exists()
    try:
        df = pd.read_excel(MENU_EXCEL, engine="openpyxl")
    except Exception:
        df = create_sample_menu()
        df.to_excel(MENU_EXCEL, index=False, engine="openpyxl")

    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]

    required_cols = ["Item", "Size", "Price"]
    for c in required_cols:
        if c not in df.columns:
            df[c] = "" if c in ("Item", "Size") else 0.0

    df["Item"] = df["Item"].fillna("").astype(str)
    df["Size"] = df["Size"].fillna("").astype(str)
    df["Price"] = pd.to_numeric(df["Price"], errors="coerce").fillna(0.0)

    image_cols = find_image_columns(df)
    if image_cols:
        df["All_Images"] = df[image_cols].apply(
            lambda row: [str(x).strip() for x in row.tolist() if str(x).strip() and str(x).lower() != "nan"],
            axis=1
        )
    else:
        possible_cols = [c for c in df.columns if c.lower().startswith("images")]
        if possible_cols:
            df["All_Images"] = df[possible_cols].apply(
                lambda row: [s for s in [str(x).strip() for x in row.tolist()] if s and s.lower() != "nan"],
                axis=1
            )
        else:
            df["All_Images"] = [[] for _ in range(len(df))]

    return df

def write_menu(df: pd.DataFrame):
    if "Item" not in df.columns:
        df["Item"] = ""
    if "Size" not in df.columns:
        df["Size"] = ""
    if "Price" not in df.columns:
        df["Price"] = 0.0

    max_imgs = int(df.get("All_Images", pd.Series([[]]*len(df))).map(len).max() or 0)

    for i in range(max(1, max_imgs)):
        col = "Images" if i == 0 else f"Images{i+1}"
        if "All_Images" in df.columns:
            df[col] = df["All_Images"].apply(lambda imgs, idx=i: imgs[idx] if idx < len(imgs) else "")
        elif col not in df.columns:
            df[col] = ""

    df_to_save = df.copy()
    if "All_Images" in df_to_save.columns:
        df_to_save = df_to_save.drop(columns=["All_Images"])

    df_to_save = df_to_save.loc[:, ~df_to_save.columns.str.contains("^Unnamed")]

    df_to_save.to_excel(MENU_EXCEL, index=False, engine="openpyxl")

def parse_sizes(s_raw: str) -> List[str]:
    s = (s_raw or "").strip()
    if not s:
        return ["Free"]
    if "," in s:
        return [x.strip() for x in s.split(",") if x.strip()]
    if "-" in s:
        parts = [x.strip() for x in s.split("-") if x.strip()]
        return parts if parts else [s]
    return [s]

# -------------------------
# Settings persistence
# -------------------------
def load_settings() -> dict:
    defaults = {
        "owner_phone": "919999999999",
        "smtp_server": "smtp.gmail.com",
        "smtp_port": 587,
        "sender_email": "",
        "sender_password": "",
        "tax_rate": 5.0,
        "default_discount": 0.0
    }
    if os.path.exists(SETTINGS_JSON):
        try:
            with open(SETTINGS_JSON, "r", encoding="utf-8") as f:
                s = json.load(f)
            defaults.update(s)
        except Exception:
            pass
    return defaults

def save_settings(settings: dict):
    with open(SETTINGS_JSON, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

settings = load_settings()

# -------------------------
# Sales & Invoices (one row per order)
# -------------------------
def save_sale(order_id: str, item_rows: list, totals: dict, customer: dict):
    """
    Save a single row per order with combined Items column, plus payment info.
    """
    now = tz_now()
    items_str = "; ".join([f"{r['item']}({r.get('size','')}) ₹{r['price']:.2f}" for r in item_rows])
    
    payment_status = ""
    if customer.get("payment_method") == "UPI/PhonePe":
        payment_status = "Payment needs to be confirmed. Once confirmed, your item will be dispatched. If not, we will contact you."

    row = {
        "OrderID": order_id,
        "Date": now.strftime("%Y-%m-%d"),
        "Time": now.strftime("%H:%M:%S"),
        "Items": items_str,
        "Subtotal": totals["subtotal"],
        "Tax": totals["tax"],
        "Discount": totals["discount"],
        "GrandTotal": totals["grand"],
        "Customer": customer.get("name", ""),
        "Phone": customer.get("phone", ""),
        "Email": customer.get("email", ""),
        "Address": customer.get("addr", ""),
        "PaymentMethod": customer.get("payment_method", ""),
        "PaymentRef": customer.get("payment_ref", ""),
        "PaymentStatus": payment_status
    }

    df_new = pd.DataFrame([row])

    # Append to each CSV while keeping columns consistent
    now_date = now.strftime("%Y-%m-%d")
    target_paths = [SALES_CSV, ORDER_CSV, f"orderdetails_{now_date}.csv"]
    for p in target_paths:
        if os.path.exists(p):
            try:
                df_old = pd.read_csv(p)
                # ensure consistent columns: add missing columns if needed
                for col in df_new.columns:
                    if col not in df_old.columns:
                        df_old[col] = ""
                for col in df_old.columns:
                    if col not in df_new.columns:
                        df_new[col] = ""
                df_final = pd.concat([df_old, df_new[df_old.columns]], ignore_index=True)
            except Exception:
                df_final = df_new
        else:
            df_final = df_new
        df_final.to_csv(p, index=False)

def build_invoice_excel(order_id: str, item_rows: list, totals: dict, customer: dict) -> Tuple[bytes, str]:
    invoice_df = pd.DataFrame(item_rows)
    
    payment_status = ""
    if customer.get("payment_method") == "UPI/PhonePe":
        payment_status = "Payment needs to be confirmed. Once confirmed, your item will be dispatched. If not, we will contact you."

    meta = {
        "OrderID": order_id,
        "Date": tz_now().strftime("%Y-%m-%d"),
        "Time": tz_now().strftime("%H:%M:%S"),
        "Customer": customer.get("name", ""),
        "Phone": customer.get("phone", ""),
        "Email": customer.get("email", ""),
        "Address": customer.get("addr", ""),
        "PaymentMethod": customer.get("payment_method", ""),
        "PaymentRef": customer.get("payment_ref", ""),
        "Subtotal": totals["subtotal"],
        "Tax": totals["tax"],
        "Discount": totals["discount"],
        "GrandTotal": totals["grand"],
        "PaymentStatus": payment_status
    }
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        invoice_df.to_excel(writer, sheet_name="Items", index=False)
        pd.DataFrame([meta]).to_excel(writer, sheet_name="InvoiceMeta", index=False)
    out.seek(0)
    fname = f"Invoice_{order_id}.xlsx"
    path = os.path.join(INVOICES_DIR, fname)
    with open(path, "wb") as f:
        f.write(out.getvalue())
    return out.getvalue(), path

def build_receipt_pdf(order_id: str, bill_rows: list, totals: dict, cust: dict) -> Optional[io.BytesIO]:
    try:
        from reportlab.pdfgen import canvas
        from reportlab.lib.units import mm
    except ImportError:
        return None
    lines = max(1, len(bill_rows))
    width = 80 * mm
    height = (70 + 8 * lines + 60) * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    y = height - 10
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width / 2, y, "Mahi Fashion")
    y -= 12
    c.setFont("Helvetica", 8)
    c.drawCentredString(width / 2, y, "Mahi Fashion Collection")
    y -= 10
    c.line(0, y, width, y)
    y -= 12
    c.setFont("Helvetica", 8)
    c.drawString(2, y, f"Bill Time: {tz_now().strftime('%d %b %Y %H:%M:%S')}")
    y -= 10
    c.drawString(2, y, f"Order ID: {order_id}")
    y -= 10
    c.drawString(2, y, f"Customer: {cust.get('name','')}")
    y -= 10
    c.drawString(2, y, f"Phone: {cust.get('phone','')}")
    y -= 10
    c.drawString(2, y, f"Email: {cust.get('email','')}")
    y -= 10
    c.drawString(2, y, f"Address: {cust.get('addr','')}")
    y -= 10
    c.line(0, y, width, y)
    y -= 12
    c.setFont("Helvetica-Bold", 8)
    c.drawString(2, y, "Item (Size)")
    c.drawRightString(width - 2, y, "Price")
    y -= 10
    c.setFont("Helvetica", 8)
    for r in bill_rows:
        title = f"{r['item']} ({r['size']})"
        c.drawString(2, y, title[:28])
        c.drawRightString(width - 2, y, f"₹{r['price']:.2f}")
        y -= 10
    c.line(0, y, width, y)
    y -= 12
    c.setFont("Helvetica-Bold", 8)
    c.drawString(2, y, "Subtotal")
    c.drawRightString(width - 2, y, f"₹{totals['subtotal']:.2f}")
    y -= 10
    c.drawString(2, y, "Tax")
    c.drawRightString(width - 2, y, f"₹{totals['tax']:.2f}")
    y -= 10
    c.drawString(2, y, "Discount")
    c.drawRightString(width - 2, y, f"-₹{totals['discount']:.2f}")
    y -= 10
    c.drawString(2, y, "Grand Total")
    c.drawRightString(width - 2, y, f"₹{totals['grand']:.2f}")
    y -= 14
    if cust.get("payment_method") == "UPI/PhonePe":
        c.setFont("Helvetica-Bold", 8)
        c.drawCentredString(width / 2, y, "Payment needs to be confirmed.")
        y -= 10
        c.drawCentredString(width / 2, y, "Once confirmed, your item will be delivered.")
        y -= 10
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(width / 2, y, "Thank you for shopping!")
    c.showPage()
    c.save()
    buf.seek(0)
    return buf

def send_email_receipt(to_email: Optional[str], subject: str, body_text: str, pdf_bytes: bytes, order_id: str,
                       smtp_server: str, smtp_port: int, sender_email: str, sender_password: str) -> bool:
    if not to_email:
        st.error("Customer email is empty.")
        return False
    if not sender_email or not sender_password:
        st.error("Sender email credentials are missing (set in Admin Panel).")
        return False
    try:
        msg = MIMEMultipart()
        msg["From"] = sender_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body_text, "plain"))
        if pdf_bytes:
            part = MIMEApplication(pdf_bytes, Name=f"receipt_{order_id}.pdf")
            part["Content-Disposition"] = f'attachment; filename="receipt_{order_id}.pdf"'
            msg.attach(part)
        with smtplib.SMTP(smtp_server, int(smtp_port), timeout=20) as server:
            server.starttls()
            server.login(sender_email, sender_password)
            server.sendmail(sender_email, [to_email], msg.as_string())
        return True
    except Exception as e:
        st.error(f"Failed to send email: {e}")
        return False

def wa_me_url(phone_digits: Optional[str], message: str) -> str:
    if not phone_digits:
        return ""
    digits = "".join(ch for ch in phone_digits if ch.isdigit())
    return f"https://wa.me/{digits}?text={urllib.parse.quote(message)}"

# -------------------------
# Streamlit UI
# -------------------------
st.set_page_config(page_title="Mahi Fashion Store", layout="wide")
st.title("🛍️ Mahi Fashion Store")

# session init
if "cart" not in st.session_state:
    st.session_state.cart = []
if "last_checkout" not in st.session_state:
    st.session_state.last_checkout = None

# attempt to load menu
try:
    menu_df = load_menu()
except Exception as e:
    st.error(f"Failed to load product list: {e}")
    menu_df = create_sample_menu()
    write_menu(menu_df)

products = menu_df.to_dict(orient="records")

tab_shop, tab_cart = st.tabs(["Shop", "Cart & Checkout"])

# -------------------------
# SHOP TAB
# -------------------------
with tab_shop:
    st.header("Browse Products")
    q = st.text_input("Search products by name...", value="")
    filtered = [p for p in products if q.lower() in p["Item"].lower()] if q else products
    if not filtered:
        st.warning("No products found.")
    else:
        cols = st.columns(3)
        for idx, p in enumerate(filtered):
            col = cols[idx % 3]
            with col:
                st.subheader(p["Item"])
                imgs = p.get("All_Images") or []
                if imgs:
                    try:
                        st.image(imgs[0], use_container_width=True)
                    except Exception:
                        pass
                st.write(f"Price: ₹{float(p['Price']):.2f}")
                sizes = parse_sizes(p.get("Size", ""))
                if sizes:
                    sel = st.selectbox(f"Size for {p['Item']}", sizes, key=f"size_{idx}")
                    if st.button(f"Add {p['Item']}", key=f"add_{idx}"):
                        st.session_state.cart.append({"item": p["Item"], "size": sel, "price": float(p["Price"])})
                        st.success(f"Added {p['Item']} ({sel})")
                else:
                    st.warning("No sizes available for this item.")

# -------------------------
# CART TAB
# -------------------------
with tab_cart:
    st.header("🛒 Your Cart")
    if st.session_state.last_checkout:
        chk = st.session_state.last_checkout
        if chk['customer'].get('payment_method') == "UPI/PhonePe":
            st.info(f"Order {chk['order_id']} placed successfully! Your payment needs to be confirmed. Once confirmed, your item will be dispatched. If not, we will contact you.")
        else:
            st.success(f"Order {chk['order_id']} placed successfully!")
        st.download_button("Download Invoice (Excel)", data=chk["invoice_bytes"], file_name=f"Invoice_{chk['order_id']}.xlsx")
        if chk["pdf_buf"]:
            st.download_button("Download Receipt (PDF)", data=chk["pdf_buf"].getvalue(), file_name=f"Receipt_{chk['order_id']}.pdf")
        msg = f"Order {chk['order_id']}, Total ₹{chk['totals']['grand']:.2f}"
        if chk['customer'].get('payment_method') == "UPI/PhonePe":
            msg += "\n\nYour payment needs to be confirmed. Once confirmed, your item will be dispatched. If not, we will contact you."
        
        if settings.get("owner_phone"):
            from datetime import timedelta
            dispatch_date = (tz_now() + timedelta(days=3)).strftime('%d %b %Y')
            owner_msg = f"NEW ORDER: {chk['order_id']}. Total: ₹{chk['totals']['grand']:.2f}. Please dispatch by {dispatch_date}."
            wa_url = wa_me_url(settings.get("owner_phone"), owner_msg)
            st.markdown(f'[Send WhatsApp to Owner]({wa_url})')
            
        if chk["customer"].get("email"):
            if st.button("Email Receipt to Customer"):
                ok = send_email_receipt(
                    chk["customer"]["email"], f"Invoice {chk['order_id']}", msg,
                    chk["pdf_buf"].getvalue() if chk["pdf_buf"] else b"", chk["order_id"],
                    settings.get("smtp_server", "smtp.gmail.com"),
                    settings.get("smtp_port", 587),
                    settings.get("sender_email", ""), settings.get("sender_password", "")
                )
                if ok:
                    st.success("Email sent")
        if st.button("New Order"):
            st.session_state.cart = []
            st.session_state.last_checkout = None
            st.rerun()
    elif not st.session_state.cart:
        st.info("Your cart is empty.")
    else:
        for i, item in enumerate(st.session_state.cart):
            col1, col2, col3, col4 = st.columns([1, 3, 1, 1])
            with col1:
                try:
                    product_info = menu_df[menu_df['Item'] == item['item']].iloc[0]
                    images = product_info.get('All_Images', [])
                    if images:
                        st.image(images[0], width=50)
                except Exception:
                    pass
            with col2:
                st.write(f"{item['item']} ({item['size']})")
            with col3:
                st.write(f"₹{item['price']:.2f}")
            with col4:
                if st.button(f"Delete", key=f"delete_{i}"):
                    st.session_state.cart.pop(i)
                    st.rerun()

        subtotal = sum(item['price'] for item in st.session_state.cart)
        tax = subtotal * (settings.get("tax_rate", 5.0)/100)
        discount = subtotal * (settings.get("default_discount", 0.0)/100)
        grand = subtotal + tax - discount
        st.write(f"**Subtotal:** ₹{subtotal:.2f}")
        st.write(f"**Tax:** ₹{tax:.2f}")
        st.write(f"**Discount:** ₹{discount:.2f}")
        st.write(f"**Grand Total:** ₹{grand:.2f}")

        with st.form("checkout"):
            st.subheader("Customer Info")
            cname = st.text_input("Name")
            cphone = st.text_input("Phone")
            cemail = st.text_input("Email")
            caddr = st.text_input("Address")   # single line as you requested

            st.subheader("Payment Method")
            payment_method = st.radio(
                "Choose payment method",
                ["Cash on Delivery", "UPI/PhonePe", "Credit/Debit Card", "Net Banking"]
            )

            payment_ref = ""
            # Show QR image only for UPI/PhonePe
            if payment_method == "UPI/PhonePe":
                if os.path.exists(QR_IMAGE_PATH):
                    st.image(QR_IMAGE_PATH, caption="Scan & Pay (PhonePe/UPI)", width=300)
                    st.info("Scan the QR using PhonePe/any UPI app, complete payment, then enter the transaction/reference id below.")
                else:
                    st.warning("QR image not found on server (expected at {}).".format(QR_IMAGE_PATH))
                payment_ref = st.text_input("Payment Reference / Transaction ID (enter after payment)")
            elif payment_method in ("Credit/Debit Card", "Net Banking"):
                payment_ref = st.text_input("Payment Reference / Transaction ID")

            submitted = st.form_submit_button("Checkout")
            if submitted:
                # validations
                if not cname.strip() or not cphone.strip() or not cemail.strip() or not caddr.strip():
                    st.error("Please fill all fields (Name, Phone, Email, Address).")
                elif payment_method != "Cash on Delivery" and not payment_ref.strip():
                    st.error("Please enter Payment Reference / Transaction ID for online payment.")
                else:
                    order_id = f"ORD{tz_now().strftime('%Y%m%d%H%M%S')}"
                    cust = {
                        "name": cname.strip(),
                        "phone": cphone.strip(),
                        "email": cemail.strip(),
                        "addr": caddr.strip(),
                        "payment_method": payment_method,
                        "payment_ref": payment_ref.strip()
                    }
                    totals = {"subtotal": subtotal, "tax": tax, "discount": discount, "grand": grand}
                    # save order (one row)
                    save_sale(order_id, st.session_state.cart, totals, cust)
                    inv_bytes, _ = build_invoice_excel(order_id, st.session_state.cart, totals, cust)
                    pdf_buf = build_receipt_pdf(order_id, st.session_state.cart, totals, cust)
                    st.session_state.last_checkout = {"order_id": order_id, "customer": cust, "totals": totals, "invoice_bytes": inv_bytes, "pdf_buf": pdf_buf}
                    st.rerun()
import streamlit as st

# ---- PAGE CONFIG ----
st.set_page_config(
    page_title="Customer Portal",
    page_icon="🛍️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

# ---- HIDE STREAMLIT MENU/FOOTER/HEADER ----
hide_streamlit_style = """
    <style>
    #MainMenu {visibility: hidden;}   /* Hide hamburger menu */
    footer {visibility: hidden;}      /* Hide footer */
    header {visibility: hidden;}      /* Hide "Made with Streamlit" header */
    </style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)

# ---- YOUR CUSTOMER UI ----
st.title("🛍️ Welcome to Our Customer Portal")
st.write("This is a clean customer-facing interface with no Streamlit controls.")

# Example input/output
name = st.text_input("Enter your name")
if name:
    st.success(f"Hello {name}, thanks for visiting!")

# Example customer action
if st.button("Submit Order"):
    st.balloons()
    st.success("Your order has been submitted successfully!")
