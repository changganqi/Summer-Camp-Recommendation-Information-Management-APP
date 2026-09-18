"""推免日期/时间分段输入控件：固定分隔符，支持键入与步进选择。"""
import calendar
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

from recommendation_service import parse_time, offer_fields, offer_time_labels


class ModernDateTimeInput(ttk.Frame):
    """现代一体化推免时间胶囊控件：支持自然键入智能解析、微历整点快捷芯片浮层、以及严谨对齐的状态药丸"""

    COMMON_DATES = [
        ("09-18", "9月18日 (系统注册)"),
        ("09-21", "9月21日 (开网填报)"),
        ("09-28", "9月28日 (待录取开放)"),
        ("09-29", "9月29日 (首轮确认)"),
    ]

    COMMON_TIMES = [
        ("00:00", "00:00 (开网零点)"),
        ("09:00", "09:00 (上午开网)"),
        ("12:00", "12:00 (中午截止)"),
        ("14:00", "14:00 (下午确认)"),
        ("18:00", "18:00 (傍晚节点)"),
    ]

    def __init__(self, parent, variable=None, *, optional=False, default=None, year_var=None):
        super().__init__(parent)
        self.variable = variable if variable is not None else tk.StringVar(self)
        self.optional = optional
        self.default = default or datetime.now().replace(second=0, microsecond=0)
        self.year_var = year_var or tk.StringVar(self, value=str(self.default.year))
        self.popup = None

        self.enabled = tk.BooleanVar(self, value=bool(self.variable.get()) or not optional)
        self.display_var = tk.StringVar(self)
        self._updating = False

        self._build_ui()
        self._from_variable()
        self._trace = self.variable.trace_add("write", self._from_variable)
        self._year_trace = None
        if self.year_var:
            try:
                self._year_trace = self.year_var.trace_add("write", self._on_year_var_changed)
            except Exception:
                pass

    def _build_ui(self):
        # 1. 外层胶囊容器
        self.capsule = tk.Frame(self, bg="#ffffff", bd=1, relief="solid", highlightthickness=0)
        self.capsule.configure(highlightbackground="#cbd5e1")
        self.capsule.pack(side="left", padx=(0, 10))

        # 2. 单一体化格式化输入框
        self.entry = tk.Entry(
            self.capsule,
            textvariable=self.display_var,
            font=("Consolas", 10),
            bg="#ffffff",
            fg="#0f172a",
            bd=0,
            width=18,
            justify="center",
            relief="flat",
        )
        self.entry.pack(side="left", padx=(10, 4), pady=5)
        self.entry.bind("<FocusOut>", self._on_entry_commit)
        self.entry.bind("<Return>", self._on_entry_commit)

        # 3. 日历/整点快捷选择图标按钮
        self.drop_btn = tk.Label(
            self.capsule,
            text="📅 ▾",
            font=("Segoe UI Emoji", 9),
            bg="#ffffff",
            fg="#64748b",
            cursor="hand2",
        )
        self.drop_btn.pack(side="left", padx=(0, 8), pady=5)
        self.drop_btn.bind("<Button-1>", self._toggle_popup)
        self.drop_btn.bind("<Enter>", lambda e: self.drop_btn.configure(fg="#2563eb") if self.enabled.get() else None)
        self.drop_btn.bind("<Leave>", lambda e: self.drop_btn.configure(fg="#64748b") if self.enabled.get() else None)

        # 4. 可选状态药丸切换
        if self.optional:
            self.pill_btn = tk.Canvas(self, width=100, height=28, bg="#f8fafc", highlightthickness=0, cursor="hand2")
            self.pill_btn.pack(side="left")
            self.pill_btn.bind("<Button-1>", self._toggle_optional)
            self._update_pill_ui()

    def _update_pill_ui(self):
        if not self.optional:
            return
        self.pill_btn.delete("all")
        is_on = self.enabled.get()
        w, h, r = 100, 28, 6
        if is_on:
            self.pill_btn.create_polygon(r,0, w-r,0, w,r, w,h-r, w-r,h, r,h, 0,h-r, 0,r, fill="#eff6ff", outline="#93c5fd", width=1, smooth=True)
            self.pill_btn.create_text(50, 14, text="✓ 官方已公布", fill="#1d4ed8", font=("Microsoft YaHei UI", 8, "bold"))
            self.entry.configure(state="normal", bg="#ffffff", fg="#0f172a")
            self.capsule.configure(bg="#ffffff", relief="solid")
            self.drop_btn.configure(bg="#ffffff", state="normal")
        else:
            self.pill_btn.create_polygon(r,0, w-r,0, w,r, w,h-r, w-r,h, r,h, 0,h-r, 0,r, fill="#f1f5f9", outline="#cbd5e1", width=1, smooth=True)
            self.pill_btn.create_text(50, 14, text="○ 暂未公布", fill="#64748b", font=("Microsoft YaHei UI", 8))
            self.entry.configure(state="disabled", disabledbackground="#f8fafc", disabledforeground="#94a3b8")
            self.capsule.configure(bg="#f8fafc")
            self.drop_btn.configure(bg="#f8fafc", state="disabled")

    def _toggle_optional(self, event=None):
        new_state = not self.enabled.get()
        self.enabled.set(new_state)
        if new_state:
            if not self.variable.get():
                val = f"{self.year_var.get().strip() or '2026'}-09-28 09:00"
                self.variable.set(val)
        else:
            self.variable.set("")
        self._update_pill_ui()

    def _from_variable(self, *args):
        if self._updating:
            return
        self._updating = True
        try:
            val = (self.variable.get() or "").strip()
            if not val:
                if not self.optional:
                    default_time = f"{self.year_var.get().strip() or '2026'}-09-21 00:00"
                    self.display_var.set(default_time)
                else:
                    self.display_var.set("")
                    self.enabled.set(False)
            else:
                self.enabled.set(True)
                self.display_var.set(val)
            self._update_pill_ui()
        finally:
            self._updating = False

    def _on_entry_commit(self, event=None):
        if self._updating:
            return
        self._updating = True
        try:
            text = self.display_var.get().strip()
            if not text:
                if self.optional:
                    self.enabled.set(False)
                    self.variable.set("")
                    self._update_pill_ui()
                return

            year = self.year_var.get().strip() or "2026"
            # 智能格式化清洗
            clean = text.replace("月", "-").replace("日", " ").replace("点", ":").replace("分", "").replace("/", "-")
            tokens = clean.split()
            date_token = ""
            time_token = ""
            for t in tokens:
                if ":" in t:
                    time_token = t
                elif "-" in t or len(t) == 4 or len(t) == 8:
                    date_token = t

            # 解析日期
            d_parts = date_token.split("-") if "-" in date_token else []
            if len(d_parts) == 3:
                year, m, d = d_parts[0].zfill(4), d_parts[1].zfill(2), d_parts[2].zfill(2)
            elif len(d_parts) == 2:
                m, d = d_parts[0].zfill(2), d_parts[1].zfill(2)
            else:
                m, d = "09", "21"

            # 解析时间
            t_parts = time_token.split(":") if ":" in time_token else []
            if len(t_parts) >= 2:
                hh, mm = t_parts[0].zfill(2), t_parts[1].zfill(2)
            else:
                hh, mm = "00", "00"

            formatted = f"{year}-{m}-{d} {hh}:{mm}"
            self.display_var.set(formatted)
            self.variable.set(formatted)
            if self.optional:
                self.enabled.set(True)
                self._update_pill_ui()
        finally:
            self._updating = False

    def _on_year_var_changed(self, *_args):
        if self._updating:
            return
        try:
            y_str = str(self.year_var.get()).strip()
            if len(y_str) == 4 and y_str.isdigit():
                cur_val = (self.variable.get() or "").strip()
                if cur_val:
                    import re
                    m = re.match(r"^\d{4}(.*)$", cur_val)
                    if m:
                        self.variable.set(f"{y_str}{m.group(1)}")
        except Exception:
            pass

    def _toggle_popup(self, event=None):
        if self.optional and not self.enabled.get():
            return
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
            self.popup = None
            return

        # 解析当前已有时间作为日历初始定位
        val = self.display_var.get().strip()
        now = datetime.now()

        # 始终优先以推免年份 (year_var) 作为日历基准年份
        app_year = now.year
        if self.year_var:
            try:
                y_str = str(self.year_var.get()).strip()
                if y_str.isdigit() and len(y_str) == 4:
                    app_year = int(y_str)
            except Exception:
                pass

        cur_year = app_year
        cur_month = 9
        cur_day = 18
        cur_hour = 9
        cur_min = 0
        try:
            if val:
                parsed = parse_time(val if len(val) >= 16 else f"{cur_year}-{val}")
                cur_month, cur_day = parsed.month, parsed.day
                cur_hour, cur_min = parsed.hour, parsed.minute
                # 如果没有明确的推免年份配置，才使用已解析文本中的年份
                if not (self.year_var and str(self.year_var.get()).strip().isdigit()):
                    cur_year = parsed.year
        except Exception:
            pass

        self.popup = tk.Toplevel(self)
        self.popup.overrideredirect(True)
        self.popup.configure(bg="#ffffff", bd=1, relief="solid", highlightthickness=0)
        self.popup.configure(highlightbackground="#94a3b8")

        x = self.capsule.winfo_rootx()
        y = self.capsule.winfo_rooty() + self.capsule.winfo_height() + 4
        try:
            screen_h = self.winfo_screenheight()
            popup_h = 280
            if y + popup_h > screen_h - 40:
                y = max(10, self.capsule.winfo_rooty() - popup_h - 4)
        except Exception:
            pass
        self.popup.geometry(f"+{x}+{y}")

        self._cal_year = cur_year
        self._cal_month = cur_month
        from datetime import date
        # 月份切换或推免年份联动时，原日期可能在新月份不存在（例如 2 月 29 日
        # 跟随到非闰年）。日历必须先将日期钳制到该月最后一天，避免弹窗直接崩溃。
        cur_day = min(cur_day, calendar.monthrange(cur_year, cur_month)[1])
        self._cal_selected_date = date(cur_year, cur_month, cur_day)
        self._cal_hour = cur_hour
        self._cal_minute = cur_min

        container = tk.Frame(self.popup, bg="#ffffff", padx=12, pady=10)
        container.pack()

        # 左右分栏：左边日历，右边时分滚轮
        body = tk.Frame(container, bg="#ffffff")
        body.pack(fill="both", expand=True)

        # ---------------- 左侧日历 ----------------
        left_cal = tk.Frame(body, bg="#ffffff", padx=4, pady=2)
        left_cal.pack(side="left", fill="both")

        # 顶部年月导航
        nav = tk.Frame(left_cal, bg="#ffffff")
        nav.pack(fill="x", pady=(0, 6))

        tk.Button(
            nav, text="◀", font=("Microsoft YaHei UI", 8),
            bg="#f1f5f9", fg="#475569", bd=0, padx=6, pady=1, cursor="hand2",
            command=self._cal_prev_month
        ).pack(side="left")

        self.nav_label = tk.Label(nav, text="", font=("Microsoft YaHei UI", 10, "bold"), bg="#ffffff", fg="#0f172a", width=14)
        self.nav_label.pack(side="left", padx=4)

        tk.Button(
            nav, text="▶", font=("Microsoft YaHei UI", 8),
            bg="#f1f5f9", fg="#475569", bd=0, padx=6, pady=1, cursor="hand2",
            command=self._cal_next_month
        ).pack(side="left")

        # 星期表头
        w_grid = tk.Frame(left_cal, bg="#ffffff")
        w_grid.pack(fill="x", pady=(0, 4))
        for i, w_text in enumerate(["一", "二", "三", "四", "五", "六", "日"]):
            tk.Label(
                w_grid, text=w_text, font=("Microsoft YaHei UI", 8, "bold"),
                bg="#ffffff", fg="#dc2626" if i >= 5 else "#64748b", width=4, anchor="center"
            ).grid(row=0, column=i, padx=1)

        # 日期按钮容器
        self.days_grid = tk.Frame(left_cal, bg="#ffffff")
        self.days_grid.pack(fill="both", expand=True)

        # 分割线
        sep = tk.Frame(body, width=1, bg="#e2e8f0")
        sep.pack(side="left", fill="y", padx=8)

        # ---------------- 右侧滚轮时分 ----------------
        right_time = tk.Frame(body, bg="#ffffff", padx=4, pady=2)
        right_time.pack(side="left", fill="y")

        tk.Label(right_time, text="⏰ 时:分选择", font=("Microsoft YaHei UI", 9, "bold"), bg="#ffffff", fg="#334155").pack(pady=(0, 6))

        wheels = tk.Frame(right_time, bg="#ffffff")
        wheels.pack(fill="both", expand=True)

        # 时
        h_frame = tk.Frame(wheels, bg="#ffffff")
        h_frame.pack(side="left", padx=(0, 4))
        tk.Label(h_frame, text="时", font=("Microsoft YaHei UI", 8), bg="#ffffff", fg="#64748b").pack()
        self.hour_list = tk.Listbox(
            h_frame, width=4, height=7, font=("Consolas", 10),
            selectmode="single", exportselection=False,
            bd=1, relief="solid", highlightthickness=0, activestyle="none"
        )
        self.hour_list.pack()
        for h in range(24):
            self.hour_list.insert("end", f"{h:02d}")
        self.hour_list.bind("<<ListboxSelect>>", self._on_cal_hour_select)
        self.hour_list.bind("<MouseWheel>", lambda e: self._on_listbox_wheel(self.hour_list, True, e))
        self.hour_list.bind("<Button-4>", lambda e: self._on_listbox_wheel(self.hour_list, True, type("E", (), {"delta": 120})()))
        self.hour_list.bind("<Button-5>", lambda e: self._on_listbox_wheel(self.hour_list, True, type("E", (), {"delta": -120})()))

        # 分
        m_frame = tk.Frame(wheels, bg="#ffffff")
        m_frame.pack(side="left")
        tk.Label(m_frame, text="分", font=("Microsoft YaHei UI", 8), bg="#ffffff", fg="#64748b").pack()
        self.min_list = tk.Listbox(
            m_frame, width=4, height=7, font=("Consolas", 10),
            selectmode="single", exportselection=False,
            bd=1, relief="solid", highlightthickness=0, activestyle="none"
        )
        self.min_list.pack()
        for m in range(60):
            self.min_list.insert("end", f"{m:02d}")
        self.min_list.bind("<<ListboxSelect>>", self._on_cal_min_select)
        self.min_list.bind("<MouseWheel>", lambda e: self._on_listbox_wheel(self.min_list, False, e))
        self.min_list.bind("<Button-4>", lambda e: self._on_listbox_wheel(self.min_list, False, type("E", (), {"delta": 120})()))
        self.min_list.bind("<Button-5>", lambda e: self._on_listbox_wheel(self.min_list, False, type("E", (), {"delta": -120})()))

        # 初始化时分选中
        self.hour_list.selection_set(self._cal_hour)
        self.hour_list.see(max(0, self._cal_hour - 2))
        self.min_list.selection_set(self._cal_minute)
        self.min_list.see(max(0, self._cal_minute - 2))

        # ---------------- 底部栏 ----------------
        bot_bar = tk.Frame(container, bg="#ffffff")
        bot_bar.pack(fill="x", pady=(10, 0))

        self.cal_preview_lbl = tk.Label(
            bot_bar, text="", font=("Consolas", 9, "bold"),
            bg="#f8fafc", fg="#2563eb", padx=6, pady=2, bd=1, relief="solid"
        )
        self.cal_preview_lbl.configure(highlightbackground="#cbd5e1")
        self.cal_preview_lbl.pack(side="left")

        ok_btn = tk.Button(
            bot_bar, text="确定", font=("Microsoft YaHei UI", 9, "bold"),
            bg="#2563eb", fg="#ffffff", activebackground="#1d4ed8", activeforeground="#ffffff",
            bd=0, padx=12, pady=2, cursor="hand2", command=self._cal_confirm
        )
        ok_btn.pack(side="right")

        cancel_btn = tk.Button(
            bot_bar, text="取消", font=("Microsoft YaHei UI", 9),
            bg="#ffffff", fg="#64748b", bd=1, relief="solid", padx=10, pady=2,
            cursor="hand2", command=self._close_popup
        )
        cancel_btn.configure(highlightbackground="#cbd5e1")
        cancel_btn.pack(side="right", padx=(0, 6))

        self._render_cal_grid()
        self._update_cal_preview()

        self.popup.bind("<FocusOut>", lambda e: self.after(150, self._check_popup_focus))
        self.popup.bind("<Escape>", lambda e: self._close_popup())
        self.popup.focus_set()

    def _close_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = None

    def _check_popup_focus(self):
        if not self.popup or not self.popup.winfo_exists():
            return
        try:
            focused = self.popup.focus_get()
        except Exception:
            focused = None
        if not focused:
            self._close_popup()
            return
        w = focused
        while w:
            if w == self.popup:
                return
            w = getattr(w, "master", None)
        self._close_popup()

    def _render_cal_grid(self):
        self.nav_label.configure(text=f"{self._cal_year}年 {self._cal_month:02d}月")
        for w in self.days_grid.winfo_children():
            w.destroy()

        cal = calendar.monthcalendar(self._cal_year, self._cal_month)
        for r_idx, week in enumerate(cal):
            for c_idx, day in enumerate(week):
                if day == 0:
                    continue
                is_selected = (
                    self._cal_selected_date.year == self._cal_year and
                    self._cal_selected_date.month == self._cal_month and
                    self._cal_selected_date.day == day
                )
                bg_c = "#2563eb" if is_selected else "#ffffff"
                fg_c = "#ffffff" if is_selected else ("#dc2626" if c_idx >= 5 else "#0f172a")

                lbl = tk.Label(
                    self.days_grid, text=str(day),
                    font=("Microsoft YaHei UI", 9, "bold" if is_selected else "normal"),
                    bg=bg_c, fg=fg_c, width=4, height=1, cursor="hand2"
                )
                lbl.grid(row=r_idx, column=c_idx, padx=1, pady=1)
                lbl.bind("<Button-1>", lambda e, d=day: self._cal_pick_day(d))
                if not is_selected:
                    lbl.bind("<Enter>", lambda e, w=lbl: w.configure(bg="#f1f5f9"))
                    lbl.bind("<Leave>", lambda e, w=lbl: w.configure(bg="#ffffff"))

    def _cal_pick_day(self, day):
        from datetime import date
        self._cal_selected_date = date(self._cal_year, self._cal_month, day)
        self._render_cal_grid()
        self._update_cal_preview()

    def _cal_prev_month(self):
        if self._cal_month == 1:
            self._cal_month = 12
            self._cal_year -= 1
        else:
            self._cal_month -= 1
        self._clamp_selected_date_to_calendar()
        self._render_cal_grid()

    def _cal_next_month(self):
        if self._cal_month == 12:
            self._cal_month = 1
            self._cal_year += 1
        else:
            self._cal_month += 1
        self._clamp_selected_date_to_calendar()
        self._render_cal_grid()

    def _clamp_selected_date_to_calendar(self):
        """让月份翻页后的预览日期跟随当前日历页，并处理月末/闰年差异。"""
        from datetime import date
        day = min(self._cal_selected_date.day, calendar.monthrange(self._cal_year, self._cal_month)[1])
        self._cal_selected_date = date(self._cal_year, self._cal_month, day)
        self._update_cal_preview()

    def _on_cal_hour_select(self, event=None):
        sel = self.hour_list.curselection()
        if sel:
            self._cal_hour = sel[0]
            self.hour_list.see(max(0, self._cal_hour - 2))
            self._update_cal_preview()

    def _on_cal_min_select(self, event=None):
        sel = self.min_list.curselection()
        if sel:
            self._cal_minute = sel[0]
            self.min_list.see(max(0, self._cal_minute - 2))
            self._update_cal_preview()

    def _on_listbox_wheel(self, listbox, is_hour, event):
        delta = -1 if getattr(event, "delta", 0) > 0 else 1
        if is_hour:
            new_val = max(0, min(23, self._cal_hour + delta))
            self._cal_hour = new_val
            self.hour_list.selection_clear(0, "end")
            self.hour_list.selection_set(new_val)
            self.hour_list.see(max(0, new_val - 2))
        else:
            new_val = max(0, min(59, self._cal_minute + delta))
            self._cal_minute = new_val
            self.min_list.selection_clear(0, "end")
            self.min_list.selection_set(new_val)
            self.min_list.see(max(0, new_val - 2))
        self._update_cal_preview()
        return "break"

    def _update_cal_preview(self):
        formatted = f"{self._cal_selected_date.strftime('%Y-%m-%d')} {self._cal_hour:02d}:{self._cal_minute:02d}"
        if hasattr(self, "cal_preview_lbl"):
            self.cal_preview_lbl.configure(text=formatted)

    def _cal_confirm(self):
        formatted = f"{self._cal_selected_date.strftime('%Y-%m-%d')} {self._cal_hour:02d}:{self._cal_minute:02d}"
        self.display_var.set(formatted)
        self.variable.set(formatted)
        if self.optional:
            self.enabled.set(True)
            self._update_pill_ui()
        self._close_popup()

    def get(self):
        val = self.variable.get()
        if not val and self.optional:
            return ""
        date = parse_time(val) if val else self.default
        return date.isoformat(sep=" ", timespec="seconds" if date.second else "minutes")

    def set(self, value):
        self.variable.set(value)
        self._from_variable()

    def destroy(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None
        try:
            self.variable.trace_remove("write", self._trace)
        except Exception:
            pass
        if getattr(self, "_year_trace", None) and self.year_var:
            try:
                self.year_var.trace_remove("write", self._year_trace)
            except Exception:
                pass
            self._year_trace = None
        super().destroy()


class DateTimeInput(ttk.Frame):
    def __init__(self, parent, variable=None, *, optional=False, default=None):
        super().__init__(parent)
        self.variable = variable if variable is not None else tk.StringVar(self)
        self.optional = optional
        self.default = default or datetime.now().replace(second=0, microsecond=0)
        self._updating = False
        self.enabled = tk.BooleanVar(self, value=bool(self.variable.get()) or not optional)
        self.parts = [tk.StringVar(self) for _ in range(5)]
        self.spins = []
        if optional:
            ttk.Checkbutton(self, text="已确定", variable=self.enabled, command=self._toggle).pack(side="left", padx=(0, 4))
        for index, (minimum, maximum, width, suffix) in enumerate(((1900, 9998, 5, "-"), (1, 12, 3, "-"),
                                                                   (1, 31, 3, "  "), (0, 23, 3, ":"), (0, 59, 3, ""))):
            spin = ttk.Spinbox(self, from_=minimum, to=maximum, width=width, textvariable=self.parts[index],
                               format="%04.0f" if index == 0 else "%02.0f", wrap=index != 0)
            spin.pack(side="left")
            self.spins.append(spin)
            if suffix:
                ttk.Label(self, text=suffix).pack(side="left")
        self._trace = self.variable.trace_add("write", self._from_variable)
        self._from_variable()
        for part in self.parts:
            part.trace_add("write", self._from_parts)
        if not optional and not self.variable.get():
            self._from_parts()

    def _from_variable(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            value = self.variable.get()
            try:
                date = parse_time(value) if value else self.default
            except ValueError:
                return
            self.enabled.set(bool(value) or not self.optional)
            for part, number, width in zip(self.parts, (date.year, date.month, date.day, date.hour, date.minute), (4, 2, 2, 2, 2)):
                part.set(f"{number:0{width}d}")
            self._update_days()
            self._set_enabled()
        finally:
            self._updating = False

    def _update_days(self):
        try:
            last = calendar.monthrange(int(self.parts[0].get()), int(self.parts[1].get()))[1]
            self.spins[2].configure(to=last)
        except (ValueError, calendar.IllegalMonthError):
            pass

    def _set_enabled(self):
        for spin in self.spins:
            spin.configure(state="normal" if self.enabled.get() else "disabled")

    def _from_parts(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            self._update_days()
            parts = [v.get().strip() for v in self.parts]
            parts = [value.zfill(4 if i == 0 else 2) if value.isdigit() else value for i, value in enumerate(parts)]
            self.variable.set(f"{parts[0]}-{parts[1]}-{parts[2]} {parts[3]}:{parts[4]}" if self.enabled.get() else "")
        finally:
            self._updating = False

    def _toggle(self):
        self._set_enabled()
        self._from_parts()

    def get(self):
        value = self.variable.get()
        if not value and self.optional:
            return ""
        date = parse_time(value)
        return date.isoformat(sep=" ", timespec="seconds" if date.second else "minutes")

    def set(self, value):
        self.variable.set(value)

    def destroy(self):
        self.variable.trace_remove("write", self._trace)
        super().destroy()


class OfferTimingInput(ttk.Frame):
    MODES = {"system": "系统开放时起算（成果类）", "fixed": "指定开始时间", "pending": "待通知（候补等）"}

    def __init__(self, parent, settings, clock=None):
        super().__init__(parent)
        self.settings = settings
        self.clock = clock or datetime.now
        self.mode_var = tk.StringVar(self, value=self.MODES["pending"])
        self.start_var = tk.StringVar(self)
        self.duration_var = tk.StringVar(self)
        self.unit_var = tk.StringVar(self, value="分钟")
        ttk.Label(self, text="录取确认开始时间:").pack(anchor="w")
        self.mode_combo = ttk.Combobox(self, textvariable=self.mode_var, values=list(self.MODES.values()), state="readonly")
        self.mode_combo.pack(fill="x", pady=(2, 4))
        self.date_input = DateTimeInput(self, self.start_var, default=self.clock())
        self.date_input.pack(anchor="w")
        self.start_hint = ttk.Label(self, foreground="#64748b", wraplength=380)
        self.start_hint.pack(anchor="w")
        duration_row = ttk.Frame(self)
        duration_row.pack(fill="x", pady=(6, 4))
        ttk.Label(duration_row, text="允许确认时长:").pack(side="left")
        ttk.Spinbox(duration_row, from_=1, to=525600, textvariable=self.duration_var, width=7).pack(side="left", padx=4)
        ttk.Combobox(duration_row, textvariable=self.unit_var, values=("分钟", "小时"), width=5, state="readonly").pack(side="left")
        self.deadline_label = ttk.Label(self, foreground="#b45309", wraplength=380)
        self.deadline_label.pack(anchor="w", pady=(0, 4))
        for variable in (self.mode_var, self.start_var, self.duration_var, self.unit_var):
            variable.trace_add("write", self._update)
        self._update()

    def get(self):
        mode = next((key for key, label in self.MODES.items() if label == self.mode_var.get()), None)
        raw = self.duration_var.get().strip()
        if raw and (not raw.isascii() or not raw.isdigit()):
            raise ValueError("允许确认时长请输入正整数，可选择分钟或小时。")
        minutes = int(raw) * (60 if self.unit_var.get() == "小时" else 1) if raw else None
        return offer_fields({"offer_start_mode": mode, "offer_start_time": self.date_input.get() if mode == "fixed" else "",
                             "offer_duration_minutes": minutes})

    def set(self, item):
        fields = offer_fields(item)
        self.start_var.set(fields["offer_start_time"] or self.clock().strftime("%Y-%m-%d %H:%M"))
        minutes = fields["offer_duration_minutes"]
        self.unit_var.set("小时" if minutes and minutes % 60 == 0 else "分钟")
        self.duration_var.set(str(minutes // 60 if self.unit_var.get() == "小时" else minutes) if minutes else "")
        self.mode_var.set(self.MODES[fields["offer_start_mode"]])
        self._update()

    def _update(self, *_args):
        fixed = self.mode_var.get() == self.MODES["fixed"]
        if fixed:
            self.date_input.pack(anchor="w", before=self.start_hint)
        else:
            self.date_input.pack_forget()
        settings = self.settings()
        self.start_hint.configure(text=(f"跟随系统开放：{settings.get('admission_open_time') or '请先在规则中设置'}" if self.mode_var.get() == self.MODES["system"] else
                                        "收到通知后再登记开始时间；可先填写允许时长。" if not fixed else "年-月-日 时:分，可键入或点击箭头选择。"))
        try:
            _, _, deadline = offer_time_labels(settings, self.get())
            self.deadline_label.configure(text=f"确认截止时间：{deadline}")
        except ValueError as exc:
            self.deadline_label.configure(text=str(exc))


class DateTimeDialog(tk.Toplevel):
    def __init__(self, parent, title, prompt, initial, on_save):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.grab_set()
        self.on_save = on_save
        body = ttk.Frame(self, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=prompt).pack(anchor="w", pady=(0, 10))
        self.input = DateTimeInput(body, tk.StringVar(self, value=initial))
        self.input.pack(anchor="w")
        ttk.Button(body, text="保存时间", command=self._save).pack(side="right", pady=(14, 0))

    def _save(self):
        try:
            if self.on_save(self.input.get()) is not False:
                self.destroy()
        except ValueError as exc:
            messagebox.showerror("时间未保存", str(exc), parent=self)
