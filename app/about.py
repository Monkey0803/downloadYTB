"""关于对话框（ROADMAP 第 11 项）。

为什么值得做：诊断报告早就有了，但入口只有一个「复制诊断信息」按钮——用户想知道自己用
的是哪一版、yt-dlp 是打包的还是本机更新的、日志在哪，界面里没有任何地方能看。

刻意**不再写一份依赖列表**：报告里的字段是诊断用的唯一真相，另抄一份迟早会漂。所以这里
展示的就是 ``diagnostics.build_report()`` 的原文，配上复制 / 打开日志 / 打开发布页三个动作。
"""

import threading
import tkinter as tk

from . import applog, diagnostics, i18n, theme
from .widgets import PillButton, TFrame, TLabel


def _open_with_system(url_or_path):
    """用系统默认程序打开；延迟导入 gui 是因为 gui 会 import 本模块。"""
    from .gui import open_path

    open_path(url_or_path)


class AboutDialog(tk.Toplevel):
    """版本、依赖、保存位置、日志路径 + 一键复制报告。"""

    WIDTH = 660

    def __init__(self, master, app, probe=None):
        super().__init__(master)
        self.app = app
        self._probe = probe
        self.report = ""
        self.title(i18n.tr("about_title"))
        self.configure(bg=theme.C().BG)
        try:
            self.transient(master.winfo_toplevel())
        except tk.TclError:
            pass

        outer = TFrame(self, bg_role="BG")
        outer.pack(fill="both", expand=True, padx=16, pady=14)
        TLabel(outer, text=i18n.tr("about_title"), font=app.f_section,
               bg_role="BG").pack(anchor="w")
        TLabel(outer, text=i18n.tr("about_subtitle"), font=app.f_sub,
               bg_role="BG", fg_role="TEXT_SUB").pack(anchor="w", pady=(2, 8))

        # 报告是纯文本：用可换行的标签展示（与状态详情弹窗一致），按钮负责整段复制
        self.body_label = TLabel(outer, text=i18n.tr("about_collecting"),
                                 font=app.f_sub, bg_role="BG", fg_role="TEXT",
                                 justify="left", anchor="w",
                                 wraplength=self.WIDTH - 70)
        self.body_label.pack(anchor="w", fill="x")

        self.hint_label = TLabel(outer, text="", font=app.f_small, bg_role="BG",
                                 fg_role="TEXT_SUB")
        self.hint_label.pack(anchor="w", pady=(6, 0))

        buttons = TFrame(outer, bg_role="BG")
        buttons.pack(fill="x", pady=(8, 0))
        self.copy_btn = PillButton(buttons, i18n.tr("copy_diagnostics"),
                                   command=self._copy, font=app.f_btn, width=148,
                                   bg_role="BG")
        self.copy_btn.pack(side="left")
        PillButton(buttons, i18n.tr("open_log"), command=self._open_log,
                   font=app.f_btn, width=110, bg_role="BG").pack(
            side="left", padx=(8, 0))
        PillButton(buttons, i18n.tr("update_open_page"),
                   command=self._open_release, font=app.f_btn, width=116,
                   bg_role="BG").pack(side="left", padx=(8, 0))
        PillButton(buttons, i18n.tr("close"), command=self.destroy, primary=True,
                   font=app.f_btn, width=90, bg_role="BG").pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._center_on(master)
        self._collect()

    def _center_on(self, master):
        try:
            self.update_idletasks()
            x = master.winfo_rootx() + max(20, (master.winfo_width() - self.WIDTH) // 2)
            self.geometry(f"{self.WIDTH}+{max(0, x)}+100")
        except tk.TclError:
            pass

    def _collect(self):
        """报告要探一次浏览器 Cookie（可能到秒级），因此放后台线程。"""
        def work():
            try:
                report = diagnostics.build_report(self.app.settings, probe=self._probe)
                error = ""
            except Exception as exc:
                report, error = "", str(exc)[:200]
            try:
                self.after(0, lambda: self._fill(report, error))
            except (RuntimeError, tk.TclError):
                pass          # 窗口已关闭或没有 mainloop，结果直接丢弃

        threading.Thread(target=work, daemon=True).start()

    def _fill(self, report, error):
        if not self.winfo_exists():
            return
        if error:
            self.body_label.configure(
                text=i18n.tr("diagnostics_failed", message=error))
            return
        self.report = report
        self.body_label.configure(text=report)

    def _copy(self):
        if not self.report:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(self.report)
        except tk.TclError:
            return
        self.hint_label.configure(text=i18n.tr("copied"))

    def _open_log(self):
        _open_with_system(applog.log_path())

    def _open_release(self):
        from . import appupdate

        _open_with_system(getattr(self.app, "_release_url", "")
                          or appupdate.RELEASES_PAGE)


def show(app, probe=None):
    """显示（或抬起）关于对话框；每个 App 只保留一个实例。"""
    existing = getattr(app, "_about_dialog", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                existing.focus_set()
                return existing
        except tk.TclError:
            pass
    dialog = AboutDialog(app, app, probe=probe)
    app._about_dialog = dialog
    return dialog
