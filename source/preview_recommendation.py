# -*- coding: utf-8 -*-
"""
推免管理前端预览启动器
直接启动软件并自动切换至【信息助手】->【推免管理】，方便快速审核前端设计。
"""

from summer_camp_planner import SummerCampPlanner

if __name__ == "__main__":
    app = SummerCampPlanner()
    # 自动选中右侧【信息助手】主Tab
    if hasattr(app, "profile_tab") and app.profile_tab is not None:
        app.notebook.select(app.profile_tab)
    # 自动激活【推免管理】胶囊
    app.show_profile_section("recommendation")
    # 提升窗口到最前
    app.lift()
    app.attributes("-topmost", True)
    app.after(500, lambda: app.attributes("-topmost", False))
    app.mainloop()
