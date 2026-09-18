# -*- coding: utf-8 -*-
"""
推免待录取强提醒、音频警报循环与 Windows 原生系统通知模块
"""

from __future__ import annotations

import atexit
from array import array
import ctypes
from html import escape as xml_escape
import os
from pathlib import Path
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import ttk
import webbrowser
from datetime import datetime


class AlertSoundPlayer:
    """使用 miniaudio 输出预解码 PCM 的后台循环播放器。

    MP3 解码不能放在音频设备回调线程中，否则 Windows 调度或磁盘读取的
    短暂抖动就会直接表现为卡顿。播放前先解码到内存，回调线程只负责切片。
    """

    _instance: AlertSoundPlayer | None = None
    _audio_cache: tuple[str, array, int] | None = None
    _audio_cache_lock = threading.Lock()
    _CHANNELS = 2
    _SAMPLE_RATE = 44100
    _BUFFER_MSEC = 500
    _GAIN = 1.8

    def __init__(self):
        self._device = None
        self._thread: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._is_playing = False
        atexit.register(self.stop)

    @classmethod
    def get_instance(cls) -> AlertSoundPlayer:
        if cls._instance is None:
            cls._instance = AlertSoundPlayer()
        return cls._instance

    def play_loop(self, audio_path: str | Path | None = None) -> bool:
        """开始无阻塞循环播放指定音频文件"""
        if self._is_playing:
            return True

        if audio_path is None:
            candidates = [
                Path(getattr(sys, "_MEIPASS", "")) / "music1.mp3",
                Path(sys.executable).resolve().parent / "music1.mp3",
                Path(__file__).resolve().parent / "music1.mp3",
            ]
            for cand in candidates:
                if cand and cand.is_file():
                    audio_path = cand
                    break
            else:
                return False
        else:
            audio_path = Path(audio_path)
            if not audio_path.exists():
                fallback = Path(__file__).resolve().parent / audio_path.name
                if fallback.is_file():
                    audio_path = fallback
                else:
                    return False

        stop_event = threading.Event()
        ready_event = threading.Event()
        failed = []
        self._stop_event = stop_event

        def play_worker():
            device = None
            try:
                import miniaudio

                output_format = miniaudio.SampleFormat.SIGNED16
                cache_key = str(audio_path.resolve())
                try:
                    cache_key += f"|{audio_path.stat().st_mtime_ns}|{audio_path.stat().st_size}"
                except OSError:
                    pass

                # 一次性解码并缓存到内存，避免在设备回调线程中解码 MP3 造成
                # 音频断续；同一资源的后续提醒也不必重复做耗时的增益处理。
                with self._audio_cache_lock:
                    cached = self._audio_cache
                    if cached is not None and cached[0] == cache_key:
                        _, source_samples, total_frames = cached
                    else:
                        decoded = miniaudio.decode_file(
                            str(audio_path),
                            output_format=output_format,
                            nchannels=self._CHANNELS,
                            sample_rate=self._SAMPLE_RATE,
                        )
                        source_samples = decoded.samples
                        total_frames = int(decoded.num_frames)
                        if total_frames <= 0 or not source_samples:
                            raise ValueError("audio file contains no samples")

                        # 资源本身录音电平偏低，播放前做一次软件增益；饱和裁剪避免
                        # 放大后的峰值溢出 16-bit PCM。该循环只执行一次，不在回调中执行。
                        gain = float(self._GAIN)
                        if gain != 1.0:
                            amplified = array("h")
                            amplified.extend(
                                max(-32768, min(32767, int(sample * gain)))
                                for sample in source_samples
                            )
                            source_samples = amplified
                        self._audio_cache = (cache_key, source_samples, total_frames)

                channels = self._CHANNELS
                if stop_event.is_set():
                    return

                device = miniaudio.PlaybackDevice(
                    output_format=output_format,
                    nchannels=channels,
                    sample_rate=self._SAMPLE_RATE,
                    buffersize_msec=self._BUFFER_MSEC,
                    app_name="夏令营与推免日程助手",
                )
                self._device = device

                def stream_loop():
                    """按设备请求的帧数从内存 PCM 中循环取样。"""
                    position = 0
                    requested_frames = yield array("h")
                    while not stop_event.is_set():
                        try:
                            frame_count = int(requested_frames or 0)
                        except (TypeError, ValueError):
                            frame_count = 0
                        if frame_count <= 0:
                            frame_count = 1024

                        result = array("h")
                        remaining = frame_count
                        while remaining > 0:
                            available = total_frames - position
                            take = min(remaining, available)
                            start = position * channels
                            end = start + take * channels
                            result.extend(source_samples[start:end])
                            position += take
                            if position >= total_frames:
                                position = 0
                            remaining -= take
                        requested_frames = yield result

                playback_stream = stream_loop()
                next(playback_stream)
                device.start(playback_stream)
                ready_event.set()
                while not stop_event.wait(0.1):
                    pass
            except Exception:
                failed.append(True)
            finally:
                ready_event.set()
                if device is not None:
                    try:
                        device.stop()
                    except Exception:
                        pass
                    try:
                        device.close()
                    except Exception:
                        pass
                self._device = None
                self._is_playing = False

        try:
            self._thread = threading.Thread(target=play_worker, name="alert-audio", daemon=True)
            self._thread.start()
            if not ready_event.wait(8.0) or failed:
                self.stop()
                return False
            self._is_playing = True
            return True
        except Exception:
            self._thread = None
            self._stop_event = None
            self._is_playing = False
            return False

    def stop(self) -> None:
        """停止播放并释放后台进程"""
        if self._stop_event is not None:
            self._stop_event.set()
        if self._device is not None:
            try:
                self._device.stop()
            except Exception:
                pass
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.5)
        self._thread = None
        self._stop_event = None
        self._is_playing = False

    @property
    def is_playing(self) -> bool:
        if self._thread is not None and not self._thread.is_alive():
            self._thread = None
            self._is_playing = False
        return self._is_playing


def send_windows_toast(title: str, message: str) -> None:
    """发送 Windows 10/11 原生 Toast 系统横幅通知 (非阻塞异步调用)"""
    # 内容位于 Toast XML 中，先做 XML 转义；PowerShell here-string 不需要额外
    # 拼接用户输入的引号转义，避免学校名称含 '&' 或 '<' 时通知 XML 无法解析。
    clean_title = xml_escape(str(title))
    clean_msg = xml_escape(str(message))

    ps_code = f"""
try {{
    [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
    [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null

    $template = @"
<toast duration="long">
    <visual>
        <binding template="ToastGeneric">
            <text>{clean_title}</text>
            <text>{clean_msg}</text>
        </binding>
    </visual>
    <audio src="ms-winsoundevent:Notification.Looping.Alarm" loop="true" />
</toast>
"@
    $xml = New-Object Windows.Data.Xml.Dom.XmlDocument
    $xml.LoadXml($template)
    $toast = New-Object Windows.UI.Notifications.ToastNotification $xml
    $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("夏令营与推免日程助手")
    $notifier.Show($toast)
}} catch {{}}
"""
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    except Exception:
        pass


def flash_window(window: tk.Misc) -> None:
    """高频闪烁 Windows 任务栏以吸引注意"""
    if sys.platform != "win32":
        return
    try:
        hwnd = window.winfo_id()
        # FLASHWINFO structure: FLASHW_ALL | FLASHW_TIMERNOSTOP
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hwnd", ctypes.c_void_p),
                ("dwFlags", ctypes.c_uint),
                ("uCount", ctypes.c_uint),
                ("dwTimeout", ctypes.c_uint),
            ]
        finfo = FLASHWINFO(
            cbSize=ctypes.sizeof(FLASHWINFO),
            hwnd=hwnd,
            dwFlags=3 | 12,  # FLASHW_ALL | FLASHW_TIMERNOSTOP
            uCount=0,
            dwTimeout=0,
        )
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(finfo))
    except Exception:
        pass


class AdmissionAlertModal(tk.Toplevel):
    """超级醒目的推免录取确认强提醒置顶弹窗"""

    def __init__(
        self,
        master,
        event_info: dict,
        *,
        on_confirm_callback=None,
        audio_path: str | Path | None = None,
    ):
        super().__init__(master)
        self.event_info = event_info
        self.on_confirm = on_confirm_callback
        self.sound_player = AlertSoundPlayer.get_instance()

        self.is_deadline_warning = event_info.get("kind") == "deadline"
        self.title("🚨【超高优先级】待录取确认截止提醒！" if self.is_deadline_warning else "🚨【超高优先级】高校待录取确认开启！")
        self.geometry("620x490")
        self.minsize(580, 460)
        self.configure(bg="#fef2f2")

        # 强制最高层级置顶
        self.attributes("-topmost", True)
        self.transient(master.winfo_toplevel())
        self.grab_set()

        self._ticker_job = None
        self._build_ui()

        # 启动音乐循环播放
        self.sound_player.play_loop(audio_path)

        # 发送系统通知与高亮闪烁
        school_name = event_info.get("school", "高校")
        send_windows_toast(
            f"🚨【高校待录取确认开始】{school_name}",
            f"{school_name} 待录取确认流程现已开启！请尽快前往研招网推免系统确认录取！"
        )
        self.after(50, lambda: flash_window(self))

        self.protocol("WM_DELETE_WINDOW", self._on_dismiss)

    def _build_ui(self):
        main_container = tk.Frame(self, bg="#fef2f2", padx=20, pady=16)
        main_container.pack(fill="both", expand=True)

        # 1. 顶部警报横幅 (红黄高对比度冲击力横幅)
        banner = tk.Frame(main_container, bg="#dc2626", bd=0, padx=16, pady=12)
        banner.pack(fill="x", pady=(0, 14))

        tk.Label(
            banner,
            text=("🚨 紧急推免提醒 · 待录取确认将在 5 分钟后截止 🚨"
                  if self.is_deadline_warning else "🚨 紧急推免提醒 · 高校待录取确认已开启 🚨"),
            font=("Microsoft YaHei UI", 13, "bold"),
            bg="#dc2626",
            fg="#ffffff",
        ).pack(anchor="center")

        tk.Label(
            banner,
            text=("请立即前往研招网完成确认，逾期可能失去录取资格。"
                  if self.is_deadline_warning else "全国推免服务系统已到达确认时间，高校确认通道现已开启！"),
            font=("Microsoft YaHei UI", 9),
            bg="#dc2626",
            fg="#fecaca",
        ).pack(anchor="center", pady=(4, 0))

        # 2. 高校与专业核心信息卡片
        info_card = tk.Frame(main_container, bg="#ffffff", bd=1, relief="solid", padx=16, pady=14)
        info_card.configure(highlightbackground="#fca5a5")
        info_card.pack(fill="x", pady=(0, 12))

        school = self.event_info.get("school", "未知学校")
        college = self.event_info.get("college", "")
        major = self.event_info.get("major", "待确认专业")
        contact = self.event_info.get("contact", "暂无联系方式")

        tk.Label(
            info_card,
            text=f"拟接收单位：{school}  {college}",
            font=("Microsoft YaHei UI", 12, "bold"),
            bg="#ffffff",
            fg="#0f172a",
        ).pack(anchor="w")

        tk.Label(
            info_card,
            text=f"拟录取专业：{major}",
            font=("Microsoft YaHei UI", 10),
            bg="#ffffff",
            fg="#2563eb",
        ).pack(anchor="w", pady=(4, 6))

        tk.Label(
            info_card,
            text=f"招办/老师联系方式：{contact}",
            font=("Microsoft YaHei UI", 9),
            bg="#ffffff",
            fg="#475569",
        ).pack(anchor="w")

        # 3. 紧迫时限倒计时警报条
        alert_box = tk.Frame(main_container, bg="#fffbeb", bd=1, relief="solid", padx=14, pady=10)
        alert_box.configure(highlightbackground="#fcd34d")
        alert_box.pack(fill="x", pady=(0, 12))

        self.deadline_label = tk.Label(
            alert_box,
            text=(self.event_info.get("message") or "⏳ 请立即完成待录取确认。"),
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#fffbeb",
            fg="#b45309",
            justify="left",
            wraplength=540,
        )
        self.deadline_label.pack(anchor="w")

        # 4. 研招网关键规则
        rule_label = tk.Label(
            main_container,
            text="【国家推免重要须知】推免生在全网只能接受一个待录取通知，一旦确认接受将锁定全国唯一拟录取学籍，其他志愿将全部自动作废！",
            font=("Microsoft YaHei UI", 8),
            bg="#fef2f2",
            fg="#991b1b",
            justify="left",
            wraplength=560,
        )
        rule_label.pack(anchor="w", pady=(0, 14))

        # 5. 底部操作大按钮
        # 主行动按钮：直接打开研招网推免系统
        open_web_btn = tk.Button(
            main_container,
            text="🌐 立即打开研招网推免系统并前往操作",
            font=("Microsoft YaHei UI", 11, "bold"),
            bg="#dc2626",
            fg="#ffffff",
            activebackground="#b91c1c",
            activeforeground="#ffffff",
            bd=0,
            relief="flat",
            cursor="hand2",
            pady=10,
            command=self._open_chsi,
        )
        open_web_btn.pack(fill="x", pady=(0, 8))

        # 次按钮行
        sub_bar = tk.Frame(main_container, bg="#fef2f2")
        sub_bar.pack(fill="x")

        tk.Button(
            sub_bar,
            text="✅ 我已知晓，停止报警音乐",
            font=("Microsoft YaHei UI", 9),
            bg="#ffffff",
            fg="#0f172a",
            bd=1,
            relief="solid",
            cursor="hand2",
            padx=12,
            pady=6,
            command=self._on_dismiss,
        ).pack(side="right")

    def _open_chsi(self):
        """停止报警并调用系统默认浏览器打开研招网推免系统"""
        self.sound_player.stop()
        try:
            webbrowser.open("https://yz.chsi.com.cn/tm/")
        except Exception:
            pass
        if callable(self.on_confirm):
            self.on_confirm()
        self.destroy()

    def _on_dismiss(self):
        """用户点击确认知晓，停止音乐并关闭弹窗"""
        self.sound_player.stop()
        if callable(self.on_confirm):
            self.on_confirm()
        self.destroy()
