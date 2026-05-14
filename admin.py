import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from datetime import datetime, timedelta
import csv
import requests
import os
import psycopg2
import sqlite3
from dotenv import load_dotenv

load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

REFRESH_INTERVAL = 5000
ACTIVE_THRESHOLD_MINUTES = 5


# РОЗУМНИЙ МЕНЕДЖЕР БАЗИ ДАНИХ 
class DBManager:
    def __init__(self):
        self.conn = None
        self.active_source = "Немає підключення"

    def connect(self):
        try:
            self.conn = psycopg2.connect(
                host=os.getenv("SERVER_IP", "127.0.0.1"),
                user=os.getenv("DB_USER", "postgres"),
                password=os.getenv("DB_PASSWORD", ""),
                dbname=os.getenv("DB_NAME", "word_bot_db"),
                port=os.getenv("DB_PORT", "5432"),
                connect_timeout=3,
            )
            self.active_source = "Домашній сервер (PostgreSQL)"
            print(f"✅ Адмінка підключена: {self.active_source}")
            return True
        except Exception as e:
            print(f"⚠️ Домашній сервер недоступний: {e}")

        neon_url = os.getenv("NEON_URL")
        if neon_url and "тут_буде_твій_лінк" not in neon_url:
            # 🛠 ВИПРАВЛЕННЯ: Робимо так, щоб psycopg2 розумів посилання
            neon_url = neon_url.replace("ssl=require", "sslmode=require")
            try:
                self.conn = psycopg2.connect(neon_url, connect_timeout=5)
                self.active_source = "Хмара Neon (PostgreSQL)"
                print(f"✅ Адмінка підключена: {self.active_source}")
                return True
            except Exception as e:
                print(f"⚠️ Помилка Neon: {e}")

        return False

    def execute(self, query, params=()):
        cur = self.conn.cursor()
        try:
            cur.execute(query, params)
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            raise e
        finally:
            cur.close()

    def fetchall(self, query, params=()):
        cur = self.conn.cursor()
        cur.execute(query, params)
        res = cur.fetchall()
        cur.close()
        return res

    def fetchone(self, query, params=()):
        cur = self.conn.cursor()
        cur.execute(query, params)
        res = cur.fetchone()
        cur.close()
        return res


db = DBManager()
if not db.connect():
    print("❌ КРИТИЧНА ПОМИЛКА: Жодна база даних (Домашня/Neon) не доступна!")
    exit()


def fix_db_safe():
    try:
        db.execute(
            "ALTER TABLE feedback ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'Новий'"
        )
    except:
        pass


# ПАЛІТРА
THEMES = {
    "dark": {
        "name": "🌙 Темна тема",
        "bg": "#28243D",
        "sidebar": "#201C30",
        "card": "#312D4B",
        "text": "#E7E3FC",
        "muted": "#8A8D93",
        "accent": "#9155FD",
        "accent_hover": "#804BDF",
        "success": "#56CA00",
        "danger": "#FF4C51",
        "input_bg": "#28243D",
        "row_even": "#312D4B",
        "row_odd": "#2C2843",
    },
    "light": {
        "name": "☀️ Світла тема",
        "bg": "#FFF0F0",
        "sidebar": "#FFFFFF",
        "card": "#FFFFFF",
        "text": "#4A4A4A",
        "muted": "#9CA3AF",
        "accent": "#FF6B6B",
        "accent_hover": "#FA5252",
        "success": "#20C997",
        "danger": "#FA5252",
        "input_bg": "#F9FAFB",
        "row_even": "#FFFFFF",
        "row_odd": "#FFF8F8",
    },
}

FONT_MAIN = ("Segoe UI", 10)
FONT_BOLD = ("Segoe UI", 10, "bold")
FONT_TITLE = ("Segoe UI", 13, "bold")


class AdminApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"WordBot Admin Panel ({db.active_source})")
        self.geometry("1250x720")

        self.current_theme = "dark"
        self.colors = THEMES[self.current_theme]
        self.style = ttk.Style()
        self.style.theme_use("clam")

        self.theme_widgets = {
            "bg": [],
            "sidebar": [],
            "card": [],
            "text": [],
            "btn_accent": [],
            "input": [],
            "labels": [],
        }

        self.setup_ui()
        self.apply_theme()

        self.selected_user_id = None
        self.update_users_table()
        self.update_feedback_table()

    def setup_ui(self):
        self.main_container = tk.Frame(self)
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.theme_widgets["bg"].append(self.main_container)

        self.sidebar = tk.Frame(self.main_container, width=220)
        self.sidebar.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar.pack_propagate(False)
        self.theme_widgets["sidebar"].append(self.sidebar)

        lbl_logo = tk.Label(
            self.sidebar, text="🔥 WordBot Admin", font=("Segoe UI", 14, "bold")
        )
        lbl_logo.pack(pady=25, padx=15, anchor="w")
        self.theme_widgets["labels"].append(lbl_logo)

        lbl_status = tk.Label(
            self.sidebar,
            text=f"Джерело:\n{db.active_source}",
            font=("Segoe UI", 9),
            fg=self.colors["success"],
        )
        lbl_status.pack(pady=(0, 20), padx=10)
        self.theme_widgets["labels"].append(lbl_status)

        self.btn_nav_dash = self.create_nav_button(
            self.sidebar, "📊 Дашборд та Слова", lambda: self.switch_tab(0)
        )
        self.btn_nav_msg = self.create_nav_button(
            self.sidebar, "✉️ Відгуки та Розсилка", lambda: self.switch_tab(1)
        )

        self.btn_backup = self.create_nav_button(
            self.sidebar, "💾 Зробити Бекап", self.create_local_backup
        )

        self.btn_theme = tk.Button(
            self.sidebar,
            text=self.colors["name"],
            font=FONT_BOLD,
            relief="flat",
            cursor="hand2",
            command=self.toggle_theme,
        )
        self.btn_theme.pack(side=tk.BOTTOM, fill=tk.X, pady=15, padx=15, ipady=8)
        self.theme_widgets["btn_accent"].append(self.btn_theme)

        self.content_area = tk.Frame(self.main_container)
        self.content_area.pack(
            side=tk.LEFT, fill=tk.BOTH, expand=True, padx=15, pady=15
        )
        self.theme_widgets["bg"].append(self.content_area)

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
        btn = tk.Button(
            parent,
            text=text,
            font=FONT_BOLD,
            relief="flat",
            cursor="hand2",
            anchor="w",
            padx=15,
            pady=10,
            command=command,
        )
        btn.pack(fill=tk.X, padx=10, pady=5)
        self.theme_widgets["card"].append(btn)
        return btn

    def create_action_button(self, parent, text, command):
        btn = tk.Button(
            parent,
            text=text,
            font=FONT_BOLD,
            relief="flat",
            cursor="hand2",
            padx=15,
            pady=6,
            command=command,
        )
        self.theme_widgets["btn_accent"].append(btn)
        return btn

    def create_card(self, parent, title):
        frame = ttk.Frame(parent, style="Card.TFrame")
        if title:
            lbl = ttk.Label(
                frame, text=title, font=FONT_TITLE, style="CardTitle.TLabel"
            )
            lbl.pack(anchor="w", pady=(5, 10), padx=5)
        return frame

    def switch_tab(self, index):
        self.notebook.select(index)
        act_bg, act_fg = self.colors["accent"], "#FFFFFF"
        inact_bg, inact_fg = self.colors["card"], self.colors["text"]
        self.btn_nav_dash.configure(
            bg=act_bg if index == 0 else inact_bg, fg=act_fg if index == 0 else inact_fg
        )
        self.btn_nav_msg.configure(
            bg=act_bg if index == 1 else inact_bg, fg=act_fg if index == 1 else inact_fg
        )
        self.btn_backup.configure(
            bg=inact_bg, fg=inact_fg
        ) 

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
        self.configure(bg=c["bg"])

        for w in self.theme_widgets["bg"]:
            w.configure(bg=c["bg"])
        for w in self.theme_widgets["sidebar"]:
            w.configure(bg=c["sidebar"])
        for w in self.theme_widgets["labels"]:
            w.configure(bg=c["sidebar"], fg=c["text"])
        for w in self.theme_widgets["card"]:
            w.configure(
                bg=c["card"],
                fg=c["text"],
                activebackground=c["row_odd"],
                activeforeground=c["text"],
            )
        for w in self.theme_widgets["btn_accent"]:
            w.configure(
                bg=c["accent"],
                fg="#FFFFFF",
                activebackground=c["accent_hover"],
                activeforeground="#FFFFFF",
            )
        for w in self.theme_widgets["input"]:
            w.configure(bg=c["input_bg"], fg=c["text"], insertbackground=c["text"])

        self.style.configure("TNotebook", background=c["bg"])
        self.style.configure("TFrame", background=c["bg"])
        self.style.configure("Card.TFrame", background=c["card"])
        self.style.configure(
            "CardTitle.TLabel", background=c["card"], foreground=c["accent"]
        )

        self.style.configure(
            "Treeview",
            background=c["card"],
            fieldbackground=c["card"],
            foreground=c["text"],
            rowheight=30,
            borderwidth=0,
            font=FONT_MAIN,
        )
        self.style.map(
            "Treeview",
            background=[("selected", c["accent"])],
            foreground=[("selected", "#FFFFFF")],
        )
        self.style.configure(
            "Treeview.Heading",
            font=FONT_BOLD,
            background=c["row_odd"],
            foreground=c["muted"],
            padding=5,
            borderwidth=0,
        )
        self.style.map("Treeview.Heading", background=[("active", c["row_even"])])

    def build_dashboard_tab(self):
        left_col = tk.Frame(self.tab_dashboard, width=550)
        left_col.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15))
        left_col.pack_propagate(False)
        self.theme_widgets["bg"].append(left_col)

        right_col = tk.Frame(self.tab_dashboard)
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.theme_widgets["bg"].append(right_col)

        # КОРИСТУВАЧІ
        card_users = self.create_card(left_col, "👥 Користувачі")
        card_users.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        self.users_tree = ttk.Treeview(
            card_users,
            columns=("id", "name", "style", "hobby", "score", "active"),
            show="headings",
            height=8,
        )
        self.users_tree.heading(
            "id",
            text="ID",
            command=lambda: self.sort_by_column(self.users_tree, "id", False),
        )
        self.users_tree.column("id", width=80, stretch=False, anchor="center")

        self.users_tree.heading(
            "name",
            text="Юзернейм",
            command=lambda: self.sort_by_column(self.users_tree, "name", False),
        )
        self.users_tree.column("name", width=100, stretch=True, anchor="w")

        self.users_tree.heading(
            "style",
            text="Психотип",
            command=lambda: self.sort_by_column(self.users_tree, "style", False),
        )
        self.users_tree.column("style", width=90, stretch=False, anchor="center")

        self.users_tree.heading(
            "hobby",
            text="Хобі",
            command=lambda: self.sort_by_column(self.users_tree, "hobby", False),
        )
        self.users_tree.column("hobby", width=120, stretch=True, anchor="w")

        self.users_tree.heading(
            "score",
            text="Рекорд",
            command=lambda: self.sort_by_column(self.users_tree, "score", False),
        )
        self.users_tree.column("score", width=60, stretch=False, anchor="center")

        self.users_tree.heading(
            "active",
            text="Статус",
            command=lambda: self.sort_by_column(self.users_tree, "active", False),
        )
        self.users_tree.column("active", width=70, stretch=False, anchor="center")

        self.users_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.users_tree.bind("<<TreeviewSelect>>", self.on_user_select)

        # ПРОГРЕС
        card_stats = self.create_card(left_col, "📊 Мови (Натисніть для фільтру)")
        card_stats.pack(fill=tk.X)
        self.stats_tree = ttk.Treeview(
            card_stats, columns=("lang", "count", "xp"), show="headings", height=5
        )
        self.stats_tree.heading("lang", text="Мова")
        self.stats_tree.column("lang", width=120, stretch=True, anchor="center")
        self.stats_tree.heading("count", text="Слів")
        self.stats_tree.column("count", width=70, stretch=False, anchor="center")
        self.stats_tree.heading("xp", text="Повторень (XP)")
        self.stats_tree.column("xp", width=100, stretch=False, anchor="center")

        self.stats_tree.pack(fill=tk.X, padx=5, pady=5)
        self.stats_tree.bind("<<TreeviewSelect>>", self.on_stat_select)

        # СЛОВНИК
        card_words = self.create_card(right_col, "")
        card_words.pack(fill=tk.BOTH, expand=True)

        self.lbl_selected = tk.Label(
            card_words, text="Оберіть користувача зліва 👈", font=FONT_TITLE
        )
        self.lbl_selected.pack(pady=(0, 5))
        self.theme_widgets["labels"].append(self.lbl_selected)

        self.lbl_filter = tk.Label(
            card_words, text="📚 Словник (Фільтр: Усі мови)", font=FONT_BOLD
        )
        self.lbl_filter.pack(pady=(0, 10))
        self.theme_widgets["labels"].append(self.lbl_filter)

        self.words_tree = ttk.Treeview(
            card_words, columns=("word", "trans", "lang", "interval"), show="headings"
        )
        self.words_tree.heading(
            "word",
            text="Слово",
            command=lambda: self.sort_by_column(self.words_tree, "word", False),
        )
        self.words_tree.column("word", width=150, stretch=True, anchor="w")
        self.words_tree.heading(
            "trans",
            text="Переклад",
            command=lambda: self.sort_by_column(self.words_tree, "trans", False),
        )
        self.words_tree.column("trans", width=150, stretch=True, anchor="w")
        self.words_tree.heading(
            "lang",
            text="Мова",
            command=lambda: self.sort_by_column(self.words_tree, "lang", False),
        )
        self.words_tree.column("lang", width=90, stretch=False, anchor="center")
        self.words_tree.heading(
            "interval",
            text="Інтервал (SuperMemo)",
            command=lambda: self.sort_by_column(self.words_tree, "interval", False),
        )
        self.words_tree.column("interval", width=140, stretch=False, anchor="center")

        self.words_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        frame_tools = ttk.Frame(card_words, style="Card.TFrame")
        frame_tools.pack(fill=tk.X, padx=5, pady=10)

        self.create_action_button(
            frame_tools, "🗑 Видалити користувача", self.delete_user
        ).pack(side=tk.LEFT, padx=5)
        self.create_action_button(frame_tools, "📥 Експорт", self.export_csv).pack(
            side=tk.RIGHT, padx=5
        )
        self.create_action_button(frame_tools, "📤 Імпорт", self.import_csv).pack(
            side=tk.RIGHT, padx=5
        )

    def build_messages_tab(self):
        card_broadcast = self.create_card(
            self.tab_messages, "📢 Надіслати повідомлення"
        )
        card_broadcast.pack(fill=tk.X, pady=(0, 15))

        frame_recip = ttk.Frame(card_broadcast, style="Card.TFrame")
        frame_recip.pack(fill=tk.X, padx=10, pady=5)

        lbl_recip = tk.Label(frame_recip, text="Одержувач:", font=FONT_BOLD)
        lbl_recip.pack(side=tk.LEFT)
        self.theme_widgets["labels"].append(lbl_recip)

        self.recipient_var = tk.StringVar()
        self.combo_users = ttk.Combobox(
            frame_recip, textvariable=self.recipient_var, font=FONT_MAIN, width=40
        )
        self.combo_users.pack(side=tk.LEFT, padx=10)
        self.combo_users.set("Всі користувачі")

        self.txt_broadcast = tk.Text(
            card_broadcast,
            height=4,
            font=FONT_MAIN,
            relief="flat",
            highlightthickness=1,
        )
        self.txt_broadcast.pack(fill=tk.X, padx=10, pady=5)
        self.theme_widgets["input"].append(self.txt_broadcast)

        btn_frame = ttk.Frame(card_broadcast, style="Card.TFrame")
        btn_frame.pack(fill=tk.X, padx=10, pady=5)
        self.create_action_button(btn_frame, "Відправити 🚀", self.send_broadcast).pack(
            side=tk.RIGHT
        )

        card_fb = self.create_card(self.tab_messages, "💬 Відгуки та Ідеї")
        card_fb.pack(fill=tk.BOTH, expand=True)

        self.fb_tree = ttk.Treeview(
            card_fb, columns=("id", "user", "date", "msg", "status"), show="headings"
        )
        for col, name in {
            "id": "ID",
            "user": "Користувач",
            "date": "Дата",
            "msg": "Відгук",
            "status": "Статус",
        }.items():
            self.fb_tree.heading(
                col,
                text=name,
                command=lambda c=col: self.sort_by_column(self.fb_tree, c, False),
            )

        self.fb_tree.column("id", width=40, anchor="center")
        self.fb_tree.column("user", width=120)
        self.fb_tree.column("date", width=120, anchor="center")
        self.fb_tree.column("msg", width=400, stretch=True, anchor="w")
        self.fb_tree.column("status", width=90, anchor="center")
        self.fb_tree.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.create_action_button(card_fb, "✍️ Відповісти", self.reply_feedback).pack(
            side=tk.RIGHT, padx=10, pady=5
        )
        self.create_action_button(
            card_fb, "✅ Прочитано", self.mark_feedback_read
        ).pack(side=tk.RIGHT, padx=5, pady=5)

    # ЛОГІКА РОБОТИ З POSTGRESQL
    def send_broadcast(self):
        msg = self.txt_broadcast.get("1.0", tk.END).strip()
        recipient = self.recipient_var.get()
        if not msg:
            return messagebox.showwarning("Увага", "Введіть текст!")
        if not TELEGRAM_BOT_TOKEN:
            return messagebox.showerror("Помилка", "Токен не знайдено!")

        targets = []
        if recipient == "Всі користувачі":
            users = db.fetchall("SELECT user_id FROM users")
            targets = [r[0] for r in users]
        else:
            try:
                uid = int(recipient.split(" | ")[0])
                targets = [uid]
            except:
                return messagebox.showerror("Помилка", "Невірно обраний користувач.")

        success = 0
        for uid in targets:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            res = requests.post(
                url, json={"chat_id": uid, "text": msg, "parse_mode": "HTML"}
            )
            if res.status_code == 200:
                success += 1

        messagebox.showinfo("Готово", f"Надіслано {success} повідомлень!")
        self.txt_broadcast.delete("1.0", tk.END)

    def reply_feedback(self):
        sel = self.fb_tree.selection()
        if not sel:
            return messagebox.showwarning("Увага", "Оберіть відгук!")
        item = self.fb_tree.item(sel[0])
        fb_id, user_str = item["values"][0], item["values"][1]

        rw = tk.Toplevel(self)
        rw.title(f"Відповідь: {user_str}")
        rw.geometry("450x250")
        rw.configure(bg=self.colors["bg"])

        tk.Label(
            rw,
            text="Напишіть вашу відповідь:",
            font=FONT_BOLD,
            bg=self.colors["bg"],
            fg=self.colors["text"],
        ).pack(pady=10)
        txt = tk.Text(rw, height=6, font=FONT_MAIN)
        txt.pack(padx=15, fill=tk.BOTH, expand=True)

        def send_reply():
            msg = txt.get("1.0", tk.END).strip()
            if not msg:
                return
            res = db.fetchone("SELECT user_id FROM feedback WHERE id=%s", (fb_id,))
            if res:
                uid = res[0]
                url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
                r = requests.post(
                    url,
                    json={
                        "chat_id": uid,
                        "text": f"💬 <b>Відповідь Адміністратора:</b>\n\n{msg}",
                        "parse_mode": "HTML",
                    },
                )
                if r.status_code == 200:
                    db.execute(
                        "UPDATE feedback SET status='Відповіли' WHERE id=%s", (fb_id,)
                    )
                    self.update_feedback_table()
                    messagebox.showinfo("Успіх", "Відповідь надіслана!")
                    rw.destroy()
                else:
                    messagebox.showerror("Помилка", "Бот не зміг надіслати.")

        tk.Button(
            rw,
            text="Надіслати",
            bg=self.colors["accent"],
            fg="#FFF",
            font=FONT_BOLD,
            relief="flat",
            command=send_reply,
        ).pack(pady=10)

    def update_feedback_table(self):
        for row in self.fb_tree.get_children():
            self.fb_tree.delete(row)
        try:
            feedbacks = db.fetchall(
                "SELECT id, username, date, message, status FROM feedback ORDER BY id DESC"
            )
            c = self.colors
            self.fb_tree.tag_configure("oddrow", background=c["row_odd"])
            self.fb_tree.tag_configure("evenrow", background=c["row_even"])
            self.fb_tree.tag_configure(
                "new", background=c["accent"], foreground="#FFFFFF"
            )
            self.fb_tree.tag_configure(
                "replied", background=c["success"], foreground="#FFFFFF"
            )

            for index, fb in enumerate(feedbacks):
                date_str = str(fb[2])[:16].replace("T", " ") if fb[2] else ""
                tags = (
                    ("new",)
                    if fb[4] == "Новий"
                    else ("replied",)
                    if fb[4] == "Відповіли"
                    else ("evenrow" if index % 2 == 0 else "oddrow",)
                )
                self.fb_tree.insert(
                    "",
                    tk.END,
                    values=(
                        fb[0],
                        f"@{fb[1]}" if fb[1] else "Unknown",
                        date_str,
                        fb[3],
                        fb[4],
                    ),
                    tags=tags,
                )
        except:
            pass

    def mark_feedback_read(self):
        sel = self.fb_tree.selection()
        if not sel:
            return
        fb_id = self.fb_tree.item(sel[0])["values"][0]
        try:
            db.execute("UPDATE feedback SET status='Прочитано' WHERE id=%s", (fb_id,))
            self.update_feedback_table()
        except:
            pass

    def update_users_table(self):
        sel = self.users_tree.selection()
        sel_id = self.users_tree.item(sel[0])["values"][0] if sel else None
        for row in self.users_tree.get_children():
            self.users_tree.delete(row)

        try:
            users = db.fetchall(
                "SELECT user_id, username, start_date, last_active, best_score, learning_style, hobbies FROM users"
            )
            self.combo_users["values"] = ["Всі користувачі"] + [
                f"{u[0]} | @{u[1]}" for u in users
            ]

            now = datetime.now()
            rows = []
            for u in users:
                active = False
                if u[3]:
                    try:
                        if now - datetime.fromisoformat(str(u[3])) < timedelta(
                            minutes=ACTIVE_THRESHOLD_MINUTES
                        ):
                            active = True
                    except:
                        pass
                rows.append((active, u))
            rows.sort(key=lambda x: (not x[0], x[1][0]))

            c = self.colors
            self.users_tree.tag_configure("active", foreground=c["success"])
            self.users_tree.tag_configure("oddrow", background=c["row_odd"])
            self.users_tree.tag_configure("evenrow", background=c["row_even"])

            for index, (active, u) in enumerate(rows):
                tags = (
                    ("active",)
                    if active
                    else ("evenrow" if index % 2 == 0 else "oddrow",)
                )
                item = self.users_tree.insert(
                    "",
                    tk.END,
                    values=(
                        u[0],
                        f"@{u[1]}" if u[1] else "Без імені",
                        u[5] or "Немає",
                        u[6] or "Немає",
                        u[4] or 0,
                        "Онлайн 🟢" if active else "Офлайн ⚪",
                    ),
                    tags=tags,
                )
                if sel_id and u[0] == sel_id:
                    self.users_tree.selection_set(item)

            if self.selected_user_id:
                self.update_details(self.selected_user_id)
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
        self.lbl_filter.config(
            text=f"📚 Словник (Фільтр: {filter_lang})", fg=self.colors["text"]
        )
        for r in self.words_tree.get_children():
            self.words_tree.delete(r)
        try:
            if filter_lang and filter_lang != "Усі мови":
                words = db.fetchall(
                    "SELECT word, translation, language, interval FROM user_words WHERE user_id=%s AND language=%s",
                    (uid, filter_lang),
                )
            else:
                words = db.fetchall(
                    "SELECT word, translation, language, interval FROM user_words WHERE user_id=%s",
                    (uid,),
                )

            for index, w in enumerate(words):
                tags = ("evenrow" if index % 2 == 0 else "oddrow",)
                self.words_tree.insert("", tk.END, values=w, tags=tags)

            self.words_tree.tag_configure("oddrow", background=self.colors["row_odd"])
            self.words_tree.tag_configure("evenrow", background=self.colors["row_even"])
        except:
            pass

    def update_details(self, uid):
        for r in self.stats_tree.get_children():
            self.stats_tree.delete(r)
        try:
            data = db.fetchone(
                "SELECT username, best_score FROM users WHERE user_id=%s", (uid,)
            )
            if data:
                self.lbl_selected.config(
                    text=f"👤 @{data[0]}   |   🎮 Рекорд: {data[1]}",
                    fg=self.colors["accent"],
                )

            rows = db.fetchall(
                "SELECT language, COUNT(*), SUM(usage_count) FROM user_words WHERE user_id=%s GROUP BY language",
                (uid,),
            )
            total_w = sum(r[1] for r in rows)
            total_xp = sum(r[2] or 0 for r in rows)

            self.stats_tree.insert(
                "", tk.END, values=("Усі мови", total_w, total_xp), tags=("evenrow",)
            )

            for index, r in enumerate(rows):
                tags = ("oddrow" if index % 2 == 0 else "evenrow",)
                self.stats_tree.insert(
                    "", tk.END, values=(r[0] or "N/A", r[1], r[2] or 0), tags=tags
                )

            self.stats_tree.tag_configure("oddrow", background=self.colors["row_odd"])
            self.stats_tree.tag_configure("evenrow", background=self.colors["row_even"])
            self.load_words(uid, "Усі мови")
        except:
            pass

    def on_user_select(self, event):
        sel = self.users_tree.selection()
        if sel:
            self.selected_user_id = self.users_tree.item(sel[0])["values"][0]
            self.update_details(self.selected_user_id)

    def sort_by_column(self, tree, col, reverse):
        l = [(tree.set(k, col), k) for k in tree.get_children("")]
        try:
            l.sort(key=lambda t: float(t[0]), reverse=reverse)
        except:
            l.sort(key=lambda t: t[0].lower(), reverse=reverse)
        for index, (val, k) in enumerate(l):
            tree.move(k, "", index)
        tree.heading(col, command=lambda: self.sort_by_column(tree, col, not reverse))

    def export_csv(self):
        if not self.selected_user_id:
            return messagebox.showwarning("Увага", "Оберіть користувача!")
        filepath = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")]
        )
        if not filepath:
            return
        try:
            words = db.fetchall(
                "SELECT word, translation, language FROM user_words WHERE user_id=%s",
                (self.selected_user_id,),
            )
            with open(filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["Word", "Translation", "Language"])
                writer.writerows(words)
            messagebox.showinfo("Успіх", f"Експортовано {len(words)} слів!")
        except Exception as e:
            messagebox.showerror("Помилка", str(e))

    def import_csv(self):
        if not self.selected_user_id:
            return messagebox.showwarning("Увага", "Оберіть користувача!")
        filepath = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv")])
        if not filepath:
            return
        try:
            count = 0
            with open(filepath, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                next(reader)
                for row in reader:
                    if len(row) >= 3:
                        # У Postgres використовуємо ON CONFLICT замість OR IGNORE
                        db.execute(
                            """
                            INSERT INTO user_words (user_id, word, translation, language) 
                            VALUES (%s, %s, %s, %s) 
                            ON CONFLICT DO NOTHING
                        """,
                            (self.selected_user_id, row[0], row[1], row[2]),
                        )
                        count += 1
            messagebox.showinfo("Успіх", f"Імпортовано {count} слів!")
            self.update_details(self.selected_user_id)
        except Exception as e:
            messagebox.showerror("Помилка", str(e))

    # СТВОРЕННЯ ЛОКАЛЬНОГО БЕКАПУ 
    def create_local_backup(self):
        response = messagebox.askyesno(
            "Бекап Бази Даних",
            "Це вивантажить усі дані з PostgreSQL (хмара або сервер) у локальний файл backup_words.db.\nПочати?",
        )
        if not response:
            return

        try:

            local_db = sqlite3.connect("backup_words.db")
            cur = local_db.cursor()

            cur.execute(
                "CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, username TEXT, start_date TEXT, last_active TEXT, best_score INTEGER DEFAULT 0, level TEXT, hobbies TEXT, learning_style TEXT, streak_days INTEGER DEFAULT 0)"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS user_words (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, word TEXT, translation TEXT, language TEXT, usage_count INTEGER, image_url TEXT, association TEXT, transcription TEXT, next_review_date TEXT, interval INTEGER, ease_factor REAL)"
            )
            cur.execute(
                "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT, message TEXT, date TEXT, status TEXT)"
            )

            cur.execute("DELETE FROM users")
            cur.execute("DELETE FROM user_words")
            cur.execute("DELETE FROM feedback")

            users = db.fetchall(
                "SELECT user_id, username, start_date, last_active, best_score, level, hobbies, learning_style, streak_days FROM users"
            )
            cur.executemany(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", users
            )

            words = db.fetchall(
                "SELECT id, user_id, word, translation, language, usage_count, image_url, association, transcription, next_review_date, interval, ease_factor FROM user_words"
            )
            cur.executemany(
                "INSERT INTO user_words VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                words,
            )

            # Перетягуємо feedback
            feedbacks = db.fetchall(
                "SELECT id, user_id, username, message, date, status FROM feedback"
            )
            cur.executemany("INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?)", feedbacks)

            local_db.commit()
            local_db.close()
            messagebox.showinfo(
                "Успіх!", "Бекап успішно збережено у файл backup_words.db!"
            )

        except Exception as e:
            messagebox.showerror("Помилка бекапу", f"Не вдалося зробити бекап:\n{e}")

    def delete_user(self):
        if not self.selected_user_id:
            return messagebox.showwarning(
                "Увага", "Оберіть користувача зліва для видалення!"
            )

        data = db.fetchone(
            "SELECT username FROM users WHERE user_id=%s", (self.selected_user_id,)
        )
        username = f"@{data[0]}" if data and data[0] else "Без імені"

        confirm = messagebox.askyesno(
            "⚠️ Підтвердження видалення",
            f"Ви впевнені, що хочете ПОВНІСТЮ видалити користувача {username} (ID: {self.selected_user_id})?\n\n"
            "Ця дія безповоротна! Всі слова, відгуки та статистика цього користувача будуть видалені з бази даних.",
        )

        if confirm:
            try:
                db.execute(
                    "DELETE FROM user_words WHERE user_id=%s", (self.selected_user_id,)
                )
                db.execute(
                    "DELETE FROM feedback WHERE user_id=%s", (self.selected_user_id,)
                )
                db.execute(
                    "DELETE FROM users WHERE user_id=%s", (self.selected_user_id,)
                )
                messagebox.showinfo(
                    "Готово", f"Користувача {username} успішно видалено."
                )


                self.selected_user_id = None
                self.lbl_selected.config(
                    text="Оберіть користувача зліва 👈", fg=self.colors["text"]
                )
                self.lbl_filter.config(text="📚 Словник (Фільтр: Усі мови)")
                for r in self.words_tree.get_children():
                    self.words_tree.delete(r)
                for r in self.stats_tree.get_children():
                    self.stats_tree.delete(r)

                self.update_users_table()

            except Exception as e:
                messagebox.showerror("Помилка", f"Не вдалося видалити: {e}")


if __name__ == "__main__":
    fix_db_safe()
    app = AdminApp()
    app.mainloop()

