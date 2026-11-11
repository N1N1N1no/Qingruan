"""Chinese Tk interface for the existing RISC-V / PE engineering tasks."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from .backend import Controller
from .diagnostics import extract_log_file_issues, extract_log_issues, system_snapshot


_STATUS_NAMES = {
    "PASS": "通过", "SUCCESS": "完成", "COMPLETED": "完成", "DONE": "完成",
    "FAIL": "未通过", "FAILED": "未通过", "ERROR": "出错", "RUNNING": "运行中",
    "CANCELLED": "已停止", "CANCELED": "已停止", "STOPPED": "已停止",
    "INCOMPLETE": "尚未完成", "PASS_WITH_LIMITATION": "有限通过", "UNKNOWN": "待查看",
}
_OK_STATUSES = {"PASS", "SUCCESS", "COMPLETED", "DONE"}
_BAD_STATUSES = {"FAIL", "FAILED", "ERROR"}


def _get(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _status_name(value: Any) -> str:
    text = str(value or "UNKNOWN").upper()
    return _STATUS_NAMES.get(text, text)


class ContestApp(tk.Tk):
    """Keep all Tk operations on the main thread; task work stays in Controller."""

    def __init__(self, project_root: str | Path | None = None, native_root: str | Path | None = None) -> None:
        super().__init__()
        # The VM advertises a very high physical DPI; use a readable desktop
        # scale so task details, buttons, reports and progress all fit at once.
        self.tk.call("tk", "scaling", 4 / 3)
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.title("RISC-V 与 PE 工程助手")
        self.geometry("1180x820")
        self.minsize(960, 680)
        self.configure(background="#edf2f7")
        self._events: queue.Queue[Any] = queue.Queue()
        if native_root:
            from .native import NativeController
            self.controller = NativeController(self.project_root, self._events.put, native_root)
            self.title("RISC-V 与 PE 工程助手 · 完整新工程 · Swin-Tiny")
        else:
            self.controller = Controller(self.project_root, self._events.put)
        self.tasks = {_get(task, "id"): task for task in self.controller.tasks}
        self._pending: list[str] = []
        self._current_task: str | None = None
        self._latest_path: Path | None = None
        self._history: dict[str, dict[str, Any]] = {}
        self._report_files: dict[str, Path] = {}
        self._closed = False
        self._closing = False
        self._scan_in_progress = False
        self._selection_reentrant = False
        self._follow_log = tk.BooleanVar(value=True)
        self._status_text = tk.StringVar(value="准备就绪。先在左侧选择要做的步骤。")
        self._queue_text = tk.StringVar(value="未安排后续步骤")
        self._result_text = tk.StringVar(value="尚未选择结果")
        self._report_choice = tk.StringVar()
        self._task_title = tk.StringVar(value="选择一个步骤")
        self._task_meta = tk.StringVar(value="从左边选择任务，可以先查看说明，再点击运行。")
        self._build_style()
        self._build_layout()
        self._populate_tasks()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(80, self._pump_events)
        self.after(120, self.refresh_history)

    def _build_style(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        families = set(tkfont.families(self))
        self._ui_family = next((family for family in ("Noto Sans CJK SC", "WenQuanYi Micro Hei", "Noto Sans CJK TC", "song ti", "fangsong ti", "DejaVu Sans") if family in families), "TkDefaultFont")
        self._mono_family = "DejaVu Sans Mono" if "DejaVu Sans Mono" in families else self._ui_family
        # Some older Xft/Tk builds apply Xft.dpi a second time even to pixel
        # fonts. Measure full-width CJK glyphs to keep controls at their intended
        # size without changing the desktop's global DPI settings.
        probe = tkfont.Font(self, family=self._ui_family, size=-14)
        self._font_scale = min(1.0, 56 / max(1, probe.measure("运行结果")))
        default = self._font(14)
        # A wildcard option-database font overrides ttk's title styles as
        # well, making every heading the body size. Configure the ttk and
        # named defaults instead so headings retain their own sizes.
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family=self._ui_family, size=self._font(14)[1])
        style.configure(".", font=default)
        style.configure("TFrame", background="#edf2f7")
        style.configure("Card.TFrame", background="#ffffff")
        style.configure("TLabel", background="#edf2f7", foreground="#203246", font=default)
        style.configure("Card.TLabel", background="#ffffff")
        style.configure("Muted.TLabel", foreground="#587084")
        style.configure("Title.TLabel", background="#ffffff", foreground="#102e49", font=self._font(27, bold=True))
        style.configure("TaskTitle.TLabel", background="#ffffff", foreground="#102e49", font=self._font(20, bold=True))
        style.configure("TButton", padding=(10, 7), font=default)
        style.configure("Primary.TButton", background="#2463a6", foreground="#ffffff", padding=(15, 9), font=self._font(14, bold=True))
        style.map("Primary.TButton", background=[("active", "#174b85"), ("disabled", "#c1d1df")], foreground=[("disabled", "#eef3f8")])
        style.configure("Danger.TButton", foreground="#a53436")
        style.configure("Treeview", rowheight=31, fieldbackground="#ffffff", background="#ffffff", foreground="#203246", borderwidth=0, font=default)
        style.configure("Treeview.Heading", font=self._font(14, bold=True), padding=(6, 8))
        style.map("Treeview", background=[("selected", "#dbeaf9")], foreground=[("selected", "#12375b")])
        style.configure("TNotebook", background="#edf2f7", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(16, 8), font=default)
        style.configure("Success.TLabel", foreground="#167047")
        style.configure("Failure.TLabel", foreground="#b3303d")
        style.configure("Limited.TLabel", foreground="#9a640b")

    def _font(self, pixels: int, *, bold: bool = False, mono: bool = False) -> tuple:
        family = self._mono_family if mono else self._ui_family
        size = -max(1, round(pixels * self._font_scale))
        return (family, size, "bold") if bold else (family, size)

    def _build_layout(self) -> None:
        header = ttk.Frame(self, style="Card.TFrame", padding=(22, 17))
        header.pack(fill="x")
        title_row = ttk.Frame(header, style="Card.TFrame")
        title_row.pack(fill="x")
        ttk.Label(title_row, text="RISC-V 与 PE 工程助手", style="Title.TLabel").pack(side="left")
        ttk.Button(title_row, text="工具与运行检测", command=self._diagnose).pack(side="right")
        ttk.Button(title_row, text="打开学习文档", command=self._open_learning_pdf).pack(side="right", padx=(0, 8))
        ttk.Label(header, text="选步骤 → 点击运行 → 查看本次结果", style="Card.TLabel").pack(anchor="w", pady=(6, 0))
        path_row = ttk.Frame(self, padding=(20, 9))
        path_row.pack(fill="x")
        ttk.Label(path_row, text="工程：", style="Muted.TLabel").pack(side="left")
        ttk.Label(path_row, text=str(self.project_root), style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(path_row, text="打开工程目录", command=lambda: self._open_path(self.project_root)).pack(side="right")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        left = ttk.Frame(panes, style="Card.TFrame", padding=10)
        right = ttk.Frame(panes)
        panes.add(left, weight=0)
        panes.add(right, weight=1)
        ttk.Label(left, text="工程步骤", style="TaskTitle.TLabel").pack(anchor="w", pady=(3, 8))
        tree_frame = ttk.Frame(left, style="Card.TFrame")
        tree_frame.pack(fill="both", expand=True)
        self.task_tree = ttk.Treeview(tree_frame, show="tree", selectmode="extended", height=17)
        self.task_tree.column("#0", width=260, minwidth=215)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.task_tree.yview)
        self.task_tree.configure(yscrollcommand=tree_scroll.set)
        self.task_tree.pack(side="left", fill="both", expand=True)
        tree_scroll.pack(side="right", fill="y")
        self.task_tree.bind("<<TreeviewSelect>>", self._on_task_selected)
        self.task_tree.bind("<Double-1>", self._on_task_double_click)
        ttk.Label(left, text="按住 Ctrl 可选择多个步骤。\n多个步骤按列表顺序依次执行。", style="Card.TLabel", justify="left").pack(side="bottom", anchor="w", pady=(10, 4), before=tree_frame)

        task_card = ttk.Frame(right, style="Card.TFrame", padding=(18, 15))
        task_card.pack(fill="x", padx=(12, 0))
        ttk.Label(task_card, textvariable=self._task_title, style="TaskTitle.TLabel").pack(anchor="w")
        ttk.Label(task_card, textvariable=self._task_meta, style="Card.TLabel", wraplength=760).pack(anchor="w", pady=(6, 7))
        self.detail_text = ScrolledText(task_card, height=5, wrap="word", relief="flat", borderwidth=0,
                                       background="#ffffff", foreground="#354e65", font=self._font(14), padx=0, pady=2)
        self.detail_text.pack(fill="x")
        self.detail_text.configure(state="disabled")
        actions = ttk.Frame(task_card, style="Card.TFrame")
        actions.pack(fill="x", pady=(10, 0))
        self.run_button = ttk.Button(actions, text="运行这一步", style="Primary.TButton", command=self._run_selected)
        self.run_button.pack(side="left")
        self.queue_button = ttk.Button(actions, text="依次运行选中步骤", command=self._queue_selected)
        self.queue_button.pack(side="left", padx=8)
        self.stop_button = ttk.Button(actions, text="停止当前任务", style="Danger.TButton", command=self._stop, state="disabled")
        self.stop_button.pack(side="right")
        queue_row = ttk.Frame(right, padding=(15, 8, 0, 6))
        queue_row.pack(fill="x")
        ttk.Label(queue_row, textvariable=self._queue_text, style="Muted.TLabel").pack(side="left", fill="x", expand=True)
        ttk.Button(queue_row, text="清空待运行", command=self._clear_queue).pack(side="right")
        ttk.Button(queue_row, text="一键 RTL 基础流程", command=self._queue_rtl).pack(side="right", padx=(0, 8))

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True, padx=(12, 0))
        log_tab = ttk.Frame(self.notebook, padding=8)
        summary_tab = ttk.Frame(self.notebook, padding=10)
        history_tab = ttk.Frame(self.notebook, padding=8)
        help_tab = ttk.Frame(self.notebook, padding=12)
        self.notebook.add(log_tab, text="实时日志")
        self.notebook.add(summary_tab, text="结果与报告")
        self.notebook.add(history_tab, text="历史结果")
        self.notebook.add(help_tab, text="怎么用")

        log_tools = ttk.Frame(log_tab)
        log_tools.pack(fill="x", pady=(0, 6))
        ttk.Checkbutton(log_tools, text="跟随最新日志", variable=self._follow_log).pack(side="left")
        ttk.Button(log_tools, text="清空窗口日志", command=self._clear_log).pack(side="right")
        ttk.Button(log_tools, text="查看错误与警告", command=self._show_log_issues).pack(side="right", padx=(0, 8))
        self.log_text = ScrolledText(log_tab, wrap="word", background="#142536", foreground="#e2edf6",
                                    insertbackground="#ffffff", relief="flat", font=self._font(14, mono=True), padx=12, pady=10)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")
        self.log_text.tag_configure("error", foreground="#ffabb3")
        self.log_text.tag_configure("success", foreground="#8de2b5")
        self.log_text.tag_configure("warning", foreground="#ffd382")
        self.log_text.tag_configure("heading", foreground="#9bcaff")

        result_header = ttk.Frame(summary_tab)
        result_header.pack(fill="x", pady=(0, 8))
        self.result_status = ttk.Label(result_header, text="尚无本次运行结果", font=self._font(17, bold=True))
        self.result_status.pack(side="left")
        self.result_buttons = ttk.Frame(summary_tab)
        self.result_buttons.pack(fill="x", pady=(0, 6))
        ttk.Button(self.result_buttons, text="打开结果目录", command=self._open_latest_directory).pack(side="left")
        ttk.Button(self.result_buttons, text="打开说明／汇总", command=self._open_latest_summary).pack(side="left", padx=6)
        ttk.Button(self.result_buttons, text="查看日志文件", command=self._open_latest_log).pack(side="left")
        ttk.Button(self.result_buttons, text="打开波形", command=self._open_wave).pack(side="left", padx=6)
        ttk.Label(summary_tab, textvariable=self._result_text, style="Muted.TLabel", wraplength=750).pack(anchor="w", pady=(0, 8))
        result_panes = ttk.Panedwindow(summary_tab, orient="vertical")
        result_panes.pack(fill="both", expand=True)
        summary_frame = ttk.Frame(result_panes)
        report_frame = ttk.Frame(result_panes)
        result_panes.add(summary_frame, weight=1)
        result_panes.add(report_frame, weight=2)
        self.summary_text = ScrolledText(summary_frame, height=7, wrap="word", relief="flat", background="#ffffff",
                                        foreground="#29445c", font=self._font(14), padx=10, pady=8)
        self.summary_text.pack(fill="both", expand=True)
        self.summary_text.configure(state="disabled")
        report_tools = ttk.Frame(report_frame)
        report_tools.pack(fill="x", pady=(8, 5))
        ttk.Label(report_tools, text="本次报告：").pack(side="left")
        self.report_combo = ttk.Combobox(report_tools, textvariable=self._report_choice, state="readonly")
        self.report_combo.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.report_combo.bind("<<ComboboxSelected>>", self._preview_report)
        ttk.Button(report_tools, text="打开所选报告", command=self._open_selected_report).pack(side="right")
        ttk.Button(report_tools, text="筛选完整文件", command=self._scan_selected_report).pack(side="right", padx=(0, 6))
        self.report_text = ScrolledText(report_frame, height=9, wrap="none", relief="flat", background="#ffffff",
                                       foreground="#29445c", font=self._font(14, mono=True), padx=10, pady=8)
        self.report_text.pack(fill="both", expand=True)
        self.report_text.configure(state="disabled")

        history_tools = ttk.Frame(history_tab)
        history_tools.pack(fill="x", pady=(0, 6))
        ttk.Label(history_tools, text="每次执行的结果分别保存，失败也会保留。", style="Muted.TLabel").pack(side="left")
        self.history_refresh = ttk.Button(history_tools, text="刷新", command=self.refresh_history)
        self.history_refresh.pack(side="right")
        columns = ("time", "title", "status", "path")
        history_frame = ttk.Frame(history_tab)
        history_frame.pack(fill="both", expand=True)
        self.history_tree = ttk.Treeview(history_frame, columns=columns, show="headings", selectmode="browse")
        for name, title, width in [("time", "时间", 130), ("title", "步骤／结果", 190), ("status", "状态", 80), ("path", "结果目录", 220)]:
            self.history_tree.heading(name, text=title)
            self.history_tree.column(name, width=width, minwidth=65, stretch=name in {"title", "path"})
        self.history_tree.tag_configure("pass", foreground="#167047")
        self.history_tree.tag_configure("fail", foreground="#b3303d")
        self.history_tree.tag_configure("other", foreground="#805e17")
        hist_scroll = ttk.Scrollbar(history_frame, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=hist_scroll.set)
        self.history_tree.pack(side="left", fill="both", expand=True)
        hist_scroll.pack(side="right", fill="y")
        self.history_tree.bind("<Double-1>", self._show_history_result)
        history_actions = ttk.Frame(history_tab)
        history_actions.pack(fill="x", pady=(6, 0))
        ttk.Button(history_actions, text="查看选中结果", command=self._show_history_result).pack(side="left")
        ttk.Button(history_actions, text="打开选中目录", command=self._open_history_directory).pack(side="left", padx=7)

        help_text = (
            "第一次使用\n\n"
            "1. 点击右上角“工具与运行检测”，查看工具是否就绪，以及内存、磁盘和工具进程。\n"
            "2. 在左侧选择一个步骤。上方会解释它做什么、需要什么、结果保存在哪里。\n"
            "3. 点击“运行这一步”。程序会自动加载工具环境并执行已有脚本。\n"
            "4. 在“实时日志”里看当前进度，在“结果与报告”里看通过或失败原因。\n\n"
            "需要连续执行时\n\n"
            "按住 Ctrl 选择多个步骤，再点击“依次运行选中步骤”。步骤按左侧列表顺序执行。"
            "只有前一步成功，才会开始下一步；遇到失败会停下并保留日志。"
            "“一键 RTL 基础流程”会依次准备工程、编译软件、检查存储器、检查 PE，并运行整机 RTL。\n\n"
            "怎么看结果\n\n"
            "“通过”只对应那一次运行及该步骤的检查范围。综合完成、功能正确、SDF 时序验证是不同的检查。"
            "本机版会标明未随备份提供的工程脚本；补齐对应版本工程后重启即可使用。"
            "通过与否以本机实际运行的汇总和日志为准。\n\n"
            "任务较久时\n\n"
            "综合可能需要十几分钟。进度条表示任务仍在运行，不代表完成百分比。"
            "点击“停止当前任务”会停止由本界面启动的任务，并取消后续队列。"
            "已有的其它终端和交互 DC 会话不由这个按钮管理。"
            "可用“工具与运行检测”查看资源占用，用“查看错误与警告”筛选当前日志。"
        )
        help_area = ScrolledText(help_tab, wrap="word", background="#ffffff", foreground="#29445c",
                                 relief="flat", font=self._font(15), padx=16, pady=14)
        help_area.pack(fill="both", expand=True)
        help_area.insert("1.0", help_text)
        help_area.configure(state="disabled")

        footer = ttk.Frame(self, padding=(20, 6, 20, 12))
        footer.pack(side="bottom", fill="x", before=panes)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=130)
        self.progress.pack(side="right", padx=(15, 0))
        ttk.Label(footer, textvariable=self._status_text, wraplength=920).pack(side="left", fill="x", expand=True)

    def _populate_tasks(self) -> None:
        groups: dict[str, str] = {}
        first: str | None = None
        for task_id, task in self.tasks.items():
            group = str(_get(task, "group", "其它"))
            if group not in groups:
                node = f"group:{len(groups)}"
                self.task_tree.insert("", "end", iid=node, text=group, open=True)
                groups[group] = node
            node = f"task:{task_id}"
            unavailable = self.controller.task_unavailable(task)
            self.task_tree.insert(groups[group], "end", iid=node,
                                  text=str(_get(task, "title", task_id)) + ("（缺少脚本）" if unavailable else ""),
                                  tags=("unavailable",) if unavailable else ())
            if first is None:
                first = node
        if first:
            self.task_tree.selection_set(first)
            self.task_tree.focus(first)
        self.task_tree.tag_configure("unavailable", foreground="#8b6670")

    def _selected_tasks(self) -> list[str]:
        selected = set(self.task_tree.selection())
        return [task_id for task_id in self.tasks if f"task:{task_id}" in selected]

    def _on_task_selected(self, event: Any = None) -> None:
        selected = self._selected_tasks()
        if not selected:
            return
        focused = self.task_tree.focus()
        task_id = focused[5:] if focused.startswith("task:") and focused[5:] in selected else selected[0]
        task = self.tasks[task_id]
        self._task_title.set(str(_get(task, "title", task_id)))
        estimated = _get(task, "estimated", "")
        meta = str(_get(task, "group", ""))
        if estimated:
            meta += "  ·  " + str(estimated)
        self._task_meta.set(meta)
        parts = [str(_get(task, "description", ""))]
        unavailable = self.controller.task_unavailable(task)
        self.run_button.configure(state="disabled" if unavailable or self._closing else "normal")
        if unavailable:
            parts += ["", unavailable]
        prerequisites = _get(task, "prerequisites", ()) or ()
        outputs = _get(task, "outputs", ()) or ()
        if prerequisites:
            parts += ["", "开始前需要："] + ["• " + str(_get(self.tasks.get(item), "title", item)) for item in prerequisites]
        output_paths = []
        if outputs:
            parts += ["", "完成后查看（可点击打开）："]
            for item in outputs:
                path = Path(item)
                if not path.is_absolute():
                    path = self.controller.project / path
                output_paths.append(path)
                parts.append("• " + str(path))
        self._replace_text(self.detail_text, "\n".join(parts))
        for index, path in enumerate(output_paths):
            tag = f"output:{index}"
            start = self.detail_text.search(str(path), "1.0", stopindex="end", regexp=False)
            if start:
                self.detail_text.tag_add(tag, start, f"{start}+{len(str(path))}c")
                self.detail_text.tag_configure(tag, foreground="#2463a6", underline=True)
                self.detail_text.tag_bind(tag, "<Button-1>", lambda event, p=path: self._open_output(p))
                self.detail_text.tag_bind(tag, "<Enter>", lambda event: self.detail_text.configure(cursor="hand2"))
                self.detail_text.tag_bind(tag, "<Leave>", lambda event: self.detail_text.configure(cursor="xterm"))

    def _open_output(self, path: Path) -> None:
        if any(char in path.name for char in "*?["):
            candidates = list(path.parent.glob(path.name))
            if candidates:
                self._open_path(max(candidates, key=lambda item: item.stat().st_mtime))
            else:
                self._open_path(path.parent)
        elif path.exists():
            self._open_path(path)
        else:
            messagebox.showinfo("尚无结果", "还没有生成这个结果，请先运行对应步骤。\n\n" + str(path), parent=self)

    def _on_task_double_click(self, event: Any = None) -> None:
        # Expanding a group or inspecting a task must not accidentally launch
        # an expensive synthesis. The explicit run buttons own execution.
        self._on_task_selected()

    def _run_selected(self) -> None:
        selected = self._selected_tasks()
        if not selected:
            messagebox.showinfo("选择步骤", "请先在左侧选择一个具体步骤。", parent=self)
            return
        focused = self.task_tree.focus()
        task_id = focused[5:] if focused.startswith("task:") and focused[5:] in selected else selected[0]
        self._enqueue([task_id])

    def _queue_selected(self) -> None:
        selected = self._selected_tasks()
        if not selected:
            messagebox.showinfo("选择步骤", "请先选中需要依次运行的步骤。", parent=self)
            return
        self._enqueue(selected)

    def _queue_rtl(self) -> None:
        ids = [name for name in ("prepare", "software", "ram_test", "pe_test", "rtl_run") if name in self.tasks]
        if len(ids) != 5:
            self._append_log("RTL 基础流程中的步骤尚未完整配置，请从左侧选择对应任务。\n", "warning")
            return
        self._enqueue(ids)

    def _enqueue(self, ids: list[str]) -> None:
        missing = [self.controller.task_unavailable(self.tasks[task_id]) for task_id in ids]
        if any(missing):
            messagebox.showinfo("缺少工程脚本", "\n\n".join(dict.fromkeys(item for item in missing if item)), parent=self)
            return
        for task_id in ids:
            if task_id not in self._pending and task_id != self._current_task:
                self._pending.append(task_id)
        self._update_queue_text()
        self._start_next()

    def _start_next(self) -> None:
        if self._closed or self._closing or self.controller.is_running or self._current_task is not None or not self._pending:
            return
        task_id = self._pending.pop(0)
        self._current_task = task_id
        self._update_queue_text()
        title = str(_get(self.tasks[task_id], "title", task_id))
        self._append_log(f"\n{'─' * 24}\n开始：{title}\n", "heading")
        self.notebook.select(0)
        self._set_running(True)
        self._status_text.set("正在启动：" + title)
        try:
            started = self.controller.start(task_id)
        except Exception as error:
            started = False
            self._append_log("启动失败：" + str(error) + "\n", "error")
        if started is False:
            self._current_task = None
            self._pending.clear()
            self._set_running(False)
            self._update_queue_text()
            self._status_text.set("任务未能启动，请查看日志。")

    def _stop(self) -> None:
        self._pending.clear()
        self._update_queue_text()
        self._status_text.set("正在停止当前任务，等待进程退出……")
        self.stop_button.configure(state="disabled")
        threading.Thread(target=self.controller.stop, daemon=True).start()

    def _clear_queue(self) -> None:
        self._pending.clear()
        self._update_queue_text()

    def _update_queue_text(self) -> None:
        if not self._pending:
            self._queue_text.set("未安排后续步骤")
            return
        titles = [str(_get(self.tasks[name], "title", name)) for name in self._pending]
        label = f"等待 {len(titles)} 步：" + " → ".join(titles[:3])
        if len(titles) > 3:
            label += " ……"
        self._queue_text.set(label)

    def _set_running(self, running: bool) -> None:
        self.stop_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(12)
        else:
            self.progress.stop()

    def _pump_events(self) -> None:
        if self._closed:
            return
        for _ in range(350):
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            try:
                self._handle_event(event)
            except Exception as error:
                self._append_log("界面更新异常：" + str(error) + "\n", "error")
        self.after(80, self._pump_events)

    def _handle_event(self, event: Any) -> None:
        kind = _get(event, "type", "log")
        text = str(_get(event, "text", "") or "")
        if kind == "log":
            self._append_log(text)
        elif kind == "started":
            task_id = _get(event, "task_id", self._current_task)
            self._current_task = task_id
            self._set_running(True)
            title = str(_get(self.tasks.get(task_id), "title", task_id or "任务"))
            self._status_text.set("运行中：" + title)
            if text:
                self._append_log(text + ("" if text.endswith("\n") else "\n"), "heading")
            path = _get(event, "path")
            if path:
                self._set_result(path, _get(event, "summary"), "RUNNING")
        elif kind == "result":
            path = _get(event, "path")
            if path:
                self._set_result(path, _get(event, "summary"), _get(event, "status"))
        elif kind == "state":
            if text:
                self._status_text.set(text)
        elif kind == "finished":
            self._finish_event(event)
        elif kind == "history":
            self._scan_in_progress = False
            self.history_refresh.configure(state="normal")
            self._populate_history(_get(event, "results", []) or [])
        elif kind == "diagnosis":
            self._replace_text(self.summary_text, text)
            self.result_status.configure(text="工具与环境检测", style="TLabel")
            self.notebook.select(1)
            self._append_log("工具检测完成，详情见“结果与报告”。\n", "heading")
        elif kind == "report_scan":
            if str(self._report_files.get(self._report_choice.get())) == _get(event, "path"):
                self._replace_text(self.report_text, text)
        elif kind == "ui_error":
            self._scan_in_progress = False
            self.history_refresh.configure(state="normal")
            self._append_log(text + "\n", "error")

    def _finish_event(self, event: Any) -> None:
        status = str(_get(event, "status", "UNKNOWN") or "UNKNOWN").upper()
        returncode = _get(event, "returncode")
        summary = _get(event, "summary")
        if not isinstance(summary, dict) and _get(event, "path"):
            summary = self._read_summary(Path(_get(event, "path")))
        saved_status = str((summary or {}).get("status", "")).upper()
        if saved_status in _BAD_STATUSES or saved_status in {"INCOMPLETE", "PASS_WITH_LIMITATION"}:
            status = saved_status
        task = self.tasks.get(self._current_task)
        title = str(_get(task, "title", self._current_task or "任务"))
        path = _get(event, "path")
        display_summary = summary
        result_directories = (summary or {}).get("result_directories", [])
        if isinstance(result_directories, list):
            for candidate in reversed(result_directories):
                actual = self._read_summary(Path(candidate))
                if actual:
                    path, display_summary = candidate, actual
                    actual_status = str(actual.get("status", "")).upper()
                    if actual_status in _BAD_STATUSES or actual_status in {"INCOMPLETE", "PASS_WITH_LIMITATION"}:
                        status = actual_status
                    break
        if _get(task, "kind") == "report" and self._latest_path:
            path = self._latest_path
            display_summary = self._read_summary(self._latest_path)
        succeeded = returncode == 0 and status in _OK_STATUSES
        if path:
            self._set_result(path, display_summary, status)
        text = str(_get(event, "text", "") or "")
        self._append_log((text + "\n") if text else f"{title}：{_status_name(status)}\n", "success" if succeeded else "error")
        self._current_task = None
        self._set_running(False)
        if succeeded:
            self._status_text.set(title + "已完成。")
        else:
            self._pending.clear()
            self._status_text.set(title + "：" + _status_name(status) + "。后续队列已暂停，请查看结果和日志。")
        self._update_queue_text()
        self.refresh_history()
        if succeeded and self._pending and not self._closing:
            self.after(250, self._start_next)

    def _append_log(self, text: str, tag: str | None = None) -> None:
        if not text:
            return
        if tag is None:
            upper = text.upper()
            if any(token in upper for token in ("ERROR", "FAILED", "FATAL", "_FAIL", "未通过")):
                tag = "error"
            elif "WARNING" in upper or "WARN-" in upper:
                tag = "warning"
            elif "_PASS" in upper or upper.strip() == "PASS":
                tag = "success"
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text, (tag,) if tag else ())
        line_count = int(self.log_text.index("end-1c").split(".")[0])
        if line_count > 14000:
            self.log_text.delete("1.0", f"{line_count - 10000}.0")
        self.log_text.configure(state="disabled")
        if self._follow_log.get():
            self.log_text.see("end")

    def _clear_log(self) -> None:
        self._replace_text(self.log_text, "")

    def _show_log_issues(self) -> None:
        chooser = tk.Toplevel(self)
        chooser.title("日志中的错误与警告")
        chooser.transient(self)
        chooser.geometry("900x500")
        area = ScrolledText(chooser, wrap="word", font=self._font(14, mono=True),
                            background="#ffffff", padx=12, pady=12)
        area.pack(fill="both", expand=True)
        area.insert("1.0", extract_log_issues(self.log_text.get("1.0", "end-1c")))
        area.configure(state="disabled")

    def _scan_selected_report(self) -> None:
        path = self._report_files.get(self._report_choice.get())
        if path is None:
            messagebox.showinfo("选择文件", "请先在“本次报告”中选择一个日志或报告。", parent=self)
            return
        self._replace_text(self.report_text, "正在逐行筛选完整文件……")
        def scan() -> None:
            self._events.put({"type": "report_scan", "path": str(path),
                              "text": extract_log_file_issues(path)})
        threading.Thread(target=scan, daemon=True).start()

    @staticmethod
    def _replace_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _set_result(self, path: str | Path, summary: Any = None, status: Any = None) -> None:
        result_path = Path(path)
        if result_path.name in {"summary.json", "validation_summary.json", "run.json"}:
            result_path = result_path.parent
        self._latest_path = result_path
        self._result_text.set(str(result_path))
        if not isinstance(summary, dict):
            summary = self._read_summary(result_path)
        # The actual saved summary takes precedence over optimistic process
        # completion labels. In particular, a strict SDF FAIL stays red.
        actual_status = str((summary or {}).get("status") or status or "UNKNOWN").upper()
        style = "Success.TLabel" if actual_status in _OK_STATUSES else "Failure.TLabel" if actual_status in _BAD_STATUSES else "Limited.TLabel"
        self.result_status.configure(text="本次结果：" + _status_name(actual_status), style=style)
        paragraphs = ["结果目录：" + str(result_path), "状态：" + _status_name(actual_status)]
        if summary:
            accuracy = summary.get("accuracy")
            if isinstance(accuracy, dict) and accuracy.get("status") == "NOT_PASS":
                self.result_status.configure(text="调度检查通过；模型精度未通过", style="Limited.TLabel")
                paragraphs += ["", "模型精度尚未通过：",
                               "C 与 ONNX Runtime Top-1 一致：%s/%s" % (accuracy.get("C_vs_ORT_top1_matches"), accuracy.get("samples")),
                               "C 与公开参考 Top-1 一致：%s/%s" % (accuracy.get("C_vs_official_top1_matches"), accuracy.get("samples")),
                               "完整模型尚未完成 RISC-V / PE RTL 端到端验证。"]
            error = summary.get("error")
            if error:
                paragraphs += ["", "未通过的原因：", str(error)]
            if "NIOD" in str(error) or summary.get("unsupported_negative_iopath"):
                paragraphs += ["", "说明：旧版 VCS 将一条 −0.781 ps 的单元延迟替换为 0。程序计算正确，仍不足以认定完整 SDF 时序验证通过。"]
            for key, label in (("scope", "检查范围"), ("source_soc_compile_case", "使用的综合结果"), ("source_soc_sdf_export_case", "使用的 SDF 导出")):
                if summary.get(key):
                    paragraphs += ["", label + "：", str(summary[key])]
            fields = (
                ("clock_period_ns", "时钟周期", " ns"),
                ("external_memory_response_delay_ns", "外部存储响应", " ns"),
                ("pe_config_writes_checked", "已检查 PE 配置写入", " 次"),
                ("pe_configuration_writes_checked", "已检查 PE 配置写入", " 次"),
                ("reference_pe_output_words_checked", "已检查 PE 结果", " 个"),
                ("pe_result_words_checked", "已检查 PE 结果", " 个"),
                ("setup_slack_ns", "建立时间余量", " ns"),
                ("hold_slack_ns", "保持时间余量", " ns"),
                ("cycles", "运行周期", ""),
                ("trace_lines", "访问记录行数", ""),
                ("annotation_iopath_source_count", "SDF 单元路径数", ""),
                ("annotation_iopath_applied_count", "实际加载单元路径数", ""),
                ("executed_nodes", "C 调度节点数", ""),
                ("executed_MAC_jobs", "MAC 作业数", ""),
                ("external_axi_ram_required_bytes", "所需外部 AXI 内存", " 字节"),
            )
            facts = [label + "：" + str(summary[key]) + suffix for key, label, suffix in fields if key in summary]
            for key, label in (("output_traces_match", "RTL 与门级访问记录一致"),
                               ("traces_byte_identical", "RTL 与门级访问记录一致"),
                               ("sdf_annotated", "已加载 SDF"),
                               ("timing_met", "综合时间要求满足")):
                if isinstance(summary.get(key), bool):
                    facts.append(label + "：" + ("是" if summary[key] else "否"))
            if facts:
                paragraphs += ["", "关键结果："] + list(dict.fromkeys(facts))
            cases = summary.get("cases")
            if isinstance(cases, dict):
                cases = [dict(value, case=name) for name, value in cases.items() if isinstance(value, dict)]
            for case in cases if isinstance(cases, list) else []:
                if not isinstance(case, dict):
                    continue
                checks = case.get("checks") or case.get("event_counts") or {}
                if not isinstance(checks, dict):
                    continue
                labels = {"pe_reads": "PE 读取", "pe_writes": "PE 写入", "pe_responses": "写响应",
                          "cycles": "周期", "sram_instances": "SRAM", "results": "结果", "reads": "读取", "writes": "写入"}
                items = [labels[key] + " " + str(checks[key]) for key in labels if key in checks]
                if items:
                    paragraphs += ["", str(case.get("case") or case.get("name") or "检查") + "：" + "，".join(items)]
            paragraphs += ["", "详细面积、setup/hold 和日志可在下方选择报告查看。完整原始汇总可用“打开说明／汇总”打开。"]
        else:
            paragraphs += ["", "此任务的日志和报告保存在上面的目录中。"]
        self._replace_text(self.summary_text, "\n".join(paragraphs))
        self._load_report_list(result_path)

    def _load_report_list(self, path: Path) -> None:
        previous = self._report_choice.get()
        files = []
        for directory in (path, path / "dc", path / "rtl", path / "gate", path / "timing_probe"):
            if not directory.is_dir():
                continue
            candidates = list(directory.glob("*"))
            if directory != path:
                candidates += list(directory.glob("*/*"))
            for candidate in candidates:
                if candidate.is_file() and (candidate.suffix.lower() in {".rpt", ".log", ".json", ".sdc"} or candidate.name in {"RESULTS.md", "sdfAnnotateInfo"}):
                    files.append(candidate)
        self._report_files = {str(file.relative_to(path)): file for file in sorted(set(files))}
        labels = list(self._report_files)
        self.report_combo.configure(values=labels)
        if previous in self._report_files:
            selection = previous
        else:
            preferred = ("RESULTS.md", "summary.json", "validation_summary.json", "dc/area.rpt", "area.rpt", "run.json")
            selection = next((name for name in preferred if name in self._report_files), labels[0] if labels else "")
        self._report_choice.set(selection)
        self._preview_report()

    def _preview_report(self, event: Any = None) -> None:
        path = self._report_files.get(self._report_choice.get())
        if path is None:
            self._replace_text(self.report_text, "这个结果目录暂时没有可预览的报告。")
            return
        try:
            limit = 200 * 1024
            with path.open("rb") as stream:
                raw = stream.read(limit + 1)
            text = raw[:limit].decode("utf-8", errors="replace")
            if len(raw) > limit:
                text += "\n\n—— 文件较大，此处仅显示开头 200 KB。点击“打开所选报告”查看完整文件。——"
            self._replace_text(self.report_text, text)
        except OSError as error:
            self._replace_text(self.report_text, "读取报告失败：" + str(error))

    def _open_selected_report(self) -> None:
        path = self._report_files.get(self._report_choice.get())
        if path:
            self._open_path(path)
        else:
            messagebox.showinfo("选择报告", "请先选择一次运行结果，再从下拉列表选择报告。", parent=self)

    def _read_summary(self, path: Path) -> dict[str, Any] | None:
        for name in ("summary.json", "run.json", "validation_summary.json"):
            candidate = path / name
            try:
                if candidate.is_file() and candidate.stat().st_size <= 8 * 1024 * 1024:
                    content = json.loads(candidate.read_text())
                    if isinstance(content, dict):
                        translate = getattr(self.controller, '_host_paths', lambda value: value)
                        return translate(content)
            except (OSError, ValueError):
                continue
        return None

    def refresh_history(self) -> None:
        if self._scan_in_progress or self._closed:
            return
        self._scan_in_progress = True
        self.history_refresh.configure(state="disabled")
        def scan() -> None:
            try:
                self._events.put({"type": "history", "results": self.controller.scan_results()})
            except Exception as error:
                self._events.put({"type": "ui_error", "text": "读取历史结果失败：" + str(error)})
        threading.Thread(target=scan, daemon=True).start()

    def _populate_history(self, entries: list[Any]) -> None:
        selected_path = None
        selected = self.history_tree.selection()
        if selected and selected[0] in self._history:
            selected_path = self._history[selected[0]].get("path")
        self.history_tree.delete(*self.history_tree.get_children())
        self._history.clear()
        for index, entry in enumerate(entries):
            data = dict(entry) if isinstance(entry, dict) else vars(entry)
            path = data.get("path") or data.get("directory") or data.get("result_dir")
            if not path:
                continue
            data["path"] = str(path)
            status = str(data.get("status") or "UNKNOWN").upper()
            when = data.get("time") or data.get("timestamp")
            if not when and data.get("mtime"):
                when = time.strftime("%m-%d %H:%M", time.localtime(float(data["mtime"])))
            task_id = data.get("task_id")
            title = data.get("title") or _get(self.tasks.get(task_id), "title") or Path(path).name
            node = f"history:{index}"
            tag = "pass" if status in _OK_STATUSES else "fail" if status in _BAD_STATUSES else "other"
            self.history_tree.insert("", "end", iid=node, values=(when or "", title, _status_name(status), str(path)), tags=(tag,))
            self._history[node] = data
            if str(path) == selected_path:
                self.history_tree.selection_set(node)

    def _selected_history(self) -> dict[str, Any] | None:
        selected = self.history_tree.selection()
        if not selected:
            messagebox.showinfo("选择结果", "请先在历史列表中选择一次运行。", parent=self)
            return None
        return self._history.get(selected[0])

    def _show_history_result(self, event: Any = None) -> None:
        entry = self._selected_history()
        if entry:
            self._set_result(entry["path"], entry.get("summary"), entry.get("status"))
            self.notebook.select(1)

    def _open_history_directory(self) -> None:
        entry = self._selected_history()
        if entry:
            self._open_path(Path(entry["path"]))

    def _diagnose(self) -> None:
        self._status_text.set("正在检测工具环境……")
        def diagnose() -> None:
            try:
                result = self.controller.diagnose()
                if isinstance(result, dict):
                    lines = ["工具环境检测", ""]
                    for key, value in result.items():
                        if isinstance(value, dict):
                            lines += [str(key) + "："] + [f"  {k}：{v}" for k, v in value.items()]
                        elif isinstance(value, (list, tuple)):
                            lines += [str(key) + "："] + ["  " + str(item) for item in value]
                        else:
                            lines.append(f"{key}：{value}")
                    text = "\n".join(lines)
                else:
                    text = str(result)
                text += "\n\n" + system_snapshot(self.project_root)
                self._events.put({"type": "diagnosis", "text": text})
                self._events.put({"type": "state", "text": "工具检测完成。"})
            except Exception as error:
                self._events.put({"type": "ui_error", "text": "工具检测失败：" + str(error)})
        threading.Thread(target=diagnose, daemon=True).start()

    def _open_path(self, path: Path) -> None:
        try:
            self.controller.open_path(path)
        except Exception as error:
            messagebox.showerror("无法打开", str(error), parent=self)

    def _open_learning_pdf(self) -> None:
        desktop = Path.home() / "桌面"
        candidates = [desktop / "RISC-V与PE工程基础命令与排错指南.pdf", desktop / "RISC-V与PE工程入门笔记.pdf"]
        candidates += sorted((Path(__file__).resolve().parents[2] / "学习文档").glob("*.pdf"))
        candidates = sorted((self.controller.task_root.parent / 'docs').glob('*.pdf')) + candidates
        existing = [p for p in candidates if p.is_file()]
        if not existing:
            messagebox.showinfo("学习文档", "桌面暂未找到工程入门 PDF。可以先查看左侧步骤说明和“怎么用”。", parent=self)
            return
        if len(existing) == 1:
            self._open_path(existing[0])
            return
        chooser = tk.Toplevel(self)
        chooser.title("选择学习文档")
        chooser.transient(self)
        chooser.resizable(False, False)
        frame = ttk.Frame(chooser, padding=20)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="点击打开 PDF：").pack(anchor="w", pady=(0, 10))
        for path in existing:
            ttk.Button(frame, text=path.stem, command=lambda p=path: (self._open_path(p), chooser.destroy())).pack(fill="x", pady=5)

    def _require_latest(self) -> Path | None:
        if self._latest_path is None:
            messagebox.showinfo("选择结果", "请先运行一个步骤，或从“历史结果”选择一次运行。", parent=self)
        return self._latest_path

    def _open_latest_directory(self) -> None:
        path = self._require_latest()
        if path:
            self._open_path(path)

    def _open_latest_summary(self) -> None:
        path = self._require_latest()
        if not path:
            return
        for name in ("RESULTS.md", "summary.json", "validation_summary.json", "run.json", "README.md"):
            if (path / name).is_file():
                self._open_path(path / name)
                return
        self._open_path(path)

    def _open_latest_log(self) -> None:
        path = self._require_latest()
        if not path:
            return
        for name in ("console.log", "gate/run.log", "run.log", "dc/run.log", "gate/compile.log", "compile.log"):
            if (path / name).is_file():
                self._open_path(path / name)
                return
        self._open_path(path)

    def _open_wave(self) -> None:
        path = self._latest_path
        if path:
            for name in ("soc.vpd", "build/soc.vpd", "soc.vcd", "build/soc.vcd"):
                wave = path / name
                if wave.is_file():
                    self._open_path(wave)
                    return
        wave = self.project_root / "project/build/soc.vpd"
        if "wave" in self.tasks:
            # The backend also searches public RTL result directories such
            # as rtl.*/delay1/soc.vpd; do not reject them using a local-only
            # existence check here.
            self._enqueue(["wave"])
        elif wave.is_file():
            self._open_path(wave)
        else:
            messagebox.showinfo("波形", "目前没有保存的整机波形。请先运行左侧提供的波形生成步骤，再点击打开。", parent=self)

    def _on_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._pending.clear()
        self._update_queue_text()
        self.run_button.configure(state="disabled")
        self.queue_button.configure(state="disabled")
        self.stop_button.configure(state="disabled")
        self.controller.close()
        self._wait_for_close()

    def _wait_for_close(self) -> None:
        if self.controller.is_running:
            self._status_text.set("正在结束本界面启动的任务，清理完成后自动关闭……")
            self.after(150, self._wait_for_close)
        else:
            self._closed = True
            self.destroy()


def launch(project_root: str | Path | None = None, native_root: str | Path | None = None) -> None:
    ContestApp(project_root, native_root).mainloop()


def main(project_root: str | Path | None = None) -> None:
    launch(project_root)


if __name__ == "__main__":
    main()
