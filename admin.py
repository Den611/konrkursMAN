import sqlite3
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

DB_PATH = "words.db"
REFRESH_INTERVAL = 5000  # Оновлення кожні 5 сек
ACTIVE_THRESHOLD_MINUTES = 5


def fix_db():
    """Перевірка структури БД"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
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
        self.title("Адмін-панель: Статистика та Словники")
        self.geometry("1100x750")

        # Стилі
        style = ttk.Style()
        style.configure("Treeview.Heading", font=("Arial", 10, "bold"))
        style.configure("Treeview", font=("Arial", 10), rowheight=25)

        # --- 1. КОРИСТУВАЧІ ---
        frame_users = tk.LabelFrame(self, text="Користувачі")
        frame_users.pack(fill=tk.X, padx=10, pady=5)

        self.users_tree = ttk.Treeview(frame_users, columns=("id", "name", "date", "active", "score"), show="headings",
                                       height=6)

        # Налаштування колонок з сортуванням
        cols_users = {"id": "ID", "name": "Юзернейм", "date": "Реєстрація", "active": "Активність",
                      "score": "Рекорд гри"}
        for col, name in cols_users.items():
            self.users_tree.heading(col, text=name,
                                    command=lambda c=col: self.sort_by_column(self.users_tree, c, False))
            self.users_tree.column(col, anchor="center")

        self.users_tree.pack(fill=tk.X, padx=5, pady=5)
        self.users_tree.bind("<<TreeviewSelect>>", self.on_user_select)

        # --- 2. ІНФО ПРО ВИБРАНОГО ---
        self.lbl_selected = tk.Label(self, text="Оберіть користувача зверху 👆", font=("Arial", 12, "bold"), fg="#333")
        self.lbl_selected.pack(pady=5)

        # --- 3. СТАТИСТИКА ПО МОВАХ ---
        frame_stats = tk.LabelFrame(self, text="Прогрес по мовах")
        frame_stats.pack(fill=tk.X, padx=10, pady=5)

        self.stats_tree = ttk.Treeview(frame_stats, columns=("lang", "count", "xp", "lvl"), show="headings", height=4)

        cols_stats = {"lang": "Мова", "count": "Слів вивчено", "xp": "Бали (XP)", "lvl": "Рівень"}
        for col, name in cols_stats.items():
            self.stats_tree.heading(col, text=name,
                                    command=lambda c=col: self.sort_by_column(self.stats_tree, c, False))
            self.stats_tree.column(col, anchor="center")

        self.stats_tree.pack(fill=tk.X, padx=5, pady=5)

        # --- 4. СЛОВНИК ---
        frame_words = tk.LabelFrame(self, text="Словник користувача")
        frame_words.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.words_tree = ttk.Treeview(frame_words, columns=("word", "trans", "lang", "usage"), show="headings")

        cols_words = {"word": "Слово", "trans": "Переклад", "lang": "Мова", "usage": "Успішність"}
        for col, name in cols_words.items():
            self.words_tree.heading(col, text=name,
                                    command=lambda c=col: self.sort_by_column(self.words_tree, c, False))
            self.words_tree.column(col, anchor="center")

        self.words_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # Змінні
        self.selected_user_id = None
        self.update_users_table()

    def update_users_table(self):
        # Зберігаємо виділення
        sel = self.users_tree.selection()
        sel_id = self.users_tree.item(sel[0])['values'][0] if sel else None

        # Очищення
        for row in self.users_tree.get_children(): self.users_tree.delete(row)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, start_date, last_active, best_score FROM users")
        users = cursor.fetchall()
        conn.close()

        now = datetime.now()
        rows = []
        for u in users:
            active = False
            if u[3]:
                try:
                    dt = datetime.fromisoformat(u[3])
                    if now - dt < timedelta(minutes=ACTIVE_THRESHOLD_MINUTES): active = True
                except:
                    pass
            rows.append((active, u))

        # Сортуємо: спочатку активні
        rows.sort(key=lambda x: (not x[0], x[1][0]))

        for active, u in rows:
            tag = "active" if active else ""
            item = self.users_tree.insert("", tk.END, values=u, tags=(tag,))
            if sel_id and u[0] == sel_id: self.users_tree.selection_set(item)

        self.users_tree.tag_configure("active", background="#d1ffc4")  # Зелений для активних

        # Оновлюємо деталі, якщо хтось вибраний
        if self.selected_user_id:
            self.update_details(self.selected_user_id)

        self.after(REFRESH_INTERVAL, self.update_users_table)

    def update_details(self, uid):
        # Запам'ятовуємо сортування, якщо воно було (не реалізовано для простоти, але дані оновляться)
        for t in (self.stats_tree, self.words_tree):
            for r in t.get_children(): t.delete(r)

        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()

        # 1. Оновлення статистики по мовах
        cur.execute("""
            SELECT language, COUNT(*), SUM(usage_count) 
            FROM user_words 
            WHERE user_id=? 
            GROUP BY language
        """, (uid,))

        for lang, count, xp in cur.fetchall():
            xp = xp or 0
            lvl = (xp // 10) + 1
            self.stats_tree.insert("", tk.END, values=(lang, count, xp, f"Lvl {lvl}"))

        # 2. Оновлення слів
        cur.execute("SELECT word, translation, language, usage_count FROM user_words WHERE user_id=?", (uid,))
        for w in cur.fetchall():
            self.words_tree.insert("", tk.END, values=w)

        # 3. Заголовок
        cur.execute("SELECT username, best_score FROM users WHERE user_id=?", (uid,))
        data = cur.fetchone()
        if data:
            self.lbl_selected.config(text=f"👤 {data[0]} (ID: {uid}) | 🎮 Рекорд: {data[1]}")

        conn.close()

    def on_user_select(self, event):
        sel = self.users_tree.selection()
        if sel:
            self.selected_user_id = self.users_tree.item(sel[0])["values"][0]
            self.update_details(self.selected_user_id)

    def sort_by_column(self, tree, col, reverse):
        """Універсальна функція сортування для будь-якої таблиці"""
        l = [(tree.set(k, col), k) for k in tree.get_children('')]

        # Пробуємо сортувати як числа, якщо не вийде - як текст
        try:
            l.sort(key=lambda t: float(t[0]), reverse=reverse)
        except ValueError:
            l.sort(key=lambda t: t[0].lower(), reverse=reverse)

        for index, (val, k) in enumerate(l):
            tree.move(k, '', index)

        # Змінюємо напрямок для наступного кліку
        tree.heading(col, command=lambda: self.sort_by_column(tree, col, not reverse))


if __name__ == "__main__":
    fix_db()
    app = AdminApp()
    app.mainloop()