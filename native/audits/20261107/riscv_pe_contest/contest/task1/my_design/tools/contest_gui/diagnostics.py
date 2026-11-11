"""Small read-only diagnostics for the engineering desktop interface."""
from pathlib import Path
import re
import shutil
import subprocess
from datetime import datetime


_PROCESS_NAMES = re.compile(
    r"(?:^|[\s/])(?:dc_shell(?:-xg-t)?|common_shell_ex(?:ec)?|vcs(?:1)?|simv|"
    r"run_contest_gui\.py|contest_gui)(?:[\s/]|$)", re.IGNORECASE)
_ISSUE = re.compile(
    r"\b(?:errors?|warnings?|fatal|failed|failure|timeout|timed\s+out|"
    r"unresolved|unable\s+to\s+resolve|cannot\s+resolve|out\s+of\s+memory|"
    r"killed|segmentation\s+fault|timing\s+violation)\b|"
    r"(?:^|[\s_])(?:FAIL(?:ED)?|ERROR|FATAL|TIMEOUT)(?:[\s_:]|$)|"
    r"未通过|失败|超时|无法解析|内存不足", re.IGNORECASE)


def _size(value):
    number = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if number < 1024 or unit == "TiB":
            return "{:.1f} {}".format(number, unit)
        number /= 1024


def _process_state(state):
    first = state[:1]
    return {"R": "活跃／可运行", "S": "休眠／等待事件", "D": "等待 I/O", "T": "已暂停",
            "t": "调试暂停", "Z": "已退出待回收", "I": "空闲"}.get(first, "其它")


def system_snapshot(project_root):
    """Describe memory, project disk space and related processes without writes."""
    root = Path(project_root).resolve()
    lines = ["系统状态（只读）", "采样时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "工程目录：" + str(root), ""]
    try:
        memory = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            fields = value.split()
            if fields:
                memory[key] = int(fields[0]) * 1024
        total = memory.get("MemTotal", 0)
        available = memory.get("MemAvailable", memory.get("MemFree", 0))
        swap_total = memory.get("SwapTotal", 0)
        swap_free = memory.get("SwapFree", 0)
        lines += ["内存：总计 {}，当前可用 {}，已用约 {}。".format(
            _size(total), _size(available), _size(max(0, total - available))),
            "交换空间：总计 {}，当前可用 {}。".format(_size(swap_total), _size(swap_free))]
    except (OSError, ValueError) as error:
        lines.append("无法读取内存状态：" + str(error))
    disk_path = root
    while not disk_path.exists() and disk_path.parent != disk_path:
        disk_path = disk_path.parent
    try:
        disk = shutil.disk_usage(str(disk_path))
        lines.append("工程所在磁盘：总计 {}，已用 {}，可用 {}。".format(
            _size(disk.total), _size(disk.used), _size(disk.free)))
    except OSError as error:
        lines.append("无法读取磁盘状态：" + str(error))
    lines += ["", "EDA 与工程界面相关进程", "PID      PPID     CPU%    已运行时间    状态                  命令"]
    try:
        result = subprocess.run(["ps", "-eo", "pid=,ppid=,pcpu=,etime=,stat=,args="],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=5, check=False)
        if result.returncode:
            lines.append("读取进程列表失败：" + result.stderr.strip())
        else:
            count = 0
            for raw in result.stdout.splitlines():
                fields = raw.strip().split(None, 5)
                if len(fields) != 6:
                    continue
                pid, ppid, cpu, elapsed, state, command = fields
                executable = Path(command.split(None, 1)[0]).name
                if executable == "ps" or not _PROCESS_NAMES.search(command):
                    continue
                description = "{} ({})".format(_process_state(state), state)
                lines.append("{:<8} {:<8} {:<7} {:<13} {:<21} {}".format(
                    pid, ppid, cpu, elapsed, description, command))
                count += 1
            if not count:
                lines.append("当前未发现匹配的 DC、VCS、simv 或本工程图形界面进程。")
    except (OSError, subprocess.TimeoutExpired) as error:
        lines.append("无法读取进程列表：" + str(error))
    lines += ["", "说明：CPU% 是 ps 的进程平均占用率；“已运行时间”是进程启动以来的时长。",
              "“休眠／等待事件”常见于等待许可证、文件、子进程或输入，不能仅凭此状态判定卡死。",
              "这份快照不会停止或修改任何进程；结果是否通过仍以本次 summary 和完整日志为准。"]
    return "\n".join(lines)


def _extract_lines(lines, max_matches):
    try:
        limit = max(1, int(max_matches))
    except (TypeError, ValueError):
        limit = 300
    matches = []
    total = 0
    for number, line in enumerate(lines, 1):
        line = line.rstrip("\r\n")
        if _ISSUE.search(line):
            total += 1
            if len(matches) < limit:
                matches.append("{:>6} | {}".format(number, line))
    heading = ["日志问题提示筛选", "按固定关键词筛选并保留原始行号，不是最终判定；"
               "Warning、Total errors: 0 等文字也可能被选中。",
               "筛选结果不能代替 summary 和完整日志；相邻上下文需回到原文件查看。", ""]
    if not total:
        return "\n".join(heading + ["未匹配到所列关键词。这不等于验证已经通过。"])
    footer = ["", "共匹配 {} 行，显示 {} 行。".format(total, len(matches))]
    if total > limit:
        footer.append("其余匹配行未在此显示，请打开完整日志查看。")
    return "\n".join(heading + matches + footer)


def extract_log_issues(text, max_matches=300):
    """Keep matching original log lines and their one-based line numbers."""
    return _extract_lines(str(text).splitlines(), max_matches)


def extract_log_file_issues(path, max_matches=300):
    """Scan an entire saved log as a stream, retaining only bounded matches."""
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as stream:
            return _extract_lines(stream, max_matches)
    except (OSError, TypeError, ValueError) as error:
        return "日志问题提示筛选\n无法读取日志文件：{}\n原因：{}".format(path, error)
