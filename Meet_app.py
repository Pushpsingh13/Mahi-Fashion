import os
import io
import json
import smtplib
from typing import Optional, List, Tuple
from datetime import datetime

import pandas as pd
import streamlit as st
import pytz
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
import urllib.parse

# -------------------------
# Configuration / files
# -------------------------
MENU_EXCEL = "Product_Details_Cleaned.xlsx"
SALES_CSV = "sales_records.csv"
SETTINGS_JSON = "settings.json"
INVOICES_DIR = "invoices"
ADMIN_PASSWORD = "admin123"

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

# 🔹 Updated load_menu

def load_menu() -> pd.DataFrame:
    ensure_menu_exists()
    df = pd.read_excel(MENU_EXCEL, engine="openpyxl")

    # Drop junk unnamed columns
    df = df.loc[:, ~df.columns.str.contains("^Unnamed")]

    required_cols = ["Item", "Size", "Price"]
    for c in required_cols:
        if c not in df.columns:
            raise ValueError(f"Excel must include columns: {required_cols}")

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
        df["All_Images"] = [[] for _ in range(len(df))]

    return df

# 🔹 Updated write_menu
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

    # Drop unnamed cols before saving
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
# Sales & Invoices (unchanged)
# -------------------------
# [KEEP the rest of your original code for save_sale, build_invoice_excel, build_receipt_pdf, send_email_receipt, wa_me_url]
# Sales & Invoices (kept similar to your original)
def save_sale(order_id: str, item_rows: list, totals: dict, customer: dict):
    rows = []
    now = tz_now()
    for r in item_rows:
        rows.append({
            "OrderID": order_id,
            "Date": now.strftime("%Y-%m-%d"),
            "Time": now.strftime("%H:%M:%S"),
            "Item": r["item"],
            "Size": r["size"],
            "Price": r["price"],
            "Customer": customer.get("name", ""),
            "Phone": customer.get("phone", ""),
            "Subtotal": totals["subtotal"],
            "Tax": totals["tax"],
            "Discount": totals["discount"],
            "GrandTotal": totals["grand"]
        })
    df_new = pd.DataFrame(rows)
    if os.path.exists(SALES_CSV):
        df_old = pd.read_csv(SALES_CSV)
        df_final = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_final = df_new
    df_final.to_csv(SALES_CSV, index=False)

def build_invoice_excel(order_id: str, item_rows: list, totals: dict, customer: dict) -> Tuple[bytes, str]:
    invoice_df = pd.DataFrame(item_rows)
    meta = {
        "OrderID": order_id,
        "Date": tz_now().strftime("%Y-%m-%d"),
        "Time": tz_now().strftime("%H:%M:%S"),
        "Customer": customer.get("name",""),
        "Phone": customer.get("phone",""),
        "Email": customer.get("email",""),
        "Address": customer.get("addr",""),
        "Subtotal": totals["subtotal"],
        "Tax": totals["tax"],
        "Discount": totals["discount"],
        "GrandTotal": totals["grand"]
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
        # If reportlab not installed, return None (UI will still work)
        return None
    lines = max(1, len(bill_rows))
    width = 80 * mm
    height = (70 + 8 * lines + 40) * mm
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
# Streamlit UI (unchanged except it uses new load_menu/write_menu)
# -------------------------
# [KEEP the rest of your original app.py code as you had it, no changes needed]
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

tab_shop, tab_cart, tab_admin = st.tabs(["Shop", "Cart & Checkout", "Admin Panel"])

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
                    st.image(imgs[0], use_container_width=True)
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
        st.success(f"Order {chk['order_id']} placed successfully!")
        st.download_button("Download Invoice (Excel)", data=chk["invoice_bytes"], file_name=f"Invoice_{chk['order_id']}.xlsx")
        if chk["pdf_buf"]:
            st.download_button("Download Receipt (PDF)", data=chk["pdf_buf"].getvalue(), file_name=f"Receipt_{chk['order_id']}.pdf")
        msg = f"Order {chk['order_id']}, Total ₹{chk['totals']['grand']:.2f}"
        if settings.get("owner_phone"):
            st.markdown(f'[Send WhatsApp to Owner]({wa_me_url(settings.get("owner_phone"), "NEW ORDER: "+msg)})')
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
                # Find the product image
                product_info = menu_df[menu_df['Item'] == item['item']].iloc[0]
                images = product_info.get('All_Images', [])
                if images:
                    st.image(images[0], width=50)
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
            caddr = st.text_area("Address")
            submitted = st.form_submit_button("Checkout")
            if submitted:
                if not cname or not cphone:
                    st.error("Name and Phone are required.")
                else:
                    order_id = f"ORD{tz_now().strftime('%Y%m%d%H%M%S')}"
                    cust = {"name": cname, "phone": cphone, "email": cemail, "addr": caddr}
                    totals = {"subtotal": subtotal, "tax": tax, "discount": discount, "grand": grand}
                    save_sale(order_id, st.session_state.cart, totals, cust)
                    inv_bytes, _ = build_invoice_excel(order_id, st.session_state.cart, totals, cust)
                    pdf_buf = build_receipt_pdf(order_id, st.session_state.cart, totals, cust)
                    st.session_state.last_checkout = {"order_id": order_id, "customer": cust, "totals": totals, "invoice_bytes": inv_bytes, "pdf_buf": pdf_buf}
                    st.rerun()

# -------------------------
# ADMIN TAB
# -------------------------
with tab_admin:
    st.header("Admin Panel")
    pw = st.text_input("Password", type="password")
    if pw == ADMIN_PASSWORD:
        st.success("Logged in")

        # 🔹 Settings section
        settings["owner_phone"] = st.text_input("Owner WhatsApp Number", settings.get("owner_phone", ""))
        settings["smtp_server"] = st.text_input("SMTP Server", settings.get("smtp_server", "smtp.gmail.com"))
        settings["smtp_port"] = st.number_input("SMTP Port", value=int(settings.get("smtp_port", 587)))
        settings["sender_email"] = st.text_input("Sender Email", settings.get("sender_email", ""))
        settings["sender_password"] = st.text_input("Sender Password", settings.get("sender_password", ""), type="password")
        settings["tax_rate"] = st.number_input("Tax Rate (%)", value=float(settings.get("tax_rate", 5.0)))
        settings["default_discount"] = st.number_input("Default Discount (%)", value=float(settings.get("default_discount", 0.0)))
        if st.button("Save Settings"):
            save_settings(settings)
            st.success("Settings saved")

        st.markdown("---")
        st.subheader("📝 Manage Products")

        menu_df = load_menu()
        st.dataframe(menu_df)

        # Upload new file
        new_file = st.file_uploader("Upload updated product Excel", type=["xlsx"])
        if new_file:
            with open(MENU_EXCEL, "wb") as f:
                f.write(new_file.getbuffer())
            st.success("Product list updated!")
            st.rerun()  # ✅ fixed: use st.rerun, not experimental_rerun

        # Add new product
        with st.expander("➕ Add Product"):
            new_item = st.text_input("Item Name", "")
            new_size = st.text_input("Available Sizes (comma-separated)", "")
            new_price = st.number_input("Price", min_value=0.0, step=1.0, value=0.0)
            new_img = st.text_input("Image URL", "")
            if st.button("Add Product"):
                if new_item.strip() != "":
                    df_new = pd.DataFrame([{
                        "Item": new_item,
                        "Size": new_size,
                        "Price": new_price,
                        "Images": new_img,
                        "Images2": "",
                        "Images3": "",
                        "Images4": "",
                        "Images5": "",
                        "Images6": "",
                        "Images7": ""
                    }])
                    menu_df = pd.concat([menu_df, df_new], ignore_index=True)
                    menu_df.to_excel(MENU_EXCEL, index=False, engine="openpyxl")
                    st.success(f"{new_item} added successfully!")
                    st.rerun()
                else:
                    st.error("Item Name cannot be empty")
    else:
        if pw:
            st.error("Invalid password")

        # Show small summary / download button
        st.write(f"Products in file: **{len(menu_df)}**")
        col1, col2 = st.columns([1, 3])
        with col1:
            uploaded = st.file_uploader("Upload Excel to replace product list", type=["xlsx"])
            if uploaded:
                # Replace file entirely
                with open(MENU_EXCEL, "wb") as f:
                    f.write(uploaded.getbuffer())
                st.success("Product Excel replaced. Reloading...")
                st.rerun()
            if st.button("Download current Excel"):
                with open(MENU_EXCEL, "rb") as f:
                    st.download_button("Download .xlsx", data=f.read(), file_name=MENU_EXCEL)
        with col2:
            if len(menu_df) == 0:
                st.warning("Product list is empty (many products may have been deleted). You can restore a sample list:")
                if st.button("Restore sample products"):
                    create_sample_menu().to_excel(MENU_EXCEL, index=False, engine="openpyxl")
                    st.success("Sample products restored.")
                    st.rerun()

        st.markdown("**Current product table**")
        # Show the table (small)
        st.dataframe(menu_df.reset_index().rename(columns={"index":"RowIndex"}), height=250)

        st.markdown("### Edit / Delete product")
        # select a product to edit by index row
        if len(menu_df) > 0:
            options = list(menu_df.index.astype(str))
            sel_idx = st.selectbox("Select product row index", options)
            sel_idx_int = int(sel_idx)
            prod = menu_df.loc[sel_idx_int].to_dict()
            # prefill edit form
            with st.form("edit_product_form"):
                new_item = st.text_input("Item", value=prod.get("Item",""))
                new_size = st.text_input("Size", value=prod.get("Size",""))
                new_price = st.number_input("Price", value=float(prod.get("Price", 0.0)))
                # handle images: show All_Images concatenated and editable
                imgs = prod.get("All_Images") or []
                img_text = ",".join(imgs)
                new_imgs_text = st.text_input("Images (comma separated URLs/paths)", value=img_text)
                col_update, col_delete = st.columns(2)
                with col_update:
                    update_btn = st.form_submit_button("Update Product")
                with col_delete:
                    delete_btn = st.form_submit_button("Delete Product")
                if update_btn:
                    # apply changes
                    menu_df.at[sel_idx_int, "Item"] = new_item
                    menu_df.at[sel_idx_int, "Size"] = new_size
                    menu_df.at[sel_idx_int, "Price"] = new_price
                    new_imgs = [i.strip() for i in new_imgs_text.split(",") if i.strip()]
                    menu_df.at[sel_idx_int, "All_Images"] = new_imgs
                    # write back to excel
                    write_menu(menu_df)
                    # ensure reindex and save
                    # rebuild All_Images if missing
                    if "All_Images" not in menu_df.columns:
                        image_cols = find_image_columns(menu_df)
                        if image_cols:
                            menu_df["All_Images"] = menu_df[image_cols].apply(
                                lambda row: [s for s in [str(x).strip() for x in row.tolist()] if s and s.lower() != "nan"],
                                axis=1
                            )
                        else:
                            menu_df["All_Images"] = [[] for _ in range(len(menu_df))]
                    write_menu(menu_df)
                    st.success("Product updated and Excel updated.")
                    st.rerun()
        else:
            st.info("No rows to select for edit/delete.")

        st.markdown("### Add new product")
        with st.form("add_product_form"):
            a_item = st.text_input("Item")
            a_size = st.text_input("Size (comma separated or ranges)")
            a_price = st.number_input("Price", value=0.0, min_value=0.0)
            a_images = st.text_input("Images (comma separated URLs/paths)", value="")
            add_btn = st.form_submit_button("Add Product")
            if add_btn:
                row = {"Item": a_item, "Size": a_size, "Price": a_price}
                imgs = [i.strip() for i in a_images.split(",") if i.strip()]
                row["All_Images"] = imgs
                # append to dataframe and save
                menu_df = menu_df.append(row, ignore_index=True) if len(menu_df)>0 else pd.DataFrame([row])
                # ensure All_Images present
                if "All_Images" not in menu_df.columns:
                    menu_df["All_Images"] = menu_df.apply(lambda r: r.get("All_Images", []), axis=1)
                write_menu(menu_df)
                st.success("Product added and Excel updated.")
                st.rerun()
