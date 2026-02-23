import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta
import csv
import requests
import os
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

DB_PATH = "words.db"
REFRESH_INTERVAL = 5000
ACTIVE_THRESHOLD_MINUTES = 5

# ==========================================
# ПАЛІТРИ ДИЗАЙНУ (Dark / Light)
# ==========================================
THEMES = {
    "dark": {
        "name": "🌙 Темна тема", "bg": "#28243D", "sidebar": "#201C30", "card": "#312D4B",
        "text": "#E7E3FC", "muted": "#8A8D93", "accent": "#9155FD", "accent_hover": "#804BDF",
        "success": "#56CA00", "danger": "#FF4C51", "input_bg": "#28243D", "row_even": "#312D4B", "row_odd": "#2C2843"
    },
    "light": {
        "name": "☀️ Світла тема", "bg": "#FFF0F0", "sidebar": "#FFFFFF", "card": "#FFFFFF",
        "text": "#4A4A4A", "muted": "#9CA3AF", "accent": "#FF6B6B", "accent_hover": "#FA5252",
        "success": "#20C997", "danger": "#FA5252", "input_bg": "#F9FAFB", "row_even": "#FFFFFF", "row_odd": "#FFF8F8"
    }
}

FONT_MAIN = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 13, "bold")


def fix_db_safe():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, 
        message TEXT, date TEXT, status TEXT DEFAULT 'Новий'
    )
    """)
    try:
        cursor.execute("ALTER TABLE user_words ADD COLUMN language TEXT")
    except:
        pass
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN best_score INTEGER DEFAULT 0")
    except:
        pass
    conn.commit()
    conn.close()


class AdminApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Word Sprint - Панель Адміністратора")
        self.geometry("1150x720")  # Трохи ширше, щоб все ідеально влазило

        self.current_theme = "dark"
        self.colors = THEMES[self.current_theme]
        self.style = ttk.Style()
        self.style.theme_use('clam')

        self.theme_widgets = {'bg': [], 'sidebar': [], 'card': [], 'text': [], 'btn_accent': [], 'input': [],
                              'labels': []}

        self.setup_ui()
        self.apply_theme()

        self.selected_user_id = None
        self.update_users_table()
        self.update_feedback_table()

    def setup_ui(self):
        self.main_container = tk.Frame(self)
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.theme_widgets['bg'].append(self.main_container)

        # --- БОКОВЕ МЕНЮ ---
        self.sidebar = tk.Frame(self.main_container, width=220)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)
        self.theme_widgets['sidebar'].append(self.sidebar)

        lbl_logo = tk.Label(self.sidebar, text="🔥 Word Sprint", font=("Segoe UI", 16, "bold"))
        lbl_logo.pack(pady=25, padx=15, anchor="w")
        self.theme_widgets['labels'].append(lbl_logo)

        self.btn_nav_dash = self.create_nav_button(self.sidebar, "📊 Дашборд та Слова", lambda: self.switch_tab(0))
        self.btn_nav_msg = self.create_nav_button(self.sidebar, "✉️ Повідомлення", lambda: self.switch_tab(1))

        self.btn_theme = tk.Button(self.sidebar, text=self.colors["name"], font=FONT_BOLD, relief="flat",
                                   cursor="hand2", command=self.toggle_theme)
        self.btn_theme.pack(side=tk.BOTTOM, fill=tk.X, pady=15, padx=15, ipady=8)
        self.theme_widgets['btn_accent'].append(self.btn_theme)

        # --- КОНТЕНТ ---
        self.content_area = tk.Frame(self.main_container)
        self.content_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=15, pady=15)
        self.theme_widgets['bg'].append(self.content_area)

        self.notebook = ttk.Notebook(self.content_area)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.style.layout("TNotebook.Tab", [])

        self.tab_dashboard = ttk.Frame(self.notebook)
        self.tab_messages = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_dashboard)
        self.notebook.add(self.tab_messages)

        self.build_dashboard_tab()
        self.build_messages_tab()
        self.switch_tab(0)

    def create_nav_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, font=FONT_BOLD, relief="flat", cursor="hand2", anchor="w", padx=15, pady=10,
                        command=command)
        btn.pack(fill=tk.X, padx=10, pady=5)
        self.theme_widgets['card'].append(btn)
        return btn

    def create_action_button(self, parent, text, command):
        btn = tk.Button(parent, text=text, font=FONT_BOLD, relief="flat", cursor="hand2", padx=15, pady=6,
                        command=command)
        self.theme_widgets['btn_accent'].append(btn)
        return btn

    def create_card(self, parent, title):
        frame = ttk.Frame(parent, style="Card.TFrame")
        if title:
            lbl = ttk.Label(frame, text=title, font=FONT_TITLE, style="CardTitle.TLabel")
            lbl.pack(anchor="w", pady=(5, 10), padx=5)
        return frame

    def switch_tab(self, index):
        self.notebook.select(index)
        act_bg = self.colors['accent']
        act_fg = "#FFFFFF"
        inact_bg = self.colors['card']
        inact_fg = self.colors['text']
        self.btn_nav_dash.configure(bg=act_bg if index == 0 else inact_bg, fg=act_fg if index == 0 else inact_fg)
        self.btn_nav_msg.configure(bg=act_bg if index == 1 else inact_bg, fg=act_fg if index == 1 else inact_fg)

    def toggle_theme(self):
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self.colors = THEMES[self.current_theme]
        self.btn_theme.configure(text=self.colors["name"])
        self.apply_theme()
        self.switch_tab(self.notebook.index(self.notebook.select()))
        self.update_users_table()
        self.update_feedback_table()

    def apply_theme(self):
        c = self.colors
        self.configure(bg=c['bg'])

        for w in self.theme_widgets['bg']: w.configure(bg=c['bg'])
        for w in self.theme_widgets['sidebar']: w.configure(bg=c['sidebar'])
        for w in self.theme_widgets['labels']: w.configure(bg=c['sidebar'], fg=c['text'])
        for w in self.theme_widgets['card']: w.configure(bg=c['card'], fg=c['text'], activebackground=c['row_odd'],
                                                         activeforeground=c['text'])
        for w in self.theme_widgets['btn_accent']: w.configure(bg=c['accent'], fg="#FFFFFF",
                                                               activebackground=c['accent_hover'],
                                                               activeforeground="#FFFFFF")
        for w in self.theme_widgets['input']: w.configure(bg=c['input_bg'], fg=c['text'], insertbackground=c['text'])

        self.style.configure("TNotebook", background=c['bg'])
        self.style.configure("TFrame", background=c['bg'])
        self.style.configure("Card.TFrame", background=c['card'])
        self.style.configure("CardTitle.TLabel", background=c['card'], foreground=c['accent'])

        self.style.configure("Treeview", background=c['card'], fieldbackground=c['card'], foreground=c['text'],
                             rowheight=30, borderwidth=0, font=FONT_MAIN)
        self.style.map("Treeview", background=[('selected', c['accent'])], foreground=[('selected', '#FFFFFF')])
        self.style.configure("Treeview.Heading", font=FONT_BOLD, background=c['row_odd'], foreground=c['muted'],
                             padding=5, borderwidth=0)
        self.style.map("Treeview.Heading", background=[('active', c['row_even'])])

    def build_dashboard_tab(self):
        # ---------------------------------------------------------
        # ЛІВА КОЛОНКА (ВУЗЬКА) - Зменшив до 370px, щоб словник влазив
        # ---------------------------------------------------------
        left_col = tk.Frame(self.tab_dashboard, width=370)
        left_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        left_col.pack_propagate(False)
        self.theme_widgets['bg'].append(left_col)

        # ПРАВА КОЛОНКА (ШИРОКА) - Словник займає весь інший простір
        right_col = tk.Frame(self.tab_dashboard)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.theme_widgets['bg'].append(right_col)

        # 1. КОРИСТУВАЧІ
        card_users = self.create_card(left_col, "👥 Користувачі")
        card_users.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.users_tree = ttk.Treeview(card_users, columns=("id", "name", "date", "active"), show="headings", height=8)
        self.users_tree.heading("id", text="ID", command=lambda: self.sort_by_column(self.users_tree, "id", False))
        self.users_tree.column("id", width=40, stretch=False, anchor="center")
        self.users_tree.heading("name", text="Юзернейм",
                                command=lambda: self.sort_by_column(self.users_tree, "name", False))
        self.users_tree.column("name", width=120, stretch=True, anchor="w")
        self.users_tree.heading("date", text="Реєстрація",
                                command=lambda: self.sort_by_column(self.users_tree, "date", False))
        self.users_tree.column("date", width=80, stretch=False, anchor="center")
        self.users_tree.heading("active", text="Статус",
                                command=lambda: self.sort_by_column(self.users_tree, "active", False))
        self.users_tree.column("active", width=80, stretch=False, anchor="center")

        self.users_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.users_tree.bind("<<TreeviewSelect>>", self.on_user_select)

        # 2. ПРОГРЕС (МОВИ)
        card_stats = self.create_card(left_col, "📊 Мови (Натисніть для фільтру)")
        card_stats.pack(fill=tk.X)
        self.stats_tree = ttk.Treeview(card_stats, columns=("lang", "count", "xp"), show="headings", height=5)
        self.stats_tree.heading("lang", text="Мова")
        self.stats_tree.column("lang", width=120, stretch=True, anchor="center")
        self.stats_tree.heading("count", text="Слів")
        self.stats_tree.column("count", width=70, stretch=False, anchor="center")
        self.stats_tree.heading("xp", text="Бали")
        self.stats_tree.column("xp", width=70, stretch=False, anchor="center")

        self.stats_tree.pack(fill=tk.X, padx=5, pady=5)
        self.stats_tree.bind("<<TreeviewSelect>>", self.on_stat_select)

        # 3. СЛОВНИКИ (ПРАВА ПАНЕЛЬ)
        card_words = self.create_card(right_col, "")
        card_words.pack(fill=tk.BOTH, expand=True)

        self.lbl_selected = tk.Label(card_words, text="Оберіть користувача зліва 👈", font=FONT_TITLE)
        self.lbl_selected.pack(pady=(0, 5))
        self.theme_widgets['labels'].append(self.lbl_selected)

        self.lbl_filter = tk.Label(card_words, text="📚 Словник (Фільтр: Усі мови)", font=FONT_BOLD)
        self.lbl_filter.pack(pady=(0, 10))
        self.theme_widgets['labels'].append(self.lbl_filter)

        # Динамічні колонки, щоб "все влазило"
        self.words_tree = ttk.Treeview(card_words, columns=("word", "trans", "lang", "usage"), show="headings")
        self.words_tree.heading("word", text="Слово",
                                command=lambda: self.sort_by_column(self.words_tree, "word", False))
        self.words_tree.column("word", width=160, stretch=True, anchor="w")
        self.words_tree.heading("trans", text="Переклад",
                                command=lambda: self.sort_by_column(self.words_tree, "trans", False))
        self.words_tree.column("trans", width=160, stretch=True, anchor="w")
        self.words_tree.heading("lang", text="Мова",
                                command=lambda: self.sort_by_column(self.words_tree, "lang", False))
        self.words_tree.column("lang", width=90, stretch=False, anchor="center")
        self.words_tree.heading("usage", text="Успішність",
                                command=lambda: self.sort_by_column(self.words_tree, "usage", False))
        self.words_tree.column("usage", width=90, stretch=False, anchor="center")

        self.words_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Інструменти
        frame_tools = ttk.Frame(card_words, style="Card.TFrame")
        frame_tools.pack(fill=tk.X, padx=5, pady=10)
        btn_exp = self.create_action_button(frame_tools, "📥 Експорт", self.export_csv)
        btn_exp.pack(side=tk.RIGHT, padx=5)
        btn_imp = self.create_action_button(frame_tools, "📤 Імпорт", self.import_csv)
        btn_imp.pack(side=tk.RIGHT, padx=5)

    def build_messages_tab(self):
        # --- РОЗСИЛКА ТА ПОШУК КОРИСТУВАЧА ---
        card_broadcast = self.create_card(self.tab_messages, "📢 Надіслати повідомлення")
        card_broadcast.pack(fill=tk.X, pady=(0, 15))

        frame_recip = ttk.Frame(card_broadcast, style="Card.TFrame")
        frame_recip.pack(fill=tk.X, padx=10, pady=5)

        lbl_recip = tk.Label(frame_recip, text="Одержувач (пошук):", font=FONT_BOLD)
        lbl_recip.pack(side=tk.LEFT)
        self.theme_widgets['labels'].append(lbl_recip)

        self.recipient_var = tk.StringVar()
        self.combo_users = ttk.Combobox(frame_recip, textvariable=self.recipient_var, font=FONT_MAIN, width=40)
        self.combo_users.pack(side=tk.LEFT, padx=10)
        self.combo_users.set("Всі користувачі")

        self.txt_broadcast = tk.Text(card_broadcast, height=4, font=FONT_MAIN, relief="flat", highlightthickness=1)
        self.txt_broadcast.pack(fill=tk.X, padx=10, pady=5)
        self.theme_widgets['input'].append(self.txt_broadcast)

        btn_frame = ttk.Frame(card_broadcast, style="Card.TFrame")
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        self.create_action_button(btn_frame, "Відправити 🚀", self.send_broadcast).pack(side=tk.RIGHT)

        # --- ВІДГУКИ ---
        card_fb = self.create_card(self.tab_messages, "💬 Відгуки та Ідеї")
        card_fb.pack(fill=tk.BOTH, expand=True)

        self.fb_tree = ttk.Treeview(card_fb, columns=("id", "user", "date", "msg", "status"), show="headings")
        cols_fb = {"id": "ID", "user": "Користувач", "date": "Дата", "msg": "Повідомлення", "status": "Статус"}
        for col, name in cols_fb.items():
            self.fb_tree.heading(col, text=name, command=lambda c=col: self.sort_by_column(self.fb_tree, c, False))

        self.fb_tree.column("id", width=30, anchor="center")
        self.fb_tree.column("user", width=120)
        self.fb_tree.column("date", width=100, anchor="center")
        self.fb_tree.column("msg", width=400, stretch=True, anchor="w")
        self.fb_tree.column("status", width=90, anchor="center")
        self.fb_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        btn_reply = self.create_action_button(card_fb, "✍️ Відповісти юзеру", self.reply_feedback)
        btn_reply.pack(side=tk.RIGHT, padx=10, pady=5)
        btn_read = self.create_action_button(card_fb, "✅ Прочитано", self.mark_feedback_read)
        btn_read.pack(side=tk.RIGHT, padx=5, pady=5)

    # ================= ЛОГІКА РОБОТИ =================
    def send_broadcast(self):
        msg = self.txt_broadcast.get("1.0", tk.END).strip()
        recipient = self.recipient_var.get()
        if not msg: return messagebox.showwarning("Увага", "Введіть текст!")
        if not TELEGRAM_BOT_TOKEN: return messagebox.showerror("Помилка", "Токен не знайдено!")

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        targets = []
        if recipient == "Всі користувачі":
            cur.execute("SELECT user_id FROM users")
            targets = [r[0] for r in cur.fetchall()]
        else:
            try:
                uid = int(recipient.split(" | ")[0])
                targets = [uid]
            except:
                messagebox.showerror("Помилка", "Невірно обраний користувач.")
                conn.close()
                return

        conn.close()

        success = 0
        for uid in targets:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            res = requests.post(url, json={"chat_id": uid, "text": msg, "parse_mode": "HTML"})
            if res.status_code == 200: success += 1

        messagebox.showinfo("Готово", f"Надіслано {success} повідомлень!")
        self.txt_broadcast.delete("1.0", tk.END)

    def reply_feedback(self):
        sel = self.fb_tree.selection()
        if not sel: return messagebox.showwarning("Увага", "Оберіть відгук у таблиці!")
        item = self.fb_tree.item(sel[0])
        fb_id, user_str = item["values"][0], item["values"][1]

        rw = tk.Toplevel(self)
        rw.title(f"Відповідь: {user_str}")
        rw.geometry("450x250")
        rw.configure(bg=self.colors['bg'])

        tk.Label(rw, text="Напишіть вашу відповідь:", font=FONT_BOLD, bg=self.colors['bg'],
                 fg=self.colors['text']).pack(pady=10)
        txt = tk.Text(rw, height=6, font=FONT_MAIN)
        txt.pack(padx=15, fill=tk.BOTH, expand=True)

        def send_reply():
            msg = txt.get("1.0", tk.END).strip()
            if not msg: return
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT user_id FROM feedback WHERE id=?", (fb_id,))
            res = cur.fetchone()
            if res:
                uid = res[0]
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                r = requests.post(url, json={"chat_id": uid, "text": f"💬 <b>Відповідь від Адміністратора:</b>\n\n{msg}",
                                             "parse_mode": "HTML"})
                if r.status_code == 200:
                    cur.execute("UPDATE feedback SET status='Відповіли' WHERE id=?", (fb_id,))
                    conn.commit()
                    self.update_feedback_table()
                    messagebox.showinfo("Успіх", "Відповідь надіслана!")
                    rw.destroy()
                else:
                    messagebox.showerror("Помилка", "Бот не зміг надіслати.")
            conn.close()

        tk.Button(rw, text="Надіслати", bg=self.colors['accent'], fg="#FFF", font=FONT_BOLD, relief="flat",
                  command=send_reply).pack(pady=10)

    def update_feedback_table(self):
        for row in self.fb_tree.get_children(): self.fb_tree.delete(row)
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, date, message, status FROM feedback ORDER BY id DESC")

            c = self.colors
            self.fb_tree.tag_configure("oddrow", background=c['row_odd'])
            self.fb_tree.tag_configure("evenrow", background=c['row_even'])
            self.fb_tree.tag_configure("new", background=c['accent'], foreground="#FFFFFF")
            self.fb_tree.tag_configure("replied", background=c['success'], foreground="#FFFFFF")

            for index, fb in enumerate(cursor.fetchall()):
                date_str = fb[2][:16] if fb[2] else ""
                tags = ("new",) if fb[4] == "Новий" else ("replied",) if fb[4] == "Відповіли" else (
                    "evenrow" if index % 2 == 0 else "oddrow",)
                self.fb_tree.insert("", tk.END,
                                    values=(fb[0], f"@{fb[1]}" if fb[1] else "Unknown", date_str, fb[3], fb[4]),
                                    tags=tags)
            conn.close()
        except:
            pass

    def mark_feedback_read(self):
        sel = self.fb_tree.selection()
        if not sel: return
        fb_id = self.fb_tree.item(sel[0])["values"][0]
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("UPDATE feedback SET status='Прочитано' WHERE id=?", (fb_id,))
            conn.commit()
            conn.close()
            self.update_feedback_table()
        except:
            pass

    def update_users_table(self):
        sel = self.users_tree.selection()
        sel_id = self.users_tree.item(sel[0])['values'][0] if sel else None
        for row in self.users_tree.get_children(): self.users_tree.delete(row)

        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, username, start_date, last_active, best_score FROM users")
            users = cursor.fetchall()
            conn.close()

            # Оновлюємо список для розсилки
            combo_vals = ["Всі користувачі"] + [f"{u[0]} | @{u[1]}" for u in users]
            self.combo_users['values'] = combo_vals

            now = datetime.now()
            rows = []
            for u in users:
                active = False
                if u[3]:
                    try:
                        if now - datetime.fromisoformat(u[3]) < timedelta(
                            minutes=ACTIVE_THRESHOLD_MINUTES): active = True
                    except:
                        pass
                rows.append((active, u))
            rows.sort(key=lambda x: (not x[0], x[1][0]))

            c = self.colors
            self.users_tree.tag_configure("active", foreground=c['success'])
            self.users_tree.tag_configure("oddrow", background=c['row_odd'])
            self.users_tree.tag_configure("evenrow", background=c['row_even'])

            for index, (active, u) in enumerate(rows):
                tags = ("active",) if active else ("evenrow" if index % 2 == 0 else "oddrow",)
                reg_date = u[2][:10] if u[2] else "N/A"
                item = self.users_tree.insert("", tk.END, values=(u[0], f"@{u[1]}" if u[1] else "Без імені", reg_date,
                                                                  "Онлайн 🟢" if active else "Офлайн ⚪"), tags=tags)
                if sel_id and u[0] == sel_id: self.users_tree.selection_set(item)

            if self.selected_user_id: self.update_details(self.selected_user_id)
            self.update_feedback_table()
        except:
            pass

        self.after(REFRESH_INTERVAL, self.update_users_table)

    def on_stat_select(self, event):
        sel = self.stats_tree.selection()
        if sel and self.selected_user_id:
            lang = self.stats_tree.item(sel[0])["values"][0]
            self.load_words(self.selected_user_id, lang)

    def load_words(self, uid, filter_lang="Усі мови"):
        # Змінюємо напис фільтру
        self.lbl_filter.config(text=f"📚 Словник (Фільтр: {filter_lang})", fg=self.colors['text'])

        for r in self.words_tree.get_children(): self.words_tree.delete(r)
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            # Оригінальний запит
            if filter_lang and filter_lang != "Усі мови":
                cur.execute(
                    "SELECT word, translation, language, usage_count FROM user_words WHERE user_id=? AND language=?",
                    (uid, filter_lang))
            else:
                cur.execute("SELECT word, translation, language, usage_count FROM user_words WHERE user_id=?", (uid,))

            for index, w in enumerate(cur.fetchall()):
                tags = ("evenrow" if index % 2 == 0 else "oddrow",)
                self.words_tree.insert("", tk.END, values=w, tags=tags)

            self.words_tree.tag_configure("oddrow", background=self.colors['row_odd'])
            self.words_tree.tag_configure("evenrow", background=self.colors['row_even'])
            conn.close()
        except Exception as e:
            print(e)

    def update_details(self, uid):
        for r in self.stats_tree.get_children(): self.stats_tree.delete(r)

        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()

            cur.execute("SELECT username, best_score FROM users WHERE user_id=?", (uid,))
            data = cur.fetchone()
            if data:
                self.lbl_selected.config(text=f"👤 @{data[0]}   |   🎮 Рекорд: {data[1]}", fg=self.colors['accent'])

            cur.execute("SELECT language, COUNT(*), SUM(usage_count) FROM user_words WHERE user_id=? GROUP BY language",
                        (uid,))
            rows = cur.fetchall()

            total_w = sum(r[1] for r in rows)
            total_xp = sum(r[2] or 0 for r in rows)

            self.stats_tree.insert("", tk.END, values=("Усі мови", total_w, total_xp), tags=("evenrow",))

            for index, r in enumerate(rows):
                tags = ("oddrow" if index % 2 == 0 else "evenrow",)
                self.stats_tree.insert("", tk.END, values=(r[0] or "N/A", r[1], r[2] or 0), tags=tags)

            self.stats_tree.tag_configure("oddrow", background=self.colors['row_odd'])
            self.stats_tree.tag_configure("evenrow", background=self.colors['row_even'])
            conn.close()

            self.load_words(uid, "Усі мови")

        except:
            pass

    def on_user_select(self, event):
        sel = self.users_tree.selection()
        if sel:
            self.selected_user_id = self.users_tree.item(sel[0])["values"][0]
            self.update_details(self.selected_user_id)

    def sort_by_column(self, tree, col, reverse):
        l = [(tree.set(k, col), k) for k in tree.get_children('')]
        try:
            l.sort(key=lambda t: float(t[0]), reverse=reverse)
        except:
            l.sort(key=lambda t: t[0].lower(), reverse=reverse)
        for index, (val, k) in enumerate(l): tree.move(k, '', index)
        tree.heading(col, command=lambda: self.sort_by_column(tree, col, not reverse))

    def export_csv(self):
        if not self.selected_user_id: return messagebox.showwarning("Увага", "Оберіть користувача!")
        filepath = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV files", "*.csv")])
        if not filepath: return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT word, translation, language FROM user_words WHERE user_id=?", (self.selected_user_id,))
            words = cur.fetchall()
            conn.close()
            with open(filepath, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(['Word', 'Translation', 'Language'])
                writer.writerows(words)
            messagebox.showinfo("Успіх", f"Експортовано {len(words)} слів!")
        except Exception as e:
            messagebox.showerror("Помилка", str(e))

    def import_csv(self):
        if not self.selected_user_id: return messagebox.showwarning("Увага", "Оберіть користувача!")
        filepath = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if not filepath: return
        try:
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            count = 0
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                next(reader)
                for row in reader:
                    if len(row) >= 3:
                        cur.execute(
                            "INSERT OR IGNORE INTO user_words (user_id, word, translation, language) VALUES (?, ?, ?, ?)",
                            (self.selected_user_id, row[0], row[1], row[2]))
                        count += 1
            conn.commit()
            conn.close()
            messagebox.showinfo("Успіх", f"Імпортовано {count} слів!")
            self.update_details(self.selected_user_id)
        except Exception as e:
            messagebox.showerror("Помилка", str(e))


if __name__ == "__main__":
    fix_db_safe()
    app = AdminApp()
    app.mainloop()