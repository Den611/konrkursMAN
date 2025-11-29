import sqlite3
import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

DB_PATH = "words.db"
REFRESH_INTERVAL = 5000  # Інтервал оновлення даних (у мілісекундах)
ACTIVE_THRESHOLD_MINUTES = 5  # Час, протягом якого користувач вважається активним


def fix_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute("ALTER TABLE user_words ADD COLUMN language TEXT")
    except sqlite3.OperationalError:
        pass
    conn.commit()
    conn.close()


class AdminApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Адмін-панель: Перегляд бази даних")
        self.geometry("1000x600")

        tk.Label(self, text="Список користувачів").pack()

        self.users_tree = ttk.Treeview(self, columns=("user_id", "username", "start_date", "last_active"),
                                       show="headings")

        # Налаштування заголовків та сортування при кліку
        for col in ("user_id", "username", "start_date", "last_active"):
            self.users_tree.heading(col, text=col, command=lambda c=col: self.sort_by_column(c, False))
            self.users_tree.column(col, width=200)

        self.users_tree.pack(fill=tk.X)
        self.users_tree.bind("<<TreeviewSelect>>", self.on_user_select)

        self.selected_label = tk.Label(self, text="Вибраний користувач: Немає", font=("Arial", 12, "bold"))
        self.selected_label.pack(pady=5)

        tk.Label(self, text="Словник користувача").pack()

        self.words_tree = ttk.Treeview(self, columns=("word", "translation", "language"), show="headings")
        for col in ("word", "translation", "language"):
            self.words_tree.heading(col, text=col)
            self.words_tree.column(col, width=200)

        self.words_tree.pack(fill=tk.BOTH, expand=True)

        # Зберігання стану сортування та вибору
        self.selected_user_id = None
        self.sort_column = None
        self.sort_reverse = False

        self.update_users_table()

    def update_users_table(self):
        for row in self.users_tree.get_children():
            self.users_tree.delete(row)

        # Отримання даних з бази
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT user_id, username, start_date, last_active FROM users")
        users = cursor.fetchall()
        conn.close()

        now = datetime.now()
        user_rows = []

        # Визначення активності користувачів
        for u in users:
            user_id, username, start_date, last_active = u
            active = False
            if last_active:
                try:
                    last_active_dt = datetime.fromisoformat(last_active)
                    if now - last_active_dt < timedelta(minutes=ACTIVE_THRESHOLD_MINUTES):
                        active = True
                except ValueError:
                    pass
            user_rows.append((active, u))

        # Сортування: спочатку активні, потім за логікою користувача
        user_rows.sort(key=lambda x: (not x[0], x[1][0]))

        # Заповнення таблиці
        for active, u in user_rows:
            tag = "active" if active else ""
            self.users_tree.insert("", tk.END, values=u, tags=(tag,))

        self.users_tree.tag_configure("active", background="lightgreen")

        # Відновлення сортування, якщо воно було застосоване
        if self.sort_column:
            self.sort_by_column(self.sort_column, self.sort_reverse)

        # Оновлення списку слів, якщо користувач вибраний
        if self.selected_user_id:
            self.update_words_table(self.selected_user_id)

        # Планування наступного оновлення
        self.after(REFRESH_INTERVAL, self.update_users_table)

    def update_words_table(self, user_id):
        for row in self.words_tree.get_children():
            self.words_tree.delete(row)

        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Отримання слів
        cursor.execute("SELECT word, translation, language FROM user_words WHERE user_id=?", (user_id,))
        words = cursor.fetchall()

        # Отримання імені користувача для заголовка
        cursor.execute("SELECT username FROM users WHERE user_id=?", (user_id,))
        user_row = cursor.fetchone()
        conn.close()

        # Заповнення таблиці слів
        for w in words:
            self.words_tree.insert("", tk.END, values=w)

        username = user_row[0] if user_row else "Невідомий"
        self.selected_label.config(text=f"Вибраний користувач: {username}")

    def on_user_select(self, event):
        selected = self.users_tree.selection()
        if selected:
            user_id = self.users_tree.item(selected[0])["values"][0]
            self.selected_user_id = user_id
            self.update_words_table(user_id)

    def sort_by_column(self, col, reverse):
        data = [(self.users_tree.set(k, col), k) for k in self.users_tree.get_children('')]
        
        if col == "user_id":
            data = [(int(v), k) for v, k in data]
        elif col in ("start_date", "last_active"):
            def parse_dt(v):
                try:
                    return datetime.fromisoformat(v)
                except:
                    return datetime.min

            data = [(parse_dt(v), k) for v, k in data]

        data.sort(reverse=reverse)

        for index, (val, k) in enumerate(data):
            self.users_tree.move(k, '', index)

        self.sort_column = col
        self.sort_reverse = reverse

        # зміна напрямку сортування
        self.users_tree.heading(col, command=lambda: self.sort_by_column(col, not reverse))


if __name__ == "__main__":
    fix_db()
    app = AdminApp()
    app.mainloop()
