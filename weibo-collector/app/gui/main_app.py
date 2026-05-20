"""CSL Sentinel 桌面 GUI（CustomTkinter）。"""
from __future__ import annotations

import sys
import threading
import tkinter as tk
from datetime import date, timedelta
from pathlib import Path
from tkinter import messagebox

import customtkinter as ctk

# 确保可从 weibo-collector 根目录导入 app
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config import bootstrap, require_api_key
from app.llm_settings import PRESETS, load_settings, save_settings
from app.paths import get_reports_dir, get_weibo_collector_dir
from app.pipeline_core import PipelineContext
from app.pipeline_runner import get_pipeline_runner
from app.reports_util import find_latest_report, open_report
from app.gui.log_bridge import UiLogBridge
from app.web.server import (
    DEFAULT_PORT,
    get_server_info,
    start_lan_server,
    stop_lan_server,
)
from app.weibo_login import get_login_session, is_login_file_present

STEP_COUNT = 4
LOG_TRIM_LINES = 2000


class SentinelApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("light")
        ctk.set_default_color_theme("blue")

        self.title("足球舆情监测系统 CSL Sentinel")
        self.geometry("920x720")
        self.minsize(800, 640)

        bootstrap()
        self._login = get_login_session()
        self._runner = get_pipeline_runner()
        self._last_report: Path | None = None

        self._build_ui()
        self._run_log_bridge = UiLogBridge(
            self._append_log_batch,
            lambda ms, fn: self.after(ms, fn),
        )
        self._login_log_bridge = UiLogBridge(
            self._append_login_log_batch,
            lambda ms, fn: self.after(ms, fn),
        )
        self._refresh_login_status()
        self.after(50, self._load_settings_into_form)

    def _build_ui(self) -> None:
        header = ctk.CTkLabel(
            self,
            text="中国足球协会 · 舆情监测（桌面版）",
            font=ctk.CTkFont(size=18, weight="bold"),
        )
        header.pack(pady=(12, 8))

        self.tabs = ctk.CTkTabview(self, width=880, height=620)
        self.tabs.pack(padx=16, pady=8, fill="both", expand=True)

        self.tab_run = self.tabs.add("监测任务")
        self.tab_login = self.tabs.add("微博登录")
        self.tab_lan = self.tabs.add("手机访问")
        self.tab_settings = self.tabs.add("设置")

        self._build_run_tab()
        self._build_login_tab()
        self._build_lan_tab()
        self._build_settings_tab()

    # —— 监测任务 ——
    def _build_run_tab(self) -> None:
        form = ctk.CTkFrame(self.tab_run)
        form.pack(fill="x", padx=12, pady=12)

        ctk.CTkLabel(form, text="搜索关键词").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        self.entry_keyword = ctk.CTkEntry(form, width=320, placeholder_text="例如：中超联赛")
        self.entry_keyword.grid(row=0, column=1, columnspan=2, sticky="w", padx=8, pady=6)
        self.entry_keyword.insert(0, "中超联赛")

        today = date.today()
        week_ago = today - timedelta(days=7)
        ctk.CTkLabel(form, text="开始日期").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        self.entry_start = ctk.CTkEntry(form, width=140, placeholder_text="YYYY-MM-DD")
        self.entry_start.grid(row=1, column=1, sticky="w", padx=8, pady=6)
        self.entry_start.insert(0, week_ago.isoformat())

        ctk.CTkLabel(form, text="结束日期").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        self.entry_end = ctk.CTkEntry(form, width=140, placeholder_text="YYYY-MM-DD")
        self.entry_end.grid(row=2, column=1, sticky="w", padx=8, pady=6)
        self.entry_end.insert(0, today.isoformat())

        ctk.CTkLabel(form, text="目标条数").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        self.entry_count = ctk.CTkEntry(form, width=100)
        self.entry_count.grid(row=3, column=1, sticky="w", padx=8, pady=6)
        self.entry_count.insert(0, "80")

        self.chk_efficient = ctk.CTkCheckBox(
            form, text="高效模式（少调 LLM，速度更快、质量略降）"
        )
        self.chk_efficient.grid(row=4, column=1, columnspan=2, sticky="w", padx=8, pady=8)

        btn_row = ctk.CTkFrame(self.tab_run, fg_color="transparent")
        btn_row.pack(fill="x", padx=12, pady=4)
        self.btn_start = ctk.CTkButton(
            btn_row, text="开始监测", width=140, command=self._on_start_pipeline
        )
        self.btn_start.pack(side="left", padx=8)
        self.btn_open_report = ctk.CTkButton(
            btn_row,
            text="打开最新报告",
            width=140,
            fg_color="#4b5563",
            command=self._on_open_latest_report,
        )
        self.btn_open_report.pack(side="left", padx=8)
        ctk.CTkButton(
            btn_row,
            text="打开报告目录",
            width=140,
            fg_color="#6b7280",
            command=self._on_open_reports_dir,
        ).pack(side="left", padx=8)

        prog_frame = ctk.CTkFrame(self.tab_run)
        prog_frame.pack(fill="x", padx=12, pady=8)
        self.label_step = ctk.CTkLabel(prog_frame, text="当前步骤：等待开始")
        self.label_step.pack(anchor="w", padx=12, pady=(8, 4))
        self.progress = ctk.CTkProgressBar(prog_frame, width=820)
        self.progress.pack(padx=12, pady=4)
        self.progress.set(0)
        self.label_progress_pct = ctk.CTkLabel(prog_frame, text="0%")
        self.label_progress_pct.pack(anchor="e", padx=12, pady=(0, 8))

        log_frame = ctk.CTkFrame(self.tab_run)
        log_frame.pack(fill="both", expand=True, padx=12, pady=(4, 12))
        ctk.CTkLabel(log_frame, text="运行日志").pack(anchor="w", padx=12, pady=(8, 4))
        self.log_box = ctk.CTkTextbox(log_frame, height=220, font=ctk.CTkFont(family="Consolas", size=12))
        self.log_box.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    # —— 登录 ——
    def _build_login_tab(self) -> None:
        frame = ctk.CTkFrame(self.tab_login)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        self.label_login_status = ctk.CTkLabel(
            frame,
            text="登录状态：检查中…",
            font=ctk.CTkFont(size=14),
        )
        self.label_login_status.pack(anchor="w", padx=16, pady=12)

        ctk.CTkLabel(
            frame,
            text=(
                "1. 点击「打开微博登录窗口」\n"
                "2. 在弹出浏览器中扫码登录微博\n"
                "3. 登录成功后点击「确认已登录并保存」"
            ),
            justify="left",
        ).pack(anchor="w", padx=16, pady=8)

        row = ctk.CTkFrame(frame, fg_color="transparent")
        row.pack(anchor="w", padx=16, pady=16)
        ctk.CTkButton(
            row, text="打开微博登录窗口", command=self._on_login_start
        ).pack(side="left", padx=(0, 12))
        ctk.CTkButton(
            row,
            text="确认已登录并保存",
            fg_color="#16a34a",
            command=self._on_login_confirm,
        ).pack(side="left")

        self.login_log = ctk.CTkTextbox(frame, height=180)
        self.login_log.pack(fill="both", expand=True, padx=16, pady=16)

    # —— 手机访问（局域网 Web）——
    def _build_lan_tab(self) -> None:
        frame = ctk.CTkFrame(self.tab_lan)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            frame,
            text="方案 A：家中 Windows 电脑跑全流程，手机浏览器通过局域网访问。",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=(0, 12))

        self.switch_lan = ctk.CTkSwitch(
            frame,
            text="允许局域网访问（手机与 PC 同一 WiFi）",
            command=self._on_lan_switch,
        )
        self.switch_lan.pack(anchor="w", pady=8)

        self.label_lan_url = ctk.CTkLabel(
            frame, text="服务地址：（未开启）", font=ctk.CTkFont(size=14)
        )
        self.label_lan_url.pack(anchor="w", pady=6)

        self.label_lan_token = ctk.CTkLabel(
            frame, text="访问令牌：（开启后显示）", wraplength=760, justify="left"
        )
        self.label_lan_token.pack(anchor="w", pady=6)

        ctk.CTkLabel(
            frame,
            text=(
                "手机浏览器打开上述地址，填入令牌后即可下任务、看进度与报告。\n"
                "微博扫码登录仍须在本机「微博登录」页完成。\n"
                f"默认端口 {DEFAULT_PORT}；若无法访问，请在 Windows 防火墙允许专用网络入站。"
            ),
            text_color="gray",
            wraplength=760,
            justify="left",
        ).pack(anchor="w", pady=12)

        self._refresh_lan_labels()

    def _refresh_lan_labels(self) -> None:
        info = get_server_info()
        if info.running:
            self.switch_lan.select()
            self.label_lan_url.configure(
                text=f"服务地址：{info.phone_url}\n（本机 IP：{info.lan_ip}，端口 {info.port}）"
            )
            self.label_lan_token.configure(
                text=f"访问令牌（手机 H5 首次需填写）：\n{info.token}"
            )
        else:
            self.switch_lan.deselect()
            self.label_lan_url.configure(text="服务地址：（未开启）")
            self.label_lan_token.configure(text="访问令牌：（开启后显示）")

    def _on_lan_switch(self) -> None:
        if self.switch_lan.get():
            try:
                start_lan_server()
                self._refresh_lan_labels()
                messagebox.showinfo(
                    "手机访问",
                    "局域网服务已开启。请用手机浏览器打开显示的地址，并填入访问令牌。",
                )
            except Exception as e:
                self.switch_lan.deselect()
                messagebox.showerror("手机访问", str(e))
        else:
            stop_lan_server()
            self._refresh_lan_labels()

    # —— 设置 ——
    def _build_settings_tab(self) -> None:
        frame = ctk.CTkFrame(self.tab_settings)
        frame.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(frame, text="大模型 API Key").grid(row=0, column=0, sticky="w", padx=12, pady=8)
        self.entry_api_key = ctk.CTkEntry(frame, width=400, show="*")
        self.entry_api_key.grid(row=0, column=1, sticky="w", padx=12, pady=8)

        ctk.CTkLabel(frame, text="模型预设").grid(row=1, column=0, sticky="w", padx=12, pady=8)
        self.preset_var = ctk.StringVar(value="deepseek")
        self.option_preset = ctk.CTkOptionMenu(
            frame,
            variable=self.preset_var,
            values=[p.label for p in PRESETS.values()],
            command=self._on_preset_changed,
            width=280,
        )
        self.option_preset.grid(row=1, column=1, sticky="w", padx=12, pady=8)

        ctk.CTkLabel(frame, text="Base URL").grid(row=2, column=0, sticky="w", padx=12, pady=8)
        self.entry_base_url = ctk.CTkEntry(frame, width=400)
        self.entry_base_url.grid(row=2, column=1, sticky="w", padx=12, pady=8)

        ctk.CTkLabel(frame, text="Model").grid(row=3, column=0, sticky="w", padx=12, pady=8)
        self.entry_model = ctk.CTkEntry(frame, width=400)
        self.entry_model.grid(row=3, column=1, sticky="w", padx=12, pady=8)

        ctk.CTkButton(frame, text="保存设置", command=self._on_save_settings).grid(
            row=4, column=1, sticky="w", padx=12, pady=16
        )

        ctk.CTkLabel(
            frame,
            text=f"配置文件：{get_weibo_collector_dir() / '.env'}",
            text_color="gray",
        ).grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=8)

    def _preset_id_from_label(self, label: str) -> str:
        for p in PRESETS.values():
            if p.label == label:
                return p.id
        return "deepseek"

    def _on_preset_changed(self, _choice: str) -> None:
        pid = self._preset_id_from_label(self.preset_var.get())
        if pid == "deepseek":
            self.entry_base_url.configure(state="disabled")
            self.entry_model.configure(state="disabled")
            self.entry_base_url.delete(0, "end")
            self.entry_model.delete(0, "end")
            self.entry_base_url.insert(0, PRESETS["deepseek"].base_url)
            self.entry_model.insert(0, PRESETS["deepseek"].model)
        else:
            self.entry_base_url.configure(state="normal")
            self.entry_model.configure(state="normal")

    def _load_settings_into_form(self) -> None:
        s = load_settings()
        self.entry_api_key.delete(0, "end")
        self.entry_api_key.insert(0, s.api_key)
        preset = PRESETS.get(s.preset_id, PRESETS["deepseek"])
        self.preset_var.set(preset.label)
        self.entry_base_url.delete(0, "end")
        self.entry_model.delete(0, "end")
        self.entry_base_url.insert(0, s.base_url)
        self.entry_model.insert(0, s.model)
        self._on_preset_changed(preset.label)

    def _on_save_settings(self) -> None:
        try:
            pid = self._preset_id_from_label(self.preset_var.get())
            save_settings(
                pid,
                self.entry_api_key.get().strip(),
                base_url=self.entry_base_url.get().strip(),
                model=self.entry_model.get().strip(),
            )
            bootstrap()
            messagebox.showinfo("设置", "已保存 API 与模型配置。")
        except Exception as e:
            messagebox.showerror("设置", str(e))

    def _refresh_login_status(self) -> None:
        if is_login_file_present():
            self.label_login_status.configure(text="登录状态：已保存微博登录态 ✓")
        else:
            self.label_login_status.configure(
                text="登录状态：未登录（采集前请先完成微博登录）"
            )

    def _append_log_batch(self, text: str) -> None:
        if not text:
            return
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self._trim_textbox(self.log_box)

    def _append_login_log_batch(self, text: str) -> None:
        if not text:
            return
        self.login_log.insert("end", text + "\n")
        self.login_log.see("end")
        self._trim_textbox(self.login_log)

    @staticmethod
    def _trim_textbox(box: ctk.CTkTextbox) -> None:
        try:
            end_index = box.index("end-1c")
            line_count = int(end_index.split(".")[0])
        except (ValueError, tk.TclError):
            return
        if line_count > LOG_TRIM_LINES:
            box.delete("1.0", f"{line_count - LOG_TRIM_LINES}.0")

    def _set_running_ui(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.btn_start.configure(state=state)
        self.entry_keyword.configure(state=state)
        self.entry_start.configure(state=state)
        self.entry_end.configure(state=state)
        self.entry_count.configure(state=state)

    def _on_login_start(self) -> None:
        self._login_log_bridge.write("—— 启动登录 ——")
        get_login_session().start(self._login_log_bridge.write)

    def _on_login_confirm(self) -> None:
        self.label_login_status.configure(text="登录状态：正在保存…")

        def _do_confirm() -> None:
            ok, msg = get_login_session().confirm_save(self._login_log_bridge.write)

            def _on_main() -> None:
                self._refresh_login_status()
                if ok:
                    messagebox.showinfo("登录", "微博登录态已保存。")
                else:
                    messagebox.showwarning("登录", msg)

            self.after(0, _on_main)

        threading.Thread(target=_do_confirm, daemon=True).start()

    def _on_start_pipeline(self) -> None:
        if self._runner.is_running():
            messagebox.showwarning("运行中", "请等待当前任务完成。")
            return
        if not is_login_file_present():
            if not messagebox.askyesno(
                "未登录",
                "未检测到微博登录态，采集可能失败。是否仍要继续？",
            ):
                return
        try:
            require_api_key()
        except RuntimeError as e:
            messagebox.showerror("配置", str(e))
            self.tabs.set("设置")
            return

        keyword = self.entry_keyword.get().strip()
        if not keyword:
            messagebox.showerror("参数", "请填写搜索关键词。")
            return

        ctx = PipelineContext.from_gui(
            keyword,
            self.entry_start.get().strip(),
            self.entry_end.get().strip(),
            int(self.entry_count.get().strip() or "80"),
            bool(self.chk_efficient.get()),
        )

        self.log_box.delete("1.0", "end")
        self.progress.set(0)
        self.label_progress_pct.configure(text="0%")
        self.label_step.configure(text="当前步骤：准备启动…")
        self._set_running_ui(True)

        def on_log(line: str) -> None:
            self._run_log_bridge.write(line)

        def on_step_start(idx: int, _name: str, label: str) -> None:
            pct = idx / STEP_COUNT
            self.after(0, lambda: self._update_progress(pct, label))

        def on_step_done(idx: int, _name: str, label: str) -> None:
            pct = (idx + 1) / STEP_COUNT
            self.after(0, lambda: self._update_progress(pct, f"已完成：{label}"))

        def on_complete(report_path: Path) -> None:
            self._last_report = report_path
            self.after(0, lambda: self._on_pipeline_complete(report_path))

        def on_error(err: Exception) -> None:
            self.after(0, lambda: self._on_pipeline_error(err))

        self._runner.run_async(
            ctx,
            on_log=on_log,
            on_step_start=on_step_start,
            on_step_done=on_step_done,
            on_complete=on_complete,
            on_error=on_error,
            auto_open_report=True,
        )

    def _update_progress(self, ratio: float, step_text: str) -> None:
        self.progress.set(min(1.0, max(0.0, ratio)))
        self.label_progress_pct.configure(text=f"{int(ratio * 100)}%")
        self.label_step.configure(text=f"当前步骤：{step_text}")

    def _on_pipeline_complete(self, report_path: Path) -> None:
        self._set_running_ui(False)
        self.progress.set(1.0)
        self.label_progress_pct.configure(text="100%")
        self.label_step.configure(text="当前步骤：全部完成")
        self._run_log_bridge.write(f"报告已生成: {report_path}")
        messagebox.showinfo("完成", f"监测完成。\n报告：{report_path.name}")

    def _on_pipeline_error(self, err: Exception) -> None:
        self._set_running_ui(False)
        self.label_step.configure(text="当前步骤：失败")
        self._run_log_bridge.write(f"错误: {err}")
        messagebox.showerror("失败", str(err))

    def _on_open_latest_report(self) -> None:
        keyword = self.entry_keyword.get().strip()
        if not keyword:
            messagebox.showerror("参数", "请先填写关键词。")
            return
        try:
            open_report(find_latest_report(keyword))
        except Exception as e:
            messagebox.showerror("报告", str(e))

    def _on_open_reports_dir(self) -> None:
        import os
        import subprocess

        path = get_reports_dir()
        if sys.platform == "win32":
            os.startfile(path)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)


def run_app() -> None:
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleCP(65001)
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        except Exception:
            pass
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    app = SentinelApp()
    app.mainloop()
