import os
import csv
import smtplib
import streamlit as st
import pandas as pd
from datetime import datetime
from email.message import EmailMessage
from openpyxl import Workbook

# ✅ Environment variables with safe defaults
EMAIL_ADDRESS: str = os.getenv("EMAIL_ADDRESS") or ""
EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD") or ""
SMTP_SERVER: str = os.getenv("SMTP_SERVER") or "smtp.gmail.com"
SMTP_PORT: int = int(os.getenv("SMTP_PORT") or 587)

# Ensure orders directory exists
os.makedirs("daily_orders", exist_ok=True)

# Global cart
if "cart" not in st.session_state:
    st.session_state.cart = []

# Sample product list
products = [
    {"name": "T-Shirt", "price": 20, "image": "👕"},
    {"name": "Jeans", "price": 50, "image": "👖"},
    {"name": "Sneakers", "price": 80, "image": "👟"},
]

# Utility: Save orders to CSV (daily + master)
def save_order(order_data: dict):
    today = datetime.today().strftime("%Y-%m-%d")
    daily_file = f"daily_orders/orders_{today}.csv"
    all_file = "orders.csv"
    
    for file in [daily_file, all_file]:
        write_header = not os.path.exists(file)
        with open(file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=order_data.keys())
            if write_header:
                writer.writeheader()
            writer.writerow(order_data)

# Utility: Create invoice Excel
def create_invoice(order_data: dict, items: list, filename: str):
    wb = Workbook()
    
    # Items sheet
    ws1 = wb.active
    ws1.title = "Items"
    ws1.append(["Product", "Price", "Qty", "Total"])
    for item in items:
        ws1.append([item["name"], item["price"], item["qty"], item["price"] * item["qty"]])

    # InvoiceMeta sheet
    ws2 = wb.create_sheet("InvoiceMeta")
    for key, val in order_data.items():
        ws2.append([key, val or ""])

    wb.save(filename)

# Utility: Send email
def send_email(to: str, subject: str, body: str):
    msg = EmailMessage()
    msg["From"] = EMAIL_ADDRESS or ""
    msg["To"] = to or ""
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as smtp:
        smtp.starttls()
        smtp.login(EMAIL_ADDRESS or "", EMAIL_PASSWORD or "")
        smtp.sendmail(EMAIL_ADDRESS or "", to or "", msg.as_string())

# Checkout process
def checkout():
    if not st.session_state.cart:
        st.error("Cart is empty!")
        return

    name: str = st.session_state.customer_name.strip()
    phone: str = st.session_state.customer_phone.strip()
    email: str = st.session_state.customer_email.strip()
    address: str = st.session_state.customer_address.strip()

    if not all([name, phone, email, address]):
        st.error("All fields are required!")
        return

    total = sum(item["price"] * item["qty"] for item in st.session_state.cart)
    order_data = {
        "Name": name,
        "Phone": phone,
        "Email": email,
        "Address": address,
        "Total": total,
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    save_order(order_data)
    create_invoice(order_data, st.session_state.cart, "invoice.xlsx")

    # Email with item summary
    items_list = "\n".join(
        [f"{item['image']} {item['name']} x {item['qty']} = ${item['price'] * item['qty']}" for item in st.session_state.cart]
    )
    email_body = f"Thank you {name}!\nYour order details:\n{items_list}\nTotal: ${total}"
    send_email(email, "Your Fashion Store Invoice", email_body)

    st.success("Order placed successfully!")
    st.session_state.cart = []

# Sidebar cart
st.sidebar.header("🛒 Cart")
if st.session_state.cart:
    for item in st.session_state.cart:
        st.sidebar.write(f"{item['image']} {item['name']} x {item['qty']} = ${item['price'] * item['qty']}")
    st.sidebar.write(f"**Total: ${sum(i['price']*i['qty'] for i in st.session_state.cart)}**")
else:
    st.sidebar.write("Cart is empty")

# Main UI
page = st.sidebar.radio("Go to", ["Shop", "Checkout", "Admin"])

if page == "Shop":
    st.title("🛍️ Fashion Store")
    for product in products:
        col1, col2 = st.columns([1, 3])
        with col1:
            st.write(product["image"])
        with col2:
            st.write(f"**{product['name']}** - ${product['price']}")
            qty = st.number_input(f"Qty {product['name']}", 0, 10, 0, key=product['name'])
            if st.button(f"Add {product['name']}", key=f"btn_{product['name']}"):
                if qty > 0:
                    st.session_state.cart.append({**product, "qty": qty})
                    st.success(f"Added {qty} x {product['name']} to cart")

elif page == "Checkout":
    st.title("💳 Checkout")
    st.text_input("Name", key="customer_name", value="")
    st.text_input("Phone", key="customer_phone", value="")
    st.text_input("Email", key="customer_email", value="")
    st.text_input("Address", key="customer_address", value="", on_change=checkout)

    if st.button("Submit Order"):
        checkout()

elif page == "Admin":
    st.title("📊 Admin Panel")

    if os.path.exists("orders.csv"):
        df = pd.read_csv("orders.csv")
        st.dataframe(df)

        if st.button("Export All Orders CSV"):
            df.to_csv("all_orders_export.csv", index=False)
            st.success("All orders exported!")

        today = datetime.today().strftime("%Y-%m-%d")
        df_today = df[df["Date"].str.startswith(today)]

        if st.button("Export Today's Orders CSV"):
            df_today.to_csv("todays_orders_export.csv", index=False)
            st.success("Today's orders exported!")

        if st.button("Export Today's Sales Summary"):
            summary = pd.DataFrame({
                "Subtotal": [df_today["Total"].sum()],
                "Tax": [0],
                "Discount": [0],
                "GrandTotal": [df_today["Total"].sum()],
            })
            summary.to_csv("todays_sales_summary.csv", index=False)
            st.success("Today's sales summary exported!")
    else:
        st.info("No orders found.")
