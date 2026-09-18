# -*- coding: utf-8 -*-
"""
推免管理前端视图模块 (Recommendation Management View) - 宽屏联动增强版
专注 9 月研招网推免服务系统（全国推荐免试攻读研究生信息公开暨管理服务系统）的志愿决战看板前端设计。
本模块支持将主看板转移至左侧宽屏区域（日历与项目列表区域），右侧作为实战控制台与招办联络簿。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

from recommendation_service import (
    DEFAULT_RECOMMENDATION_SETTINGS, parse_time, validate_settings,
    timeline_countdown, effective_admission_deadline,
    minute_duration_text, offer_fields, offer_window, offer_sort_key, offer_time_labels,
)
from camp_status import camp_status_display, camp_import_sort_key
from recommendation_widgets import DateTimeInput, ModernDateTimeInput, OfferTimingInput, DateTimeDialog
from recommendation_alerts import AdmissionAlertModal, AlertSoundPlayer, send_windows_toast



class ModernCheckbutton(tk.Frame):
    """现代抗误会打勾控件：选中使用清晰的蓝底/绿底白色对勾 √，杜绝打叉产生的歧义"""

    def __init__(self, parent, text: str, variable: tk.BooleanVar, bg="#ffffff", fg="#1e293b", command=None):
        super().__init__(parent, bg=bg, cursor="hand2")
        self.variable = variable
        self.command = command
        self.text = text
        self.bg_color = bg
        self.fg_color = fg

        # 左侧绘制勾选方框
        self.box_size = 18
        self.canvas = tk.Canvas(
            self,
            width=self.box_size,
            height=self.box_size,
            bg=bg,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack(side="left", padx=(0, 8))

        # 右侧文字
        self.label = tk.Label(
            self,
            text=text,
            bg=bg,
            fg=fg,
            font=("Microsoft YaHei UI", 9),
            cursor="hand2",
        )
        self.label.pack(side="left", anchor="w")

        # 绑定事件
        self.canvas.bind("<Button-1>", self._toggle)
        self.label.bind("<Button-1>", self._toggle)
        self.bind("<Button-1>", self._toggle)

        self._draw()

    def _toggle(self, event=None):
        self.variable.set(not self.variable.get())
        self._draw()
        if callable(self.command):
            self.command()

    def _draw(self):
        self.canvas.delete("all")
        is_checked = bool(self.variable.get())
        s = self.box_size
        radius = 4

        if is_checked:
            # 选中状态：深蓝底圆角，绘制纯白平滑对勾 √
            fill_color = "#2563eb"
            outline_color = "#1d4ed8"
            self.canvas.create_polygon(
                radius, 0, s - radius, 0, s, radius, s, s - radius,
                s - radius, s, radius, s, 0, s - radius, 0, radius,
                fill=fill_color, outline=outline_color, width=1, smooth=True
            )
            # 绘制优雅的对勾线段 √
            self.canvas.create_line(4, 9, 8, 13, fill="#ffffff", width=2.2, capstyle="round", joinstyle="round")
            self.canvas.create_line(8, 13, 14, 5, fill="#ffffff", width=2.2, capstyle="round", joinstyle="round")
        else:
            # 未选中状态：白色底淡灰圆角边框
            fill_color = "#ffffff"
            outline_color = "#94a3b8"
            self.canvas.create_polygon(
                radius, 0, s - radius, 0, s, radius, s, s - radius,
                s - radius, s, radius, s, 0, s - radius, 0, radius,
                fill=fill_color, outline=outline_color, width=1.2, smooth=True
            )


class RecommendationSettingsDialog(tk.Toplevel):
    """推免服务系统基础规则与时间设置向导弹窗 (纯打勾 & 无扰保存)"""

    def __init__(self, master, current_settings: dict, on_save_callback):
        super().__init__(master)
        self.title("推免服务系统规则与时间配置向导")
        self.geometry("760x710")
        self.minsize(720, 680)
        self.transient(master)
        self.grab_set()
        self.configure(bg="#f8fafc")
        self.on_save = on_save_callback

        self.settings = dict(current_settings)
        self.year_var = tk.StringVar(value=str(self.settings.get("year", "2026")))
        self.reg_time_var = tk.StringVar(value=str(self.settings.get("reg_open_time", "2026-09-18 09:00")))
        self.choice_time_var = tk.StringVar(value=str(self.settings.get("choice_open_time", "2026-09-21 00:00")))
        self.admission_open_var = tk.StringVar(value=self.settings.get("admission_open_time", ""))
        self.admission_close_var = tk.StringVar(value=self.settings.get("admission_close_time", ""))
        self.slot_count_var = tk.IntVar(value=int(self.settings.get("slot_count", 3)))
        self.lock_hours_var = tk.IntVar(value=int(self.settings.get("lock_hours", 48)))
        self.notify_open_var = tk.BooleanVar(value=bool(self.settings.get("notify_before_open", True)))
        self.notify_unlock_var = tk.BooleanVar(value=bool(self.settings.get("notify_before_unlock", True)))
        self.notify_admission_var = tk.BooleanVar(value=bool(self.settings.get("notify_admission", True)))
        self._year_trace = self.year_var.trace_add("write", self._on_year_changed)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._decoration_job = self.after(20, self._apply_decorations)

    def destroy(self):
        if getattr(self, "_year_trace", None):
            try:
                self.year_var.trace_remove("write", self._year_trace)
            except Exception:
                pass
            self._year_trace = None
        if getattr(self, "_decoration_job", None):
            self.after_cancel(self._decoration_job)
            self._decoration_job = None
        super().destroy()

    def _on_year_changed(self, *_args):
        new_year = self.year_var.get().strip()
        if len(new_year) == 4 and new_year.isdigit():
            import re
            for var in (self.reg_time_var, self.choice_time_var, self.admission_open_var, self.admission_close_var):
                val = var.get().strip()
                if val:
                    m = re.match(r"^\d{4}(.*)$", val)
                    if m:
                        var.set(f"{new_year}{m.group(1)}")

    def _apply_decorations(self):
        try:
            from summer_camp_planner import apply_windows_glass, apply_app_icon
            apply_windows_glass(self)
            apply_app_icon(self)
        except Exception:
            pass

    def _build_ui(self):
        container = ttk.Frame(self, padding=16)
        container.pack(fill="both", expand=True)

        # 用 grid 将底部操作栏固定在独立行。原先 form_frame 使用 pack(fill="both",
        # expand=True) 后再追加按钮栏，表单会先吃掉全部剩余高度，导致按钮被挤出窗口。
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)

        # 头部说明卡片
        header_card = tk.Frame(container, bg="#fff7ed", bd=1, relief="solid", highlightthickness=0)
        header_card.configure(highlightbackground="#fed7aa")
        header_card.grid(row=0, column=0, sticky="ew", pady=(0, 14))

        header_inner = tk.Frame(header_card, bg="#fff7ed", padx=14, pady=10)
        header_inner.pack(fill="x")
        tk.Label(
            header_inner,
            text="🎯 研招网推免服务系统 (九月填报) 基础参数设置",
            bg="#fff7ed",
            fg="#c2410c",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w")
        tk.Label(
            header_inner,
            text="根据教育部与各招生单位每年规定，推免系统的开放时间、平行志愿数及锁定时长可能微调。\n请在此配置您当年的具体参数，推免看板将自动驱动倒计时、阶段流转与平行志愿槽位。",
            bg="#fff7ed",
            fg="#9a3412",
            font=("Microsoft YaHei UI", 9),
            justify="left",
        ).pack(anchor="w", pady=(4, 0))

        # 表单区域
        form_frame = ttk.LabelFrame(container, text="系统运行规则与时间线", padding=(14, 12))
        form_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 14))

        for col in (0, 2):
            form_frame.columnconfigure(col, weight=0)
        for col in (1, 3):
            form_frame.columnconfigure(col, weight=1)

        # 第一行：推免年份 & 志愿槽位数
        ttk.Label(form_frame, text="推免年份:").grid(row=0, column=0, sticky="w", pady=6)
        year_entry = ttk.Entry(form_frame, textvariable=self.year_var, width=15)
        year_entry.grid(row=0, column=1, sticky="w", padx=4, pady=6)

        ttk.Label(form_frame, text="平行志愿数:").grid(row=0, column=2, sticky="w", pady=6)
        slot_combo = ttk.Combobox(
            form_frame,
            textvariable=self.slot_count_var,
            values=[1, 2, 3, 4, 5],
            state="readonly",
            width=15,
        )
        slot_combo.grid(row=0, column=3, sticky="w", padx=4, pady=6)

        # 第二行：注册开放时间
        ttk.Label(form_frame, text="系统注册开放时间:").grid(row=1, column=0, sticky="w", pady=6)
        reg_entry = ModernDateTimeInput(form_frame, self.reg_time_var, year_var=self.year_var)
        reg_entry.grid(row=1, column=1, columnspan=3, sticky="w", padx=4, pady=6)

        # 第三行：填志愿开放时间
        ttk.Label(form_frame, text="志愿填报开放时间:").grid(row=2, column=0, sticky="w", pady=6)
        choice_entry = ModernDateTimeInput(form_frame, self.choice_time_var, year_var=self.year_var)
        choice_entry.grid(row=2, column=1, columnspan=3, sticky="w", padx=4, pady=6)

        for row, label, variable in ((3, "待录取确认开放时间:", self.admission_open_var),
                                     (4, "待录取确认结束时间:", self.admission_close_var)):
            ttk.Label(form_frame, text=label).grid(row=row, column=0, sticky="w", pady=6)
            ModernDateTimeInput(form_frame, variable, year_var=self.year_var, optional=True).grid(row=row, column=1, columnspan=3, sticky="w", padx=4, pady=6)
        
        ttk.Label(
            form_frame,
            text="💡 支持直接键入/粘贴时间（失焦智能规整），亦可点击 📅 ▾ 快捷点选；未公布时保持“暂未公布”。",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 8),
        ).grid(row=5, column=0, columnspan=4, sticky="w", pady=(2, 6))

        ttk.Label(form_frame, text="单志愿锁定时长(小时):").grid(row=6, column=0, sticky="w", pady=6)
        lock_spin = ttk.Spinbox(form_frame, from_=12, to=72, increment=1, textvariable=self.lock_hours_var, width=15)
        lock_spin.grid(row=6, column=1, sticky="w", padx=4, pady=6)

        hint_label = ttk.Label(
            form_frame,
            text="锁定时长调整应用于后续新填报志愿；已提交志愿保留原来的解锁时间。",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 8),
        )
        hint_label.grid(row=7, column=0, columnspan=4, sticky="w", pady=(2, 6))

        # 提醒配置：采用 ModernCheckbutton 清晰打勾 √
        notify_frame = ttk.LabelFrame(container, text="决战提醒与防误触警报", padding=(14, 10))
        notify_frame.grid(row=2, column=0, sticky="ew", pady=(0, 16))

        self.cb1 = ModernCheckbutton(
            notify_frame,
            text="关键时间节点前 30 分钟高亮倒计时，志愿填报开放前弹窗提醒",
            variable=self.notify_open_var,
        )
        self.cb1.pack(anchor="w", pady=4)

        self.cb2 = ModernCheckbutton(
            notify_frame,
            text="志愿锁定到期自动解锁前 1 小时提示致电招生办",
            variable=self.notify_unlock_var,
        )
        self.cb2.pack(anchor="w", pady=4)

        self.cb3 = ModernCheckbutton(
            notify_frame,
            text="收到『待录取通知』时强制弹出全屏防误触二次确认警示框",
            variable=self.notify_admission_var,
        )
        self.cb3.pack(anchor="w", pady=4)

        # 底部操作按钮
        btn_bar = ttk.Frame(container)
        btn_bar.grid(row=3, column=0, sticky="ew", pady=(0, 0))

        ttk.Button(btn_bar, text="恢复推荐值", command=self._reset_defaults).pack(side="left")
        ttk.Button(btn_bar, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(btn_bar, text="保存并应用设置", style="Accent.TButton", command=self._save_and_close).pack(side="right", padx=(0, 8))

    def _reset_defaults(self):
        self.year_var.set("2026")
        self.reg_time_var.set("2026-09-18 09:00")
        self.choice_time_var.set("2026-09-21 00:00")
        self.admission_open_var.set("")
        self.admission_close_var.set("")
        self.slot_count_var.set(3)
        self.lock_hours_var.set(48)
        self.notify_open_var.set(True)
        self.notify_unlock_var.set(True)
        self.notify_admission_var.set(True)
        self.cb1._draw()
        self.cb2._draw()
        self.cb3._draw()

    def _save_and_close(self):
        try:
            self._save_validated_settings()
        except (ValueError, tk.TclError) as exc:
            messagebox.showerror("设置未保存", str(exc), parent=self)

    def _save_validated_settings(self):
        new_settings = {
            "year": self.year_var.get().strip() or "2026",
            "reg_open_time": self.reg_time_var.get().strip() or "2026-09-18 09:00",
            "choice_open_time": self.choice_time_var.get().strip() or "2026-09-21 00:00",
            "admission_open_time": self.admission_open_var.get().strip(),
            "admission_close_time": self.admission_close_var.get().strip(),
            "slot_count": self.slot_count_var.get(),
            "lock_hours": self.lock_hours_var.get(),
            "notify_before_open": self.notify_open_var.get(),
            "notify_before_unlock": self.notify_unlock_var.get(),
            "notify_admission": self.notify_admission_var.get(),
            "is_initialized": True,
        }
        new_settings = validate_settings(new_settings)
        if callable(self.on_save) and self.on_save(new_settings) is False:
            return
        # 直接关闭窗口，无需再弹窗二次确认
        self.destroy()


class ImportCampDialog(tk.Toplevel):
    """从夏令营/预推免已记录项目一键导入到推免储备池弹窗"""

    def __init__(self, master, camps: list[dict], on_import_callback):
        super().__init__(master)
        self.title("从已有学校导入备选志愿")
        self.geometry("740x510")
        self.minsize(640, 420)
        self.transient(master)
        self.grab_set()
        self.camps = camps
        self.on_import = on_import_callback

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._decoration_job = self.after(20, self._apply_decorations)

    def destroy(self):
        if getattr(self, "_decoration_job", None):
            self.after_cancel(self._decoration_job)
            self._decoration_job = None
        super().destroy()

    def _apply_decorations(self):
        try:
            from summer_camp_planner import apply_windows_glass, apply_app_icon
            apply_windows_glass(self)
            apply_app_icon(self)
        except Exception:
            pass

    def _build_ui(self):
        body = ttk.Frame(self, padding=14)
        body.pack(fill="both", expand=True)

        top_info = tk.Frame(body, bg="#eff6ff", bd=1, relief="solid", highlightthickness=0)
        top_info.configure(highlightbackground="#bfdbfe")
        top_info.pack(fill="x", pady=(0, 10))
        tk.Label(
            top_info,
            text="📋 选择已有学校加入推免志愿储备池（按住 Ctrl / Shift 可多选）：",
            bg="#eff6ff",
            fg="#1d4ed8",
            font=("Microsoft YaHei UI", 9),
            padx=10,
            pady=8,
        ).pack(anchor="w")

        # 列表区域
        tree_frame = ttk.Frame(body)
        tree_frame.pack(fill="both", expand=True, pady=(0, 10))

        columns = ("school", "college", "status", "priority", "date")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.heading("school", text="学校")
        self.tree.heading("college", text="学院/项目")
        self.tree.heading("status", text="夏令营状态")
        self.tree.heading("priority", text="原优先级")
        self.tree.heading("date", text="举办/截止时间")

        self.tree.column("school", width=160)
        self.tree.column("college", width=180)
        self.tree.column("status", width=100)
        self.tree.column("priority", width=90)
        self.tree.column("date", width=120)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # 填充夏令营数据
        if self.camps:
            self.tree.tag_configure("waitlist", background="#ede9fe", foreground="#6d28d9")
            self.tree.tag_configure("inactive", foreground="#94a3b8")
            for index, camp in sorted(enumerate(self.camps), key=lambda pair: camp_import_sort_key(pair[1])):
                school = camp.get("school", "") or "未知学校"
                college = camp.get("college", "") or camp.get("project_name", "")
                status = camp_status_display(camp) or "待定"
                priority = camp.get("priority", "") or "普通"
                date_str = camp.get("signup_end", "") or camp.get("camp_start", "")
                tags = ("waitlist",) if status.startswith("候补") else (("inactive",) if camp_import_sort_key(camp)[0] == 5 else ())
                self.tree.insert("", "end", iid=str(index), values=(school, college, status, priority, date_str), tags=tags)
        else:
            ttk.Label(body, text="暂无学校项目，请先到学校录入页面添加。", foreground="#64748b").pack(anchor="w")

        # 底部选择梯队与操作
        bottom_bar = ttk.Frame(body)
        bottom_bar.pack(fill="x")

        ttk.Button(bottom_bar, text="取消", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(bottom_bar, text="确定导入所选学校", style="Accent.TButton", command=self._do_import).pack(side="right")

    def _do_import(self):
        selected_ids = self.tree.selection()
        if not selected_ids:
            messagebox.showinfo("提示", "请至少选择一所要导入的学校项目。", parent=self)
            return

        if callable(self.on_import):
            if self.on_import([int(iid) for iid in selected_ids], "稳妥", self.camps) is False:
                return
        self.destroy()


class RecommendationManagementView:
    """推免管理双区联动工作台：左侧大看板（820px+） + 右侧实战控制台（440px）"""

    def __init__(self, left_container: ttk.Frame, right_tab: ttk.Frame, app_context):
        self.left_parent = left_container
        self.right_parent = right_tab
        self.app = app_context
        self.service = app_context.recommendation_service
        self._load_snapshot()
        self._timer_job = None
        self._timer_stopped = False
        self._timer_busy = False
        self._timer_error = ""
        self._settings_dialog = None
        self._pending_notifications = []

        # 控件引用
        self.countdown_label = None
        self.stage_badge = None
        self.slot_cards_container = None
        self.slot_widgets = []
        self.slot_time_labels = {}
        self.step_labels = []
        self.reservoir_tree = None

        # 右侧速记控件引用
        self.right_note_school = None
        self.right_note_contact = None
        self.right_note_text = None

        self._build_views()
        self._restore_right_notes()
        self.left_parent.bind("<Destroy>", self._on_destroy, add="+")
        self.start_recommendation_timer()

    def _build_views(self):
        """构建左右联动工作台"""
        # 构建左侧主作战大看板 (占据原来日历与项目列表的超大空间)
        self._build_left_war_room(self.left_parent)
        # 构建右侧操作控制台与联络备忘录
        self._build_right_console(self.right_parent)

    # ==================== 左侧：决战主大看板 (820px+) ====================

    def _build_left_war_room(self, parent: ttk.Frame):
        main_box = ttk.Frame(parent, padding=12, style="Panel.TFrame")
        main_box.pack(fill="both", expand=True)

        # 1. 顶部：推免时钟与阶段进度仪表盘
        self._build_dashboard_header(main_box)

        # 2. 中部：在跑平行志愿监控大看板 (展开卡片)
        self._build_parallel_slots_section(main_box)

        # 3. 底部：意向志愿储备池 (宽屏大表格)
        self._build_reservoir_section(main_box)

    def _build_dashboard_header(self, parent: ttk.Frame):
        """左侧板块一：推免时钟与进度仪表盘"""
        header_frame = tk.Frame(parent, bg="#ffffff", bd=1, relief="solid", highlightthickness=0)
        header_frame.configure(highlightbackground="#cbd5e1")
        header_frame.pack(fill="x", pady=(0, 10))

        inner = tk.Frame(header_frame, bg="#ffffff", padx=16, pady=10)
        inner.pack(fill="x")

        # 标题栏 + 状态徽章
        top_row = tk.Frame(inner, bg="#ffffff")
        top_row.pack(fill="x")

        title_box = tk.Frame(top_row, bg="#ffffff")
        title_box.pack(side="left")

        self.title_label = tk.Label(
            title_box,
            text=f"🎯 {self.settings['year']} 全国推免服务系统 · 决战工作台",
            bg="#ffffff",
            fg="#0f172a",
            font=("Microsoft YaHei UI", 13, "bold"),
        )
        self.title_label.pack(side="left")

        self.stage_badge = tk.Label(
            title_box,
            text=" 阶段二：志愿填报与复试确认中 ",
            bg="#dbeafe",
            fg="#1d4ed8",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=8,
            pady=2,
        )
        self.stage_badge.pack(side="left", padx=12)

        # 醒目倒计时横幅
        countdown_row = tk.Frame(inner, bg="#fff7ed", padx=12, pady=7, bd=1, relief="solid")
        countdown_row.configure(highlightbackground="#fed7aa")
        countdown_row.pack(fill="x", pady=(8, 8))

        tk.Label(
            countdown_row,
            text="⏳ 关键节点倒计时：",
            bg="#fff7ed",
            fg="#c2410c",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(side="left")

        self.countdown_label = tk.Label(
            countdown_row,
            text="正在读取推免时间设置…",
            bg="#fff7ed",
            fg="#9a3412",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        self.countdown_label.pack(side="left", padx=6)

        # 四步进指示器
        stepper_row = tk.Frame(inner, bg="#ffffff")
        stepper_row.pack(fill="x", pady=(4, 0))

        steps = [
            ("① 系统开放与注册", False, "#64748b"),
            ("② 志愿填报开通", False, "#64748b"),
            ("③ 锁定与复试通知", False, "#64748b"),
            ("④ 待录取确认(锁定胜局)", False, "#64748b"),
        ]
        for i, (st_name, is_done, clr) in enumerate(steps):
            st_box = tk.Frame(stepper_row, bg="#ffffff")
            st_box.pack(side="left", expand=True, fill="x")

            bullet = "✓" if is_done and i == 0 else ("●" if is_done else "○")
            lbl = tk.Label(
                st_box,
                text=f"{bullet} {st_name}",
                bg="#ffffff",
                fg=clr,
                font=("Microsoft YaHei UI", 9, "bold" if is_done else "normal"),
            )
            lbl.pack(side="left")
            self.step_labels.append(lbl)

            if i < len(steps) - 1:
                arrow = tk.Label(st_box, text=" ──▶ ", bg="#ffffff", fg="#cbd5e1", font=("Consolas", 9))
                arrow.pack(side="right")

    def _build_parallel_slots_section(self, parent: ttk.Frame):
        """左侧板块二：在跑平行志愿监控大看板 (平铺展开大卡片)"""
        section = ttk.LabelFrame(parent, text="在跑平行志愿监控看板 (系统实时槽位)", style="Section.TLabelframe")
        section.pack(fill="x", pady=(0, 10))

        self.slot_cards_container = ttk.Frame(section, padding=(10, 8))
        self.slot_cards_container.pack(fill="x")

        self._render_slot_cards()

    def _render_slot_cards(self):
        """动态平铺渲染平行志愿卡片"""
        for widget in self.slot_cards_container.winfo_children():
            widget.destroy()
        self.slot_widgets.clear()
        self.slot_time_labels.clear()

        slot_count = int(self.settings.get("slot_count", 3))
        slots_to_show = self.running_slots[:slot_count]

        for i in range(5):
            self.slot_cards_container.columnconfigure(i, weight=1 if i < slot_count else 0,
                                                      uniform="slot_card" if i < slot_count else "")

        for index, slot in enumerate(slots_to_show):
            card = self._create_slot_card(self.slot_cards_container, slot, index)
            card.grid(row=0, column=index, sticky="nsew", padx=6 if index > 0 else (0, 6), pady=4)
            self.slot_widgets.append(card)
        self._update_clock_labels()

    def _create_slot_card(self, parent, slot: dict, index: int) -> tk.Widget:
        """创建单个大尺寸平行志愿卡片"""
        status = slot.get("status", "idle")

        if status == "locked":
            card_bg = "#fffbeb"
            border_color = "#fde68a"
            hours = (parse_time(slot['unlock_at']) - parse_time(slot['submit_time'])).total_seconds() / 3600
            tag_text = f"🔒 {hours:g}h 锁定中"
            tag_bg = "#fef3c7"
            tag_fg = "#b45309"
        elif status == "admission":
            card_bg = "#fef2f2"
            border_color = "#fca5a5"
            tag_text = "🔥 收到待录取通知!"
            tag_bg = "#fee2e2"
            tag_fg = "#dc2626"
        elif status == "admitted":
            card_bg = "#f0fdf4"
            border_color = "#86efac"
            tag_text = "🎉 拟录取成功(上岸)"
            tag_bg = "#dcfce7"
            tag_fg = "#15803d"
        elif status == "archived":
            card_bg, border_color = "#f8fafc", "#cbd5e1"
            tag_text, tag_bg, tag_fg = "已作废 / 归档", "#e2e8f0", "#64748b"
        else:  # idle
            card_bg = "#f8fafc"
            border_color = "#cbd5e1"
            tag_text = "○ 槽位空闲"
            tag_bg = "#e2e8f0"
            tag_fg = "#475569"

        frame = tk.Frame(parent, bg=card_bg, bd=1, relief="solid", highlightthickness=0)
        frame.configure(highlightbackground=border_color)

        inner = tk.Frame(frame, bg=card_bg, padx=14, pady=12)
        inner.pack(fill="both", expand=True)

        # 头部：志愿编号 + 状态
        card_header = tk.Frame(inner, bg=card_bg)
        card_header.pack(fill="x", pady=(0, 8))

        tk.Label(
            card_header,
            text=f"平行志愿 {index + 1}",
            bg=card_bg,
            fg="#0f172a",
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")

        tk.Label(
            card_header,
            text=f" {tag_text} ",
            bg=tag_bg,
            fg=tag_fg,
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=8,
            pady=2,
        ).pack(side="right")

        if status == "locked":
            # 锁定中状态
            tk.Label(
                inner,
                text=f"{slot.get('school', '')} · {slot.get('college', '')}",
                bg=card_bg,
                fg="#1e293b",
                font=("Microsoft YaHei UI", 11, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(0, 2))

            tk.Label(
                inner,
                text=f"报考专业：{slot.get('major', '')}",
                bg=card_bg,
                fg="#475569",
                font=("Microsoft YaHei UI", 9),
                anchor="w",
            ).pack(fill="x", pady=(0, 6))

            # 倒计时大框
            time_box = tk.Frame(inner, bg="#fef3c7", padx=10, pady=8, bd=1, relief="solid")
            time_box.configure(highlightbackground="#fde68a")
            time_box.pack(fill="x", pady=(0, 6))

            timer_label = tk.Label(
                time_box,
                text=f"剩余锁定时长：{slot.get('remaining_lock', '')}",
                bg="#fef3c7",
                fg="#b45309",
                font=("Microsoft YaHei UI", 10, "bold"),
                anchor="w",
            )
            timer_label.pack(fill="x")
            self.slot_time_labels[index] = timer_label
            tk.Label(
                time_box,
                text=f"预计自动解锁：{slot.get('unlock_at', '')} (或待招生办拒绝)",
                bg="#fef3c7",
                fg="#78350f",
                font=("Microsoft YaHei UI", 8),
                anchor="w",
            ).pack(fill="x", pady=(2, 0))

            tk.Label(
                inner,
                text=f"📞 {slot.get('contact', '')}",
                bg=card_bg,
                fg="#334155",
                font=("Microsoft YaHei UI", 9),
                anchor="w",
            ).pack(fill="x", pady=(2, 6))

            btn_row = tk.Frame(inner, bg=card_bg)
            btn_row.pack(fill="x", side="bottom", pady=(8, 0))

            ttk.Button(
                btn_row,
                text="致电速记",
                command=lambda s=slot: self._sync_to_right_notes(s),
            ).pack(side="left", padx=(0, 4))

            ttk.Button(
                btn_row,
                text="高校已拒绝(解封)",
                command=lambda idx=index: self._force_unlock_slot(idx),
            ).pack(side="right")
            ttk.Button(inner, text="登记收到待录取", command=lambda idx=index: self._record_admission(idx)).pack(fill="x", pady=(4, 0))

        elif status == "admission":
            # 待录取紧急通知状态
            tk.Label(
                inner,
                text=f"{slot.get('school', '')} · {slot.get('college', '')}",
                bg=card_bg,
                fg="#991b1b",
                font=("Microsoft YaHei UI", 11, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(0, 2))

            tk.Label(
                inner,
                text=f"拟录取专业：{slot.get('major', '')}",
                bg=card_bg,
                fg="#b91c1c",
                font=("Microsoft YaHei UI", 9),
                anchor="w",
            ).pack(fill="x", pady=(0, 6))

            urgent_box = tk.Frame(inner, bg="#fee2e2", padx=10, pady=8, bd=1, relief="solid")
            urgent_box.configure(highlightbackground="#fca5a5")
            urgent_box.pack(fill="x", pady=(0, 6))

            timer_label = tk.Label(
                urgent_box,
                text=f"⚠️ 确认时限倒计时：{slot.get('remaining_confirm', '')}",
                bg="#fee2e2",
                fg="#b91c1c",
                font=("Microsoft YaHei UI", 10, "bold"),
                anchor="w",
            )
            timer_label.pack(fill="x")
            self.slot_time_labels[index] = timer_label
            tk.Label(
                urgent_box,
                text=f"确认截止：{effective_admission_deadline(self.settings, slot):%m-%d %H:%M}",
                bg="#fee2e2",
                fg="#7f1d1d",
                font=("Microsoft YaHei UI", 8),
                anchor="w",
            ).pack(fill="x", pady=(2, 0))

            tk.Label(
                inner,
                text="⚠️ 特别警示：接受即锁定全国唯一录取名额，全网不可撤回！",
                bg=card_bg,
                fg="#dc2626",
                font=("Microsoft YaHei UI", 8, "bold"),
                anchor="w",
            ).pack(fill="x", pady=(2, 6))

            btn_row = tk.Frame(inner, bg=card_bg)
            btn_row.pack(fill="x", side="bottom", pady=(8, 0))

            ttk.Button(btn_row, text="放弃/拒绝", command=lambda idx=index: self._reject_admission(idx)).pack(side="left")
            ttk.Button(
                btn_row,
                text="🎉 确认接受",
                style="Accent.TButton",
                command=lambda s=slot, idx=index: self._open_accept_dialog(s, idx),
            ).pack(side="right")
            ttk.Button(inner, text="致电速记 / 核实时限", command=lambda idx=index: self._admission_contact(idx)).pack(fill="x", pady=(4, 0))

        elif status == "admitted":
            # 拟录取成功状态
            tk.Label(
                inner,
                text="🎊 恭喜！推免拟录取成功！",
                bg=card_bg,
                fg="#15803d",
                font=("Microsoft YaHei UI", 13, "bold"),
            ).pack(pady=(16, 4))
            tk.Label(
                inner,
                text=f"{slot.get('school', '')} · {slot.get('college', '')}\n{slot.get('major', '')}",
                bg=card_bg,
                fg="#166534",
                font=("Microsoft YaHei UI", 10),
                justify="center",
            ).pack(pady=(0, 10))
            tk.Label(
                inner,
                text="🎉 已确认接受待录取通知！\n夏令营与推免长跑圆满上岸，终得硕果！",
                bg=card_bg,
                fg="#14532d",
                font=("Microsoft YaHei UI", 10, "bold"),
                justify="center",
            ).pack(pady=(0, 14))

        elif status == "archived":
            tk.Label(inner, text=f"{slot.get('school') or '本志愿位已关闭'}\n已确认其他高校唯一待录取\n此志愿已归档，无法再次填报。",
                     bg=card_bg, fg="#64748b", font=("Microsoft YaHei UI", 10), justify="center").pack(pady=24)
            if slot.get("school"):
                ttk.Button(inner, text="查看联络记录", command=lambda s=slot: self._sync_to_right_notes(s)).pack()
        else:  # idle 空闲
            tk.Label(
                inner,
                text="[ 平行志愿槽位空闲 ]",
                bg=card_bg,
                fg="#94a3b8",
                font=("Microsoft YaHei UI", 11),
            ).pack(pady=(22, 6))
            tk.Label(
                inner,
                text="当前坑位未被系统锁定。\n可随时从下方意向储备池选入，\n或在研招网开网后直接填报新目标。",
                bg=card_bg,
                fg="#64748b",
                font=("Microsoft YaHei UI", 9),
                justify="center",
            ).pack(pady=(0, 18))

            btn_row = tk.Frame(inner, bg=card_bg)
            btn_row.pack(fill="x", side="bottom", pady=(8, 0))

            ttk.Button(
                btn_row,
                text="＋ 从下方储备池选入",
                command=lambda idx=index: self._fill_slot_from_reservoir(idx),
            ).pack(fill="x")

        return frame

    def _build_reservoir_section(self, parent: ttk.Frame):
        """左侧板块三：意向志愿储备池 (宽屏大表格)"""
        section = ttk.LabelFrame(parent, text="意向志愿储备池 (夏令营 / 预推免资格联动)", style="Section.TLabelframe")
        section.pack(fill="both", expand=True)

        body = ttk.Frame(section, padding=10)
        body.pack(fill="both", expand=True)

        # 顶部工具栏
        toolbar = ttk.Frame(body)
        toolbar.pack(fill="x", pady=(0, 8))


        import_btn = ttk.Button(
            toolbar,
            text="＋ 从已有学校导入",
            style="Accent.TButton",
            command=self.open_import_camp_dialog,
        )
        import_btn.pack(side="right")

        ttk.Button(toolbar, text="删除所选", command=self._delete_reservoir_item).pack(side="right", padx=6)

        # 宽屏大表格
        tree_frame = ttk.Frame(body)
        tree_frame.pack(fill="both", expand=True)

        columns = ("school", "college_major", "camp_result", "confirm_start", "confirm_duration", "confirm_deadline", "accepted_at")
        self.reservoir_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)

        headings = {
            "school": "目标高校",
            "college_major": "学院与拟报专业",
            "camp_result": "状态",
            "confirm_start": "录取确认开始时间",
            "confirm_duration": "允许确认时长",
            "confirm_deadline": "确认截止时间 ↑",
            "accepted_at": "实际接受时间",
        }
        widths = {
            "school": 110,
            "college_major": 165,
            "camp_result": 120,
            "confirm_start": 170,
            "confirm_duration": 100,
            "confirm_deadline": 140,
            "accepted_at": 140,
        }
        for col in columns:
            self.reservoir_tree.heading(col, text=headings[col])
            self.reservoir_tree.column(col, width=widths[col], minwidth=widths[col], anchor="w")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.reservoir_tree.yview)
        horizontal = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.reservoir_tree.xview)
        self.reservoir_tree.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        self.reservoir_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")

        self.reservoir_tree.tag_configure("scheduled", background="#f0fdf4", foreground="#166534")
        self.reservoir_tree.tag_configure("pending", foreground="#64748b")

        self.reservoir_tree.bind("<Double-1>", self._on_reservoir_double_click)
        self.reservoir_tree.bind("<<TreeviewSelect>>", self._on_reservoir_select)
        self._refresh_reservoir_tree()

        # 底部提示
        hint_bar = ttk.Frame(body)
        hint_bar.pack(fill="x", pady=(6, 0))
        tk.Label(
            hint_bar,
            text="按确认截止时间排序；开始时间待定的按允许时长排序。选中行可在右侧编辑，双击填入空闲志愿位。",
            foreground="#64748b",
            font=("Microsoft YaHei UI", 8),
        ).pack(side="left")

    # ==================== 右侧：实战控制台与招办联络簿 (440px) ====================

    def _build_right_console(self, parent: ttk.Frame):
        panel = ttk.Frame(parent, padding=12, style="Panel.TFrame")
        panel.pack(fill="both", expand=True)

        # 模块 1：招办紧急联络簿 & 催解锁速记本 (精炼实用，联动左侧选中高校)
        form_host = ttk.Frame(panel)
        form_host.pack(fill="both", expand=True, pady=(0, 10))
        canvas = tk.Canvas(form_host, highlightthickness=0)
        scrollbar = ttk.Scrollbar(form_host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        notes_box = ttk.LabelFrame(canvas, text="推免志愿信息与联络记录", padding=12)
        window = canvas.create_window(0, 0, window=notes_box, anchor="nw")
        notes_box.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

        tk.Label(notes_box, text="当前高校/学院:", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.right_note_school = ttk.Entry(notes_box)
        self.right_note_school.pack(fill="x", pady=(2, 6))

        ttk.Label(notes_box, text="拟报专业:").pack(anchor="w")
        self.right_major = ttk.Entry(notes_box)
        self.right_major.pack(fill="x", pady=(2, 6))
        self.right_timing = OfferTimingInput(notes_box, lambda: self.settings, self.service.clock)
        self.right_timing.pack(fill="x", pady=(0, 6))
        self.right_accepted_label = ttk.Label(notes_box, text="", foreground="#15803d", font=("Microsoft YaHei UI", 9, "bold"))
        self.right_accepted_label.pack(anchor="w", pady=(0, 4))

        tk.Label(notes_box, text="联系电话/研招办:", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.right_note_contact = ttk.Entry(notes_box)
        self.right_note_contact.pack(fill="x", pady=(2, 6))

        tk.Label(notes_box, text="致电沟通记录 / 承诺备忘:", font=("Microsoft YaHei UI", 9, "bold")).pack(anchor="w")
        self.right_note_text = tk.Text(notes_box, height=5, font=("Microsoft YaHei UI", 9))
        self.right_note_text.pack(fill="both", expand=True, pady=(2, 8))

        save_note_btn = ttk.Button(panel, text="保存志愿信息", style="Accent.TButton", command=self._save_right_note)
        save_note_btn.pack(fill="x", pady=(0, 8))
        self.app.bind_mousewheel_recursive(notes_box, canvas)
        self.app.bind_mousewheel(self.right_note_text, add=False)

        # 模块 4：推免避坑指南
        tips_box = tk.Frame(panel, bg="#f8fafc", bd=1, relief="solid", highlightthickness=0)
        tips_box.configure(highlightbackground="#e2e8f0")
        tips_box.pack(fill="x")

        tips_inner = tk.Frame(tips_box, bg="#f8fafc", padx=10, pady=8)
        tips_inner.pack(fill="x")

        tk.Label(
            tips_inner,
            text="💡 9月推免决战守则：",
            bg="#f8fafc",
            fg="#334155",
            font=("Microsoft YaHei UI", 9, "bold"),
        ).pack(anchor="w")
        self.rules_label = tk.Label(
            tips_inner,
            text=self._rules_text(),
            bg="#f8fafc",
            fg="#64748b",
            font=("Microsoft YaHei UI", 8),
            justify="left",
        )
        self.rules_label.pack(anchor="w", pady=(2, 0))

    # ==================== 交互控制与事件逻辑 ====================

    def open_settings_dialog(self):
        """打开规则与时间配置向导"""
        if self._settings_dialog is not None and self._settings_dialog.winfo_exists():
            self._settings_dialog.lift()
            return
        self._settings_dialog = RecommendationSettingsDialog(self.left_parent.winfo_toplevel(), self.settings, self._on_settings_saved)

    def open_initial_settings_dialog(self):
        if not self.service.export_data()["settings"]["is_initialized"]:
            self.open_settings_dialog()

    def _on_settings_saved(self, new_settings: dict):
        return self._run_action(lambda: self.service.save_recommendation_settings(new_settings))

    def open_import_camp_dialog(self):
        """打开夏令营导入弹窗"""
        camps = getattr(self.app, "camps", [])
        ImportCampDialog(self.left_parent.winfo_toplevel(), camps, self._on_camp_imported)

    def _on_camp_imported(self, indices: list[int], tier: str, camps: list[dict]):
        count = []
        if not self._run_action(lambda: count.append(self.service.import_camps_to_reservoir(indices, tier, camps))):
            return False
        messagebox.showinfo("导入完成", f"新增 {count[0]} 个备选志愿，重复项目已跳过。", parent=self.left_parent)
        return True

    def _refresh_reservoir_tree(self):
        """刷新储备池大表格"""
        if self.reservoir_tree is None:
            return
        selected = self.reservoir_tree.selection()
        scroll = self.reservoir_tree.yview()
        self._refreshing_reservoir = True
        self.reservoir_tree.delete(*self.reservoir_tree.get_children())

        for item in sorted(self.reservoir_items, key=lambda row: offer_sort_key(self.settings, row)):
            start, deadline = offer_window(self.settings, item)
            tag = "scheduled" if start or deadline else "pending"

            col_maj = f"{item.get('college', '')} - {item.get('major', '')}"
            self.reservoir_tree.insert(
                "",
                "end",
                iid=item["id"],
                values=(
                    item.get("school", ""),
                    col_maj,
                    item.get("camp_result", ""),
                    *offer_time_labels(self.settings, item),
                    parse_time(item["accepted_at"]).strftime("%m-%d %H:%M") if item.get("accepted_at") else "尚未接受",
                ),
                tags=(tag,),
            )
        for item_id in selected:
            if self.reservoir_tree.exists(item_id):
                self.reservoir_tree.selection_add(item_id)
        if scroll:
            self.reservoir_tree.yview_moveto(scroll[0])
        self.left_parent.after_idle(lambda: setattr(self, "_refreshing_reservoir", False))


    def _rules_text(self):
        return (f"• 志愿锁定{self.settings['lock_hours']}小时不可改，目标校无回应时尽早致电求解锁\n"
                "• 待录取通知只能接受一次，点击接受立即锁定不可更改\n"
                "• 候补考生保持手机畅通，随时准备补位填报")

    def _on_reservoir_select(self, event):
        """选中储备池某行，自动联动到右侧速记本"""
        if getattr(self, "_refreshing_reservoir", False):
            return
        selected_id = self.reservoir_tree.selection()
        if not selected_id:
            return
        target_res = next((r for r in self.reservoir_items if r["id"] == selected_id[0]), None)
        if target_res:
            self._sync_to_right_notes(target_res)

    def _sync_to_right_notes(self, data_dict: dict):
        """将选中的高校信息带入右侧联络速记本"""
        if not self._persist_note_draft():
            return
        # 保存当前草稿后重新取对象，避免用旧快照覆盖刚保存的内容。
        data = self.service.export_data()
        if "slot_id" in data_dict:
            target = next((s for s in data["running_slots"] if s["slot_id"] == data_dict["slot_id"]), None)
        else:
            target = next((r for r in data["reservoir_items"] if r["id"] == data_dict.get("id")), None)
        if target and self._run_action(lambda: self.service.sync_to_right_notes(target), refresh=False):
            self._restore_right_notes()
            self._load_snapshot()
            self._refresh_reservoir_tree()
            self._reschedule_timer()

    def _save_right_note(self):
        if self._persist_note_draft():
            self.reload_data()


    def _force_unlock_slot(self, slot_index: int):
        slot = self.running_slots[slot_index]
        res = messagebox.askyesno(
            "解封志愿确认",
            f"招生办是否已在系统中明确拒绝了【{slot.get('school')}】的志愿？\n确认后该志愿槽位将立即恢复空闲状态，您可以立刻填报下一所意向高校！",
            parent=self.left_parent.winfo_toplevel(),
        )
        if res:
            self._run_action(lambda: self.service.unlock_slot_manually(slot_index))

    def _open_accept_dialog(self, slot: dict, slot_index: int):
        dialog = tk.Toplevel(self.left_parent.winfo_toplevel())
        dialog.title("⚠️ 接受推免待录取通知终极确认")
        dialog.geometry("600x380")
        dialog.transient(self.left_parent.winfo_toplevel())
        dialog.grab_set()

        f = tk.Frame(dialog, bg="#fff1f2", padx=20, pady=16)
        f.pack(fill="both", expand=True)

        tk.Label(
            f,
            text="⚠️ 极为重要的推免录取最终确认",
            bg="#fff1f2",
            fg="#be123c",
            font=("Microsoft YaHei UI", 13, "bold"),
        ).pack(anchor="w")

        box = tk.Frame(f, bg="#ffffff", bd=1, relief="solid", padx=14, pady=12)
        box.configure(highlightbackground="#fda4af")
        box.pack(fill="x", pady=12)

        tk.Label(
            box,
            text=f"拟接收单位：{slot.get('school')} · {slot.get('college')}\n拟录取专业：{slot.get('major')}",
            bg="#ffffff",
            fg="#0f172a",
            font=("Microsoft YaHei UI", 11, "bold"),
            justify="left",
        ).pack(anchor="w")

        notice_text = (
            "【国家推免系统强制规则】\n"
            "1. 每位推免生在全国推免服务系统中只能接受一个待录取通知！\n"
            "2. 一旦确认接受，推免录取流程即全部终结，高校与教育部将锁定拟录取学籍。\n"
            "3. 其他所有院校发放的复试通知或待录取将瞬间自动作废，全网不可悔改！\n"
            "本操作仅登记结果，请确认你已在研招网接受该待录取。"
        )
        tk.Label(
            f,
            text=notice_text,
            bg="#fff1f2",
            fg="#9f1239",
            font=("Microsoft YaHei UI", 9),
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        def confirm_accept():
            if self._run_action(lambda: self.service.accept_admission(slot_index)):
                dialog.destroy()
                messagebox.showinfo("推免录取已锁定", "已记录唯一待录取确认，其他志愿已归档。祝贺你完成推免！", parent=self.left_parent)

        btn_bar = tk.Frame(f, bg="#fff1f2")
        btn_bar.pack(fill="x")
        ttk.Button(btn_bar, text="我再想想 (冷静)", command=dialog.destroy).pack(side="left")
        ttk.Button(btn_bar, text="已深思熟虑，确认上岸！", style="Accent.TButton", command=confirm_accept).pack(side="right")

    def _reject_admission(self, slot_index: int):
        slot = self.running_slots[slot_index]
        res = messagebox.askyesno(
            "放弃待录取确认",
            f"确定要放弃【{slot.get('school')}】的待录取通知吗？\n放弃后该名额将无法撤回，志愿槽位将重新空出。",
            parent=self.left_parent.winfo_toplevel(),
        )
        if res:
            self._run_action(lambda: self.service.reject_admission(slot_index))

    def _fill_slot_from_reservoir(self, slot_index: int):
        selected_id = self.reservoir_tree.selection()
        if not selected_id:
            messagebox.showinfo("提示", "请在下方意向储备池表格中先选中一所高校，或直接双击该行填入！", parent=self.left_parent)
            return
        target_res = next((r for r in self.reservoir_items if r["id"] == selected_id[0]), None)
        if target_res:
            self._do_fill_slot(slot_index, target_res)

    def _on_reservoir_double_click(self, event):
        if self.service.completed:
            messagebox.showinfo("推免流程已结束", "已确认唯一待录取，其他志愿已归档。", parent=self.left_parent)
            return
        selected_id = self.reservoir_tree.selection()
        if not selected_id:
            return
        target_res = next((r for r in self.reservoir_items if r["id"] == selected_id[0]), None)
        if not target_res:
            return

        idle_idx = next((i for i, s in enumerate(self.running_slots) if s.get("status") == "idle"), None)
        if idle_idx is None:
            messagebox.showwarning(
                "槽位已满",
                "当前所有在跑平行志愿槽位均处于锁定或处理中！\n请等待某个志愿锁定到期或被高校拒绝后再填入。",
                parent=self.left_parent,
            )
            return

        self._do_fill_slot(idle_idx, target_res)

    def _do_fill_slot(self, slot_index: int, res_item: dict):
        if not messagebox.askyesno("登记已提交志愿", f"确认已在研招网提交【{res_item.get('school')}】？\n本软件将从现在起记录锁定倒计时，不会代替你向研招网提交志愿。", parent=self.left_parent):
            return
        if self._persist_note_draft() and self._run_action(lambda: self.service.fill_slot_from_reservoir(slot_index, res_item)):
            self._restore_right_notes()

    def _load_snapshot(self):
        self.service.sync_reservoir_camp_results(getattr(self.app, "camps", []))
        data = self.service.export_data()
        self.settings = data["settings"]
        self.running_slots = data["running_slots"]
        self.reservoir_items = data["reservoir_items"]

    def reload_data(self, restore_notes=False):
        self._load_snapshot()
        self._render_slot_cards()
        self._refresh_reservoir_tree()
        if restore_notes:
            self._pending_notifications.clear()
            self._restore_right_notes()
        self._reschedule_timer()

    def _run_action(self, action, refresh=True):
        if refresh and not self._persist_note_draft():
            return False
        try:
            action()
        except Exception as exc:
            messagebox.showerror("推免操作未完成", str(exc), parent=self.left_parent.winfo_toplevel())
            return False
        if refresh:
            self.reload_data()
            self._restore_right_notes()
            self._reschedule_timer()
        return True

    def _restore_right_notes(self):
        notes = self.service.export_data()["quick_contact_notes"]
        self.right_note_school.delete(0, "end")
        self.right_note_school.insert(0, notes.get("current_school", ""))
        self.right_note_contact.delete(0, "end")
        self.right_note_contact.insert(0, notes.get("current_contact", ""))
        self.right_note_text.delete("1.0", "end")
        self.right_note_text.insert("1.0", notes.get("content", ""))
        self.right_major.delete(0, "end")
        self.right_major.insert(0, notes.get("major", ""))
        self.right_timing.set(notes)
        accepted = notes.get("accepted_at")
        self.right_accepted_label.configure(text="状态：🎉 已确认接受待录取（拟录取达成）" if accepted else "")

    def _persist_note_draft(self):
        if self.right_note_text is None:
            return True
        values = (self.right_note_school.get().strip(), self.right_note_contact.get().strip(),
                  self.right_note_text.get("1.0", "end-1c"))
        notes = self.service.export_data()["quick_contact_notes"]
        try:
            details = {"major": self.right_major.get().strip(), **self.right_timing.get()}
        except ValueError as exc:
            messagebox.showerror("志愿信息未保存", str(exc), parent=self.left_parent)
            return False
        previous = {"major": notes.get("major", ""), **offer_fields(notes)}
        if values == tuple(notes.get(k, "") for k in ("current_school", "current_contact", "content")) and details == previous:
            return True
        return self._run_action(lambda: self.service.save_right_note(*values, details=details), refresh=False)

    def _delete_reservoir_item(self):
        selected = self.reservoir_tree.selection()
        if not selected:
            messagebox.showinfo("提示", "请先选中要删除的备选志愿。", parent=self.left_parent)
            return
        if messagebox.askyesno("删除备选志愿", "确认从储备池删除所选志愿？在跑槽位中的记录将保留。", parent=self.left_parent):
            self._run_action(lambda: self.service.delete_reservoir_item(selected[0]))

    def _record_admission(self, slot_index):
        if not self._persist_note_draft():
            return
        self._load_snapshot()
        dialog = tk.Toplevel(self.left_parent.winfo_toplevel())
        dialog.title("登记收到待录取通知")
        dialog.transient(self.left_parent.winfo_toplevel())
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=16)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="请按高校通知填写确认开始时间与允许时长。").pack(anchor="w", pady=(0, 10))
        timing = OfferTimingInput(body, lambda: self.settings, self.service.clock)
        timing.pack(fill="x")
        item = dict(self.running_slots[slot_index])
        if item.get("offer_start_mode", "pending") == "pending":
            item.update(offer_start_mode="fixed", offer_start_time=self.service.clock().isoformat(timespec="minutes"))
        timing.set(item)
        def save():
            if self._run_action(lambda: self.service.receive_admission_from_rule(slot_index, timing.get())):
                dialog.destroy()
        ttk.Button(body, text="登记通知", command=save).pack(side="right", pady=(10, 0))
        return dialog

    def _admission_contact(self, slot_index):
        self._sync_to_right_notes(self.running_slots[slot_index])
        if messagebox.askyesno("核实待录取时限", "高校是否已明确延长确认时限？\n选择“是”登记新的截止时间，选择“否”继续在右侧速记。", parent=self.left_parent):
            return DateTimeDialog(self.left_parent, "更新确认时限", "选择高校确认的新截止时间：",
                                  self.running_slots[slot_index]["deadline"],
                                  lambda value: self._run_action(lambda: self.service.update_admission_deadline(slot_index, value)))

    def _update_clock_labels(self, now=None):
        now = now or self.service.clock()
        settings = self.settings
        reg = parse_time(settings["reg_open_time"])
        choice = parse_time(settings["choice_open_time"])
        statuses = [s["status"] for s in self.running_slots]
        complete = "admitted" in statuses
        admission_open = parse_time(settings["admission_open_time"]) if settings.get("admission_open_time") else None
        admission_close = parse_time(settings["admission_close_time"]) if settings.get("admission_close_time") else None
        text, remaining = timeline_countdown(settings, now)
        if complete:
            text = "已确认唯一待录取，推免流程已结束，其他志愿已归档。"
        elif not settings["is_initialized"]:
            text = "请先配置当年的注册、志愿填报及待录取确认时间。"
        self.countdown_label.configure(text=text, fg="#dc2626" if settings["notify_before_open"] and 0 < remaining <= 1800 else "#9a3412")
        self.title_label.configure(text=f"🎯 {settings['year']} 全国推免服务系统 · 决战工作台")
        if hasattr(self, "rules_label"):
            self.rules_label.configure(text=self._rules_text())
        if complete:
            badge, bg, fg = "🎊 已锁定唯一待录取", "#dcfce7", "#15803d"
        elif admission_close and now >= admission_close:
            badge, bg, fg = "待录取确认时间段已结束", "#f1f5f9", "#64748b"
        elif admission_open and now >= admission_open:
            badge, bg, fg = "🔥 待录取确认中", "#fee2e2", "#dc2626"
        elif now >= choice:
            badge, bg, fg = "阶段二：志愿填报与复试确认中", "#dbeafe", "#1d4ed8"
        elif now >= reg:
            badge, bg, fg = "阶段一：注册与信息核验", "#dcfce7", "#15803d"
        else:
            badge, bg, fg = "准备期：整理意向志愿", "#f1f5f9", "#64748b"
        self.stage_badge.configure(text=f" {badge} ", bg=bg, fg=fg)
        steps = [(f"① 系统注册 ({reg:%m.%d %H:%M})", now >= reg),
                 (f"② 志愿填报 ({choice:%m.%d %H:%M})", now >= choice),
                 (f"③ {settings['lock_hours']}h锁定与复试", any(s != "idle" for s in statuses)),
                 (f"④ 待录取确认 ({admission_open:%m.%d %H:%M})" if admission_open else "④ 待录取确认 (时间待设置)",
                  bool(admission_open and now >= admission_open) or complete)]
        for label, (name, active) in zip(self.step_labels, steps):
            label.configure(text=f"{'●' if active else '○'} {name}", fg="#16a34a" if active else "#64748b")
        for index, label in self.slot_time_labels.items():
            slot = self.running_slots[index]
            if slot["status"] == "locked":
                seconds = (parse_time(slot["unlock_at"]) - now).total_seconds()
                label.configure(text=f"剩余锁定时长：{minute_duration_text(seconds)}")
            elif slot["status"] == "admission":
                seconds = (effective_admission_deadline(settings, slot) - now).total_seconds()
                label.configure(text="⚠️ 确认时限已过，请核实" if seconds <= 0 else f"⚠️ 确认时限倒计时：{minute_duration_text(seconds)}")

    def start_recommendation_timer(self):
        if self._timer_job is None:
            self._timer_stopped = False
            self._timer_job = self.app.after(self.service.next_tick_delay_ms(), self._timer_tick)

    def _reschedule_timer(self):
        if self._timer_stopped:
            return
        if self._timer_job is not None:
            self.app.after_cancel(self._timer_job)
        self._timer_job = self.app.after(self.service.next_tick_delay_ms(), self._timer_tick)

    def stop_recommendation_timer(self):
        self._timer_stopped = True
        if self._timer_job is not None:
            self.app.after_cancel(self._timer_job)
            self._timer_job = None

    def _on_destroy(self, event):
        if event.widget is self.left_parent:
            self.stop_recommendation_timer()

    def _timer_tick(self):
        self._timer_job = None
        if self._timer_stopped or self._timer_busy:
            return
        self._timer_busy = True
        try:
            before = [s["status"] for s in self.running_slots]
            events = self.service.tick()
            self._load_snapshot()
            if before != [s["status"] for s in self.running_slots]:
                self._render_slot_cards()
            self._update_clock_labels()
            self._pending_notifications.extend(events)
            if self.service.completed:
                self._pending_notifications.clear()
            # 配置、确认等模态弹窗打开期间暂存提醒，避免抢占用户确认流程。
            if self._pending_notifications and self.app.grab_current() is None:
                urgent_events = [e for e in self._pending_notifications if e.get("kind") in ("admission", "offer_start")]
                normal_events = [e for e in self._pending_notifications if e.get("kind") not in ("admission", "offer_start")]
                self._pending_notifications.clear()

                top = self.left_parent.winfo_toplevel()
                try:
                    if top.state() == "withdrawn":
                        top.deiconify()
                    top.lift()
                    top.focus_force()
                except Exception:
                    pass

                if urgent_events:
                    AdmissionAlertModal(self.left_parent, urgent_events[-1])

                if normal_events:
                    messages = "\n\n".join(e["message"] for e in normal_events)
                    send_windows_toast("推免系统日程提醒", messages)
                    messagebox.showwarning("推免提醒", messages, parent=top)
            self._timer_error = ""
        except Exception as exc:
            if str(exc) != self._timer_error:
                self._timer_error = str(exc)
                messagebox.showerror("推免计时暂未更新", str(exc), parent=self.left_parent.winfo_toplevel())
        finally:
            self._timer_busy = False
            if not self._timer_stopped:
                # 持久化失败时稍后重试，避免短间隔忙循环。
                delay = 60000 if self._timer_error else self.service.next_tick_delay_ms()
                self._timer_job = self.app.after(delay, self._timer_tick)
