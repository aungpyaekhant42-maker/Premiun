"""
Premium Store Telegram Bot
---------------------------
pyTelegramBotAPI (telebot) + SQLite, single-file, mobile-runner friendly
(Termux / Pydroid3 / Android / Render compatible).

Install:
    pip install pyTelegramBotAPI

Run:
    python PremiunStore.py
"""

import os
import threading
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler
import telebot
from telebot import types

# ============ RENDER KEEP-ALIVE SERVER ============
# Render Web Service port scan နှင့် cron-job ping အတွက် dummy web server
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Premium Store Bot is Alive & Running!")

    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

def run_keep_alive():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

threading.Thread(target=run_keep_alive, daemon=True).start()

# ============ CONFIG ============
# နောက်ဆုံးထုတ်ထားသော Bot Token နှင့် Admin Telegram ID
BOT_TOKEN = "8913055175:AAFXup8SHJ5Z-2QcfAgiVgWhn3-mKT-uH_g"
ADMIN_IDS = [8538596908]
DB_FILE = "store.db"

# KBZPay / Wave ငွေလွှဲရန် အချက်အလက်
PAYMENT_INFO = {
    "kbz": {"label": "KBZPay", "name": "KhaingThin", "number": "09679843853"},
    "wave": {"label": "Wave Money", "name": "HeinZarni", "number": "09756531541"},
}

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# Prevents two concurrent buyers from both being sold the same stock row.
stock_lock = threading.Lock()

# ============ DATABASE ============
def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = db()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS products(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        price INTEGER NOT NULL,
        description TEXT DEFAULT ''
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS stock(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        data TEXT NOT NULL,
        sold INTEGER DEFAULT 0,
        sold_to INTEGER,
        FOREIGN KEY(product_id) REFERENCES products(id)
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        balance INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        product_id INTEGER,
        stock_id INTEGER,
        price INTEGER,
        method TEXT,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS topups(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        method TEXT,
        photo_file_id TEXT,
        status TEXT DEFAULT 'pending',
        decided_by INTEGER,
        timestamp TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    conn.commit()
    conn.close()
    
    # migrate older DBs
    conn = db()
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(topups)").fetchall()]
    if "status" not in cols:
        conn.execute("ALTER TABLE topups ADD COLUMN status TEXT DEFAULT 'approved'")
    if "decided_by" not in cols:
        conn.execute("ALTER TABLE topups ADD COLUMN decided_by INTEGER")
    conn.commit()
    conn.close()
    
    conn = db()
    pcols = [r["name"] for r in conn.execute("PRAGMA table_info(products)").fetchall()]
    if "description" not in pcols:
        conn.execute("ALTER TABLE products ADD COLUMN description TEXT DEFAULT ''")
    conn.commit()
    conn.close()

def is_admin(uid):
    return uid in ADMIN_IDS

def ensure_user(uid, username):
    conn = db()
    c = conn.cursor()
    row = c.execute("SELECT user_id FROM users WHERE user_id=?", (uid,)).fetchone()
    if not row:
        c.execute("INSERT INTO users(user_id, username, balance) VALUES(?,?,0)", (uid, username or ""))
    else:
        c.execute("UPDATE users SET username=? WHERE user_id=?", (username or "", uid))
    conn.commit()
    conn.close()

def get_balance(uid):
    conn = db()
    row = conn.execute("SELECT balance FROM users WHERE user_id=?", (uid,)).fetchone()
    conn.close()
    return row["balance"] if row else 0

def stock_left(pid):
    conn = db()
    left = conn.execute("SELECT COUNT(*) c FROM stock WHERE product_id=? AND sold=0", (pid,)).fetchone()["c"]
    conn.close()
    return left

def broadcast_to_all(text):
    conn = db()
    rows = conn.execute("SELECT user_id FROM users").fetchall()
    conn.close()
    sent = 0
    for r in rows:
        try:
            bot.send_message(r["user_id"], text)
            sent += 1
        except Exception:
            pass
    return sent

def notify_admins(text):
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(admin_id, text)
        except Exception:
            pass

# ============ STATE ============
user_state = {}
manual_sell_targets = {}

# ============ KEYBOARDS ============
def user_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("🛒 ကုန်ပစ္စည်းများ"), types.KeyboardButton("💳 ငွေဖြည့်ရန်"))
    kb.add(types.KeyboardButton("💰 လက်ကျန်ငွေ"), types.KeyboardButton("🆘 အကူအညီ"))
    return kb

def admin_menu():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(types.KeyboardButton("➕ ပစ္စည်းအသစ်ထည့်ရန်"), types.KeyboardButton("📦 Stock ထည့်ရန်"))
    kb.add(types.KeyboardButton("📊 Stock ကြည့်ရန်"), types.KeyboardButton("🗑 Stock ဖျက်ရန်"))
    kb.add(types.KeyboardButton("✏️ ပစ္စည်းစီမံရန်"), types.KeyboardButton("💵 User ငွေဖြည့်ရန်"))
    kb.add(types.KeyboardButton("🧾 တိုက်ရိုက်ရောင်းရန်"), types.KeyboardButton("📈 အရောင်းစာရင်း"))
    kb.add(types.KeyboardButton("⏳ စောင့်ဆိုင်းနေသော ငွေဖြည့်များ"), types.KeyboardButton("📢 အသိပေးစာပို့ရန်"))
    kb.add(types.KeyboardButton("👤 User မီနူး"))
    return kb

def products_inline():
    conn = db()
    rows = conn.execute("SELECT * FROM products").fetchall()
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        left = conn.execute("SELECT COUNT(*) c FROM stock WHERE product_id=? AND sold=0", (r["id"],)).fetchone()["c"]
        kb.add(types.InlineKeyboardButton(
            f"{r['name']} - {r['price']} MMK (ကျန် {left})",
            callback_data=f"viewprod_{r['id']}"
        ))
    conn.close()
    return kb

# ============ START ============
@bot.message_handler(commands=["start"])
def start(msg):
    ensure_user(msg.from_user.id, msg.from_user.username)
    if is_admin(msg.from_user.id):
        bot.send_message(msg.chat.id, "👑 Admin panel မှကြိုဆိုပါတယ်ဗျာ", reply_markup=admin_menu())
    else:
        bot.send_message(msg.chat.id, "🛍 Premium Store မှကြိုဆိုပါတယ်ရှင့်", reply_markup=user_menu())

@bot.message_handler(commands=["admin"])
def admin_cmd(msg):
    if is_admin(msg.from_user.id):
        bot.send_message(msg.chat.id, "👑 Admin panel", reply_markup=admin_menu())
    else:
        bot.send_message(msg.chat.id, "❌ Admin မဟုတ်ပါ")

# ============ USER: MENU BUTTONS ============
@bot.message_handler(func=lambda m: m.text == "👤 User မီနူး")
def to_user_menu(msg):
    bot.send_message(msg.chat.id, "User မီနူးသို့ ပြောင်းလိုက်ပါပြီ", reply_markup=user_menu())

@bot.message_handler(func=lambda m: m.text == "🛒 ကုန်ပစ္စည်းများ")
def show_products(msg):
    kb = products_inline()
    if not kb.keyboard:
        bot.send_message(msg.chat.id, "လောလောဆယ် ကုန်ပစ္စည်းမရှိသေးပါ")
        return
    bot.send_message(msg.chat.id, "ဝယ်ယူလိုသော ပစ္စည်းကို ရွေးပါ 👇", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == "💰 လက်ကျန်ငွေ")
def show_balance(msg):
    ensure_user(msg.from_user.id, msg.from_user.username)
    bal = get_balance(msg.from_user.id)
    bot.send_message(msg.chat.id, f"💰 သင့်လက်ကျန်ငွေ: <b>{bal} MMK</b>")

@bot.message_handler(func=lambda m: m.text == "🆘 အကူအညီ")
def help_msg(msg):
    bot.send_message(msg.chat.id, "ငွေဖြည့်ရန် 💳 ငွေဖြည့်ရန် ကိုနှိပ်ပြီး KBZPay/Wave ဖြင့် ကိုယ်တိုင်ငွေဖြည့်နိုင်ပါတယ်။ ပစ္စည်းဝယ်ရန် 🛒 ကုန်ပစ္စည်းများ ကိုနှိပ်ပါ။")

# ============ USER: SELF-SERVICE TOP-UP ============
@bot.message_handler(func=lambda m: m.text == "💳 ငွေဖြည့်ရန်")
def topup_choose_method(msg):
    ensure_user(msg.from_user.id, msg.from_user.username)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📱 KBZPay", callback_data="pay_kbz"))
    kb.add(types.InlineKeyboardButton("🌊 Wave Money", callback_data="pay_wave"))
    bot.send_message(msg.chat.id, "ငွေဖြည့်မည့် နည်းလမ်းကို ရွေးပါ 👇", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data in ("pay_kbz", "pay_wave"))
def topup_show_account(call):
    method = "kbz" if call.data == "pay_kbz" else "wave"
    info = PAYMENT_INFO[method]
    user_state[call.from_user.id] = {"step": "selftopup_amount", "method": method}
    bot.answer_callback_query(call.id)
    bot.send_message(
        call.message.chat.id,
        f"💳 <b>{info['label']}</b> သို့ လွှဲပေးပါ:\n"
        f"📛 နာမည်: {info['name']}\n"
        f"📞 နံပါတ်: <code>{info['number']}</code>\n\n"
        f"လွှဲပြီးရင် <b>ဖြည့်ငွေပမာဏ</b> ကို ဂဏန်းသက်သက် ရိုက်ထည့်ပါ:"
    )

@bot.message_handler(
    content_types=["photo"],
    func=lambda m: user_state.get(m.from_user.id, {}).get("step") == "selftopup_photo"
)
def topup_receive_screenshot(msg):
    uid = msg.from_user.id
    state = user_state.pop(uid, {})
    amount = state.get("amount", 0)
    method = state.get("method", "unknown")
    photo_id = msg.photo[-1].file_id

    conn = db()
    conn.execute("INSERT OR IGNORE INTO users(user_id, balance) VALUES(?,0)", (uid,))
    cur = conn.execute(
        "INSERT INTO topups(user_id, amount, method, photo_file_id, status) VALUES(?,?,?,?,'pending')",
        (uid, amount, method, photo_id)
    )
    topup_id = cur.lastrowid
    conn.commit()
    conn.close()

    bot.send_message(
        msg.chat.id,
        f"📨 ငွေဖြည့်တောင်းဆိုမှု ပို့ပြီးပါပြီ!\n💰 ပမာဏ: {amount} MMK\n\n"
        f"⏳ Admin စစ်ဆေးပြီး လက်ခံပေးတာနဲ့ balance ဝင်ပါလိမ့်မယ်။",
        reply_markup=user_menu()
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ လက်ခံမည်", callback_data=f"topup_ok_{topup_id}"),
        types.InlineKeyboardButton("❌ ငြင်းပယ်မည်", callback_data=f"topup_no_{topup_id}"),
    )
    for admin_id in ADMIN_IDS:
        try:
            bot.forward_message(admin_id, msg.chat.id, msg.message_id)
            bot.send_message(
                admin_id,
                f"💳 Top-up request #{topup_id}: user {uid} (@{msg.from_user.username})\n"
                f"နည်းလမ်း: {PAYMENT_INFO.get(method, {}).get('label', method)} | ပမာဏ: {amount} MMK",
                reply_markup=kb
            )
        except Exception:
            pass

@bot.callback_query_handler(func=lambda c: c.data.startswith("topup_ok_") or c.data.startswith("topup_no_"))
def topup_decide(call):
    if not is_admin(call.from_user.id):
        bot.answer_callback_query(call.id, "❌ Admin မဟုတ်ပါ", show_alert=True)
        return

    approve = call.data.startswith("topup_ok_")
    topup_id = int(call.data.rsplit("_", 1)[1])

    conn = db()
    row = conn.execute("SELECT * FROM topups WHERE id=?", (topup_id,)).fetchone()
    if not row:
        conn.close()
        bot.answer_callback_query(call.id, "Request မတွေ့ပါ", show_alert=True)
        return
    if row["status"] != "pending":
        conn.close()
        bot.answer_callback_query(call.id, f"ဒီတောင်းဆိုမှုကို ဆုံးဖြတ်ပြီးသားပါ ({row['status']})", show_alert=True)
        return

    target = row["user_id"]
    amount = row["amount"]

    if approve:
        conn.execute("UPDATE topups SET status='approved', decided_by=? WHERE id=?", (call.from_user.id, topup_id))
        conn.execute("INSERT OR IGNORE INTO users(user_id, balance) VALUES(?,0)", (target,))
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target))
        conn.commit()
        new_balance = get_balance(target)
        conn.close()
        bot.answer_callback_query(call.id, "✅ လက်ခံပြီးပါပြီ")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, f"✅ Top-up #{topup_id} လက်ခံပြီးပါပြီ ({amount} MMK)")
        try:
            bot.send_message(
                target,
                f"✅ သင့်ငွေဖြည့်မှု (#{topup_id}, {amount} MMK) ကို admin လက်ခံလိုက်ပါပြီ။\n"
                f"💳 လက်ကျန်ငွေ: {new_balance} MMK\n🛒 ကုန်ပစ္စည်းများ ကနေ ဝယ်ယူနိုင်ပါပြီ"
            )
        except Exception:
            pass
    else:
        conn.execute("UPDATE topups SET status='rejected', decided_by=? WHERE id=?", (call.from_user.id, topup_id))
        conn.commit()
        conn.close()
        bot.answer_callback_query(call.id, "❌ ငြင်းပယ်ပြီးပါပြီ")
        try:
            bot.edit_message_reply_markup(call.message.chat.id, call.message.message_id, reply_markup=None)
        except Exception:
            pass
        bot.send_message(call.message.chat.id, f"❌ Top-up #{topup_id} ငြင်းပယ်လိုက်ပါပြီ ({amount} MMK)")
        try:
            bot.send_message(
                target,
                f"❌ သင့်ငွေဖြည့်မှု (#{topup_id}, {amount} MMK) ကို admin ငြင်းပယ်လိုက်ပါတယ်။\n"
                f"Screenshot/ပမာဏ ပြန်စစ်ပြီး 💳 ငွေဖြည့်ရန် ကနေ ပြန်ကြိုးစားပါ၊ သို့မဟုတ် admin ကို ဆက်သွယ်ပါ။"
            )
        except Exception:
            pass

# ============ USER: VIEW PRODUCT DETAIL ============
@bot.callback_query_handler(func=lambda c: c.data.startswith("viewprod_"))
def view_product_detail(call):
    pid = int(call.data.split("_")[1])
    conn = db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.close()
    bot.answer_callback_query(call.id)
    if not product:
        bot.send_message(call.message.chat.id, "ပစ္စည်းမတွေ့ပါ")
        return
    left = stock_left(pid)
    desc = (product["description"] or "").strip()
    desc_text = desc if desc else "ဖော်ပြချက် မထည့်ရသေးပါ"
    text = (
        f"📦 <b>{product['name']}</b>\n"
        f"💵 ဈေးနှုန်း: {product['price']} MMK\n"
        f"📊 ကျန်ရှိ: {left} ခု\n\n"
        f"📝 <b>အကြောင်းအရာ:</b>\n{desc_text}"
    )
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🛒 ဝယ်ယူရန်", callback_data=f"buyconfirm_{pid}"))
    kb.add(types.InlineKeyboardButton("🔙 Back Menu", callback_data="backmenu"))
    try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
        bot.send_message(call.message.chat.id, text, reply_markup=kb)

# ============ USER: BACK TO PRODUCT LIST ============
@bot.callback_query_handler(func=lambda c: c.data == "backmenu")
def back_to_products(call):
    kb = products_inline()
    bot.answer_callback_query(call.id)
    if not kb.keyboard:
        try:
            bot.edit_message_text("လောလောဆယ် ကုန်ပစ္စည်းမရှိသေးပါ", call.message.chat.id, call.message.message_id)
        except Exception:
            bot.send_message(call.message.chat.id, "လောလောဆယ် ကုန်ပစ္စည်းမရှိသေးပါ")
        return
    try:
        bot.edit_message_text("ဝယ်ယူလိုသော ပစ္စည်းကို ရွေးပါ 👇", call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
        bot.send_message(call.message.chat.id, "ဝယ်ယူလိုသော ပစ္စည်းကို ရွေးပါ 👇", reply_markup=kb)

# ============ USER: BUY ============
@bot.callback_query_handler(func=lambda c: c.data.startswith("buyconfirm_"))
def buy_product(call):
    pid = int(call.data.split("_")[1])
    uid = call.from_user.id
    ensure_user(uid, call.from_user.username)

    with stock_lock:
        conn = db()
        product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        if not product:
            conn.close()
            bot.answer_callback_query(call.id, "ပစ္စည်းမတွေ့ပါ")
            return

        balance = get_balance(uid)
        if balance < product["price"]:
            conn.close()
            bot.answer_callback_query(call.id, "ငွေလုံလောက်မှုမရှိပါ", show_alert=True)
            bot.send_message(call.message.chat.id, f"❌ လက်ကျန်ငွေ မလုံလောက်ပါ။ လက်ရှိလက်ကျန်: {balance} MMK\nလိုအပ်: {product['price']} MMK\n💳 ငွေဖြည့်ရန် ကနေ အရင်ဖြည့်ပါ။")
            return

        stock_item = conn.execute(
            "SELECT * FROM stock WHERE product_id=? AND sold=0 LIMIT 1", (pid,)
        ).fetchone()
        if not stock_item:
            conn.close()
            bot.answer_callback_query(call.id, "Stock ကုန်သွားပါပြီ", show_alert=True)
            return

        conn.execute("UPDATE stock SET sold=1, sold_to=? WHERE id=?", (uid, stock_item["id"]))
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id=?", (product["price"], uid))
        conn.execute(
            "INSERT INTO orders(user_id, product_id, stock_id, price, method) VALUES(?,?,?,?,?)",
            (uid, pid, stock_item["id"], product["price"], "auto")
        )
        conn.commit()
        remaining = stock_left(pid)
        conn.close()

    bot.answer_callback_query(call.id, "✅ ဝယ်ယူမှုအောင်မြင်ပါသည်")
    bot.send_message(
        call.message.chat.id,
        f"✅ <b>{product['name']}</b> ဝယ်ယူမှုအောင်မြင်ပါသည်\n\n📦 သင့်ပစ္စည်း:\n<code>{stock_item['data']}</code>"
    )
    notify_admins(f"🔔 Auto-sale: {product['name']} → user {uid} (@{call.from_user.username})\nကျန်ရှိ stock: {remaining}")
    if remaining == 0:
        notify_admins(f"⚠️ <b>{product['name']}</b> Stock လုံးဝကုန်သွားပါပြီ! Stock အသစ်ထည့်ပေးပါ။")
        broadcast_to_all(f"⚠️ <b>{product['name']}</b> Stock လောလောဆယ်ကုန်သွားပါပြီ။ Stock အသစ်ဝင်ရင် အသိပေးပါမယ်။")

# ============ ADMIN: ADD PRODUCT ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "➕ ပစ္စည်းအသစ်ထည့်ရန်")
def add_product_start(msg):
    user_state[msg.from_user.id] = {"step": "add_product_name"}
    bot.send_message(msg.chat.id, "ပစ္စည်းနာမည် ရိုက်ထည့်ပါ:")

# ============ ADMIN: ADD STOCK ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "📦 Stock ထည့်ရန်")
def add_stock_start(msg):
    conn = db()
    rows = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    if not rows:
        bot.send_message(msg.chat.id, "ပစ္စည်းအရင်ထည့်ပါ (➕ ပစ္စည်းအသစ်ထည့်ရန်)")
        return
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.add(types.InlineKeyboardButton(r["name"], callback_data=f"stockprod_{r['id']}"))
    bot.send_message(msg.chat.id, "Stock ထည့်မယ့် ပစ္စည်းရွေးပါ:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("stockprod_"))
def stock_choose_product(call):
    pid = int(call.data.split("_")[1])
    user_state[call.from_user.id] = {"step": "add_stock_data", "product_id": pid}
    bot.send_message(call.message.chat.id, "Stock data ရိုက်ထည့်ပါ (account/code/key - တစ်ကြောင်းလျှင် တစ်ခု၊ များစွာထည့်လိုလျှင် တစ်ကြောင်းချင်း ခွဲပို့နိုင်ပါတယ်):")
    bot.answer_callback_query(call.id)

# ============ ADMIN: VIEW STOCK ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "📊 Stock ကြည့်ရန်")
def view_stock(msg):
    conn = db()
    products = conn.execute("SELECT * FROM products").fetchall()
    if not products:
        bot.send_message(msg.chat.id, "ပစ္စည်းမရှိသေးပါ")
        conn.close()
        return
    lines = []
    for p in products:
        left = conn.execute("SELECT COUNT(*) c FROM stock WHERE product_id=? AND sold=0", (p["id"],)).fetchone()["c"]
        soldc = conn.execute("SELECT COUNT(*) c FROM stock WHERE product_id=? AND sold=1", (p["id"],)).fetchone()["c"]
        lines.append(f"📦 <b>{p['name']}</b> - {p['price']} MMK\n   ကျန်: {left} | ရောင်းပြီး: {soldc}")
    conn.close()
    bot.send_message(msg.chat.id, "\n\n".join(lines))

# ============ ADMIN: REMOVE STOCK ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "🗑 Stock ဖျက်ရန်")
def remove_stock_start(msg):
    conn = db()
    rows = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    if not rows:
        bot.send_message(msg.chat.id, "ပစ္စည်းမရှိသေးပါ")
        return
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        left = stock_left(r["id"])
        kb.add(types.InlineKeyboardButton(f"{r['name']} (ကျန် {left})", callback_data=f"rmstockprod_{r['id']}"))
    bot.send_message(msg.chat.id, "Stock ဖျက်မည့် ပစ္စည်းကို ရွေးပါ:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("rmstockprod_"))
def list_stock_items(call):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.split("_")[1])
    conn = db()
    items = conn.execute(
        "SELECT * FROM stock WHERE product_id=? AND sold=0 ORDER BY id LIMIT 20", (pid,)
    ).fetchall()
    conn.close()
    bot.answer_callback_query(call.id)
    if not items:
        bot.send_message(call.message.chat.id, "ဒီပစ္စည်းအတွက် မရောင်းရသေးသော stock မရှိပါ")
        return
    kb = types.InlineKeyboardMarkup()
    for it in items:
        label = it["data"] if len(it["data"]) <= 30 else it["data"][:27] + "..."
        kb.add(types.InlineKeyboardButton(f"🗑 {label}", callback_data=f"rmstock_{it['id']}"))
    bot.send_message(
        call.message.chat.id,
        "ဖျက်လိုသော stock item ကို ရွေးပါ (ပထမ ၂၀ ခုသာ ပြထားပါသည်):",
        reply_markup=kb
    )

@bot.callback_query_handler(func=lambda c: c.data.startswith("rmstock_"))
def remove_stock_item(call):
    if not is_admin(call.from_user.id):
        return
    sid = int(call.data.split("_")[1])
    with stock_lock:
        conn = db()
        item = conn.execute("SELECT * FROM stock WHERE id=? AND sold=0", (sid,)).fetchone()
        if not item:
            conn.close()
            bot.answer_callback_query(call.id, "Item မတွေ့ပါ (ရောင်းပြီးသားလည်း ဖြစ်နိုင်ပါတယ်)", show_alert=True)
            return
        conn.execute("DELETE FROM stock WHERE id=?", (sid,))
        conn.commit()
        remaining = stock_left(item["product_id"])
        conn.close()
    bot.answer_callback_query(call.id, "✅ ဖျက်ပြီးပါပြီ")
    bot.send_message(call.message.chat.id, f"🗑 Stock item ကို ဖျက်ပြီးပါပြီ\nကျန်ရှိ stock: {remaining}")

# ============ ADMIN: MANAGE PRODUCTS ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "✏️ ပစ္စည်းစီမံရန်")
def manage_products_start(msg):
    conn = db()
    rows = conn.execute("SELECT * FROM products").fetchall()
    conn.close()
    if not rows:
        bot.send_message(msg.chat.id, "ပစ္စည်းမရှိသေးပါ")
        return
    kb = types.InlineKeyboardMarkup()
    for r in rows:
        kb.add(types.InlineKeyboardButton(f"{r['name']} - {r['price']} MMK", callback_data=f"editprod_{r['id']}"))
    bot.send_message(msg.chat.id, "စီမံလိုသော ပစ္စည်းကို ရွေးပါ:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("editprod_"))
def edit_product_menu(call):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.split("_")[1])
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✏️ နာမည်ပြောင်းရန်", callback_data=f"editname_{pid}"))
    kb.add(types.InlineKeyboardButton("💵 ဈေးနှုန်းပြောင်းရန်", callback_data=f"editprice_{pid}"))
    kb.add(types.InlineKeyboardButton("📝 ဖော်ပြချက်ပြောင်းရန်", callback_data=f"editdesc_{pid}"))
    kb.add(types.InlineKeyboardButton("🗑 ပစ္စည်းဖျက်ရန် (stock အားလုံးပါ ပျက်မည်)", callback_data=f"delprod_{pid}"))
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "ဘာလုပ်မလဲ ရွေးပါ:", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("editname_") or c.data.startswith("editprice_") or c.data.startswith("editdesc_"))
def edit_product_field(call):
    if not is_admin(call.from_user.id):
        return
    field, pid = call.data.split("_")
    pid = int(pid)
    step_map = {"editname": "editprod_name", "editprice": "editprod_price", "editdesc": "editprod_desc"}
    prompt_map = {
        "editname": "အသစ် နာမည် ရိုက်ထည့်ပါ:",
        "editprice": "အသစ် ဈေးနှုန်း (ဂဏန်းသာ) ရိုက်ထည့်ပါ:",
        "editdesc": "အသစ် ဖော်ပြချက် ရိုက်ထည့်ပါ — ဖျက်ချင်ရင် - ကို ရိုက်ပါ:",
    }
    user_state[call.from_user.id] = {"step": step_map[field], "product_id": pid}
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, prompt_map[field])

@bot.callback_query_handler(func=lambda c: c.data.startswith("delprod_"))
def confirm_delete_product(call):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.split("_")[1])
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("✅ သေချာပါတယ် ဖျက်မည်", callback_data=f"delprodyes_{pid}"),
        types.InlineKeyboardButton("❌ မဖျက်တော့ပါ", callback_data="delprodno"),
    )
    bot.answer_callback_query(call.id)
    bot.send_message(call.message.chat.id, "⚠️ ဒီပစ္စည်းနှင့် stock အားလုံးကို ဖျက်မှာ သေချာပါသလား?", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("delprodyes_") or c.data == "delprodno")
def delete_product(call):
    if not is_admin(call.from_user.id):
        return
    if call.data == "delprodno":
        bot.answer_callback_query(call.id, "ပယ်ဖျက်လိုက်ပါပြီ")
        bot.send_message(call.message.chat.id, "ဖျက်ခြင်း မလုပ်တော့ပါ", reply_markup=admin_menu())
        return
    pid = int(call.data.split("_")[1])
    conn = db()
    product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
    conn.execute("DELETE FROM stock WHERE product_id=?", (pid,))
    conn.execute("DELETE FROM products WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    bot.answer_callback_query(call.id, "✅ ဖျက်ပြီးပါပြီ")
    name = product["name"] if product else str(pid)
    bot.send_message(call.message.chat.id, f"🗑 '{name}' ပစ္စည်းနှင့် stock အားလုံးကို ဖျက်ပြီးပါပြီ", reply_markup=admin_menu())

# ============ ADMIN: ADD USER BALANCE ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "💵 User ငွေဖြည့်ရန်")
def add_balance_start(msg):
    user_state[msg.from_user.id] = {"step": "topup_userid"}
    bot.send_message(msg.chat.id, "User ID ရိုက်ထည့်ပါ:")

# ============ ADMIN: MANUAL SELL ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "🧾 တိုက်ရိုက်ရောင်းရန်")
def manual_sell_start(msg):
    user_state[msg.from_user.id] = {"step": "manualsell_userid"}
    bot.send_message(msg.chat.id, "ရောင်းမည့် User ID ရိုက်ထည့်ပါ:")

# ============ ADMIN: BROADCAST ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "📢 အသိပေးစာပို့ရန်")
def broadcast_start(msg):
    user_state[msg.from_user.id] = {"step": "broadcast_text"}
    bot.send_message(msg.chat.id, "User အားလုံးဆီ ပို့မည့် message ရိုက်ထည့်ပါ:")

# ============ ADMIN: PENDING TOP-UPS ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "⏳ စောင့်ဆိုင်းနေသော ငွေဖြည့်များ")
def pending_topups(msg):
    conn = db()
    rows = conn.execute("SELECT * FROM topups WHERE status='pending' ORDER BY id").fetchall()
    conn.close()
    if not rows:
        bot.send_message(msg.chat.id, "⏳ စောင့်ဆိုင်းနေသော ငွေဖြည့်တောင်းဆိုမှု မရှိပါ")
        return
    for r in rows:
        kb = types.InlineKeyboardMarkup()
        kb.add(
            types.InlineKeyboardButton("✅ လက်ခံမည်", callback_data=f"topup_ok_{r['id']}"),
            types.InlineKeyboardButton("❌ ငြင်းပယ်မည်", callback_data=f"topup_no_{r['id']}"),
        )
        bot.send_message(
            msg.chat.id,
            f"💳 Top-up request #{r['id']}: user {r['user_id']}\n"
            f"နည်းလမ်း: {PAYMENT_INFO.get(r['method'], {}).get('label', r['method'])} | ပမာဏ: {r['amount']} MMK",
            reply_markup=kb
        )

# ============ ADMIN: SALES REPORT ============
@bot.message_handler(func=lambda m: is_admin(m.from_user.id) and m.text == "📈 အရောင်းစာရင်း")
def sales_report(msg):
    conn = db()
    total = conn.execute("SELECT COUNT(*) c, COALESCE(SUM(price),0) s FROM orders").fetchone()
    recent = conn.execute("""SELECT o.*, p.name FROM orders o JOIN products p ON o.product_id=p.id
                              ORDER BY o.id DESC LIMIT 10""").fetchall()
    conn.close()
    text = f"📈 <b>စုစုပေါင်းရောင်းအား</b>: {total['c']} ခု | {total['s']} MMK\n\n<b>နောက်ဆုံး ၁၀ ခု:</b>\n"
    for r in recent:
        text += f"• {r['name']} → user {r['user_id']} ({r['price']} MMK, {r['method']})\n"
    bot.send_message(msg.chat.id, text)

# ============ TEXT INPUT HANDLER ============
@bot.message_handler(func=lambda m: m.from_user.id in user_state)
def handle_state_input(msg):
    uid = msg.from_user.id
    state = user_state[uid]
    step = state["step"]
    conn = db()

    if step == "add_product_name":
        state["name"] = msg.text.strip()
        state["step"] = "add_product_price"
        bot.send_message(msg.chat.id, "ဈေးနှုန်း (ဂဏန်းသာ) ရိုက်ထည့်ပါ:")

    elif step == "add_product_price":
        try:
            price = int(msg.text.strip())
        except ValueError:
            bot.send_message(msg.chat.id, "ဂဏန်းသာ ရိုက်ပါ")
            conn.close()
            return
        state["price"] = price
        state["step"] = "add_product_desc"
        bot.send_message(msg.chat.id, "ပစ္စည်း ဖော်ပြချက် (description) ရိုက်ထည့်ပါ — မထည့်ချင်ရင် - ကို ရိုက်ပါ:")

    elif step == "add_product_desc":
        desc = msg.text.strip()
        if desc == "-":
            desc = ""
        conn.execute(
            "INSERT INTO products(name, price, description) VALUES(?,?,?)",
            (state["name"], state["price"], desc)
        )
        conn.commit()
        bot.send_message(msg.chat.id, f"✅ ပစ္စည်း '{state['name']}' ({state['price']} MMK) ထည့်ပြီးပါပြီ", reply_markup=admin_menu())
        del user_state[uid]

    elif step == "add_stock_data":
        pid = state["product_id"]
        lines = [l.strip() for l in msg.text.split("\n") if l.strip()]
        for line in lines:
            conn.execute("INSERT INTO stock(product_id, data, sold) VALUES(?,?,0)", (pid, line))
        conn.commit()
        product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        total_left = stock_left(pid)
        bot.send_message(msg.chat.id, f"✅ Stock {len(lines)} ခု ထည့်ပြီးပါပြီ (ကျန်: {total_left})", reply_markup=admin_menu())
        del user_state[uid]
        sent = broadcast_to_all(
            f"🎉 <b>{product['name']}</b> Stock အသစ်ဝင်ပါပြီ!\n💵 ဈေးနှုန်း: {product['price']} MMK\n📦 ကျန်ရှိ: {total_left} ခု\n\n🛒 ကုန်ပစ္စည်းများ ကနေ ဝယ်ယူနိုင်ပါပြီ"
        )
        bot.send_message(msg.chat.id, f"📢 User {sent} ဦးဆီ stock ဝင်ကြောင်း အသိပေးပြီးပါပြီ")

    elif step == "editprod_name":
        pid = state["product_id"]
        new_name = msg.text.strip()
        conn.execute("UPDATE products SET name=? WHERE id=?", (new_name, pid))
        conn.commit()
        bot.send_message(msg.chat.id, f"✅ ပစ္စည်းနာမည်ကို '{new_name}' အဖြစ် ပြောင်းပြီးပါပြီ", reply_markup=admin_menu())
        del user_state[uid]

    elif step == "editprod_price":
        try:
            price = int(msg.text.strip())
        except ValueError:
            bot.send_message(msg.chat.id, "ဂဏန်းသာ ရိုက်ပါ")
            conn.close()
            return
        pid = state["product_id"]
        conn.execute("UPDATE products SET price=? WHERE id=?", (price, pid))
        conn.commit()
        bot.send_message(msg.chat.id, f"✅ ဈေးနှုန်းကို {price} MMK အဖြစ် ပြောင်းပြီးပါပြီ", reply_markup=admin_menu())
        del user_state[uid]

    elif step == "editprod_desc":
        pid = state["product_id"]
        desc = msg.text.strip()
        if desc == "-":
            desc = ""
        conn.execute("UPDATE products SET description=? WHERE id=?", (desc, pid))
        conn.commit()
        bot.send_message(msg.chat.id, "✅ ဖော်ပြချက် ပြောင်းပြီးပါပြီ", reply_markup=admin_menu())
        del user_state[uid]

    elif step == "selftopup_amount":
        try:
            amount = int(msg.text.strip())
        except ValueError:
            bot.send_message(msg.chat.id, "ဂဏန်းသက်သက် ရိုက်ပါ")
            conn.close()
            return
        if amount <= 0:
            bot.send_message(msg.chat.id, "ပမာဏမှန်ကန်အောင် ရိုက်ပါ")
            conn.close()
            return
        state["amount"] = amount
        state["step"] = "selftopup_photo"
        bot.send_message(msg.chat.id, "✅ လွှဲပြီးသား Payment Screenshot ကို ဓာတ်ပုံအနေနဲ့ ပို့ပေးပါ 📸")

    elif step == "topup_userid":
        try:
            target = int(msg.text.strip())
        except ValueError:
            bot.send_message(msg.chat.id, "User ID ဂဏန်းသာ ရိုက်ပါ")
            conn.close()
            return
        state["target"] = target
        state["step"] = "topup_amount"
        bot.send_message(msg.chat.id, "ဖြည့်မည့်ပမာဏ ရိုက်ထည့်ပါ:")

    elif step == "topup_amount":
        try:
            amount = int(msg.text.strip())
        except ValueError:
            bot.send_message(msg.chat.id, "ဂဏန်းသာ ရိုက်ပါ")
            conn.close()
            return
        target = state["target"]
        conn.execute("INSERT OR IGNORE INTO users(user_id, balance) VALUES(?,0)", (target,))
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id=?", (amount, target))
        conn.commit()
        bot.send_message(msg.chat.id, f"✅ User {target} အတွက် {amount} MMK ဖြည့်ပြီးပါပြီ", reply_markup=admin_menu())
        try:
            bot.send_message(target, f"💰 သင့်အကောင့်ထဲသို့ {amount} MMK ဖြည့်ပေးလိုက်ပါပြီ")
        except Exception:
            pass
        del user_state[uid]

    elif step == "broadcast_text":
        del user_state[uid]
        sent = broadcast_to_all(msg.text)
        bot.send_message(msg.chat.id, f"📢 User {sent} ဦးဆီ ပို့ပြီးပါပြီ", reply_markup=admin_menu())

    elif step == "manualsell_userid":
        try:
            target = int(msg.text.strip())
        except ValueError:
            bot.send_message(msg.chat.id, "User ID ဂဏန်းသာ ရိုက်ပါ")
            conn.close()
            return
        manual_sell_targets[uid] = target
        products = conn.execute("SELECT * FROM products").fetchall()
        kb = types.InlineKeyboardMarkup()
        for p in products:
            left = conn.execute("SELECT COUNT(*) c FROM stock WHERE product_id=? AND sold=0", (p["id"],)).fetchone()["c"]
            kb.add(types.InlineKeyboardButton(f"{p['name']} (ကျန် {left})", callback_data=f"msell_{p['id']}"))
        del user_state[uid]
        bot.send_message(msg.chat.id, "ရောင်းမည့် ပစ္စည်းရွေးပါ:", reply_markup=kb)

    conn.close()

# ============ ADMIN: MANUAL SELL - CHOOSE PRODUCT ============
@bot.callback_query_handler(func=lambda c: c.data.startswith("msell_"))
def manual_sell_finish(call):
    if not is_admin(call.from_user.id):
        return
    pid = int(call.data.split("_")[1])
    target = manual_sell_targets.pop(call.from_user.id, None)
    if target is None:
        bot.answer_callback_query(call.id, "User ID ပျောက်သွားပါတယ်၊ 🧾 တိုက်ရိုက်ရောင်းရန် ကနေ ပြန်စပါ", show_alert=True)
        return

    with stock_lock:
        conn = db()
        product = conn.execute("SELECT * FROM products WHERE id=?", (pid,)).fetchone()
        stock_item = conn.execute("SELECT * FROM stock WHERE product_id=? AND sold=0 LIMIT 1", (pid,)).fetchone()
        if not stock_item:
            bot.answer_callback_query(call.id, "Stock ကုန်သွားပါပြီ", show_alert=True)
            conn.close()
            return
        conn.execute("UPDATE stock SET sold=1, sold_to=? WHERE id=?", (target, stock_item["id"]))
        conn.execute(
            "INSERT INTO orders(user_id, product_id, stock_id, price, method) VALUES(?,?,?,?,?)",
            (target, pid, stock_item["id"], product["price"], "manual")
        )
        conn.commit()
        remaining = stock_left(pid)
        conn.close()

    bot.answer_callback_query(call.id, "✅ ရောင်းပြီးပါပြီ")
    bot.send_message(call.message.chat.id, f"✅ {product['name']} ကို user {target} ဆီ ရောင်းလိုက်ပါပြီ\nကျန်ရှိ stock: {remaining}")
    try:
        bot.send_message(target, f"🎁 Admin ကနေ <b>{product['name']}</b> ပို့ပေးလိုက်ပါတယ်:\n\n<code>{stock_item['data']}</code>")
    except Exception:
        pass
    if remaining == 0:
        notify_admins(f"⚠️ <b>{product['name']}</b> Stock လုံးဝကုန်သွားပါပြီ! Stock အသစ်ထည့်ပေးပါ။")
        broadcast_to_all(f"⚠️ <b>{product['name']}</b> Stock လောလောဆယ်ကုန်သွားပါပြီ။ Stock အသစ်ဝင်ရင် အသိပေးပါမယ်။")

# ============ RUN ============
if __name__ == "__main__":
    init_db()
    print("Premium Store Bot စတင်နေပါပြီ...")
    bot.infinity_polling(skip_pending=True)
