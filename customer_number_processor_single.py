#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
号码工作台 - 单一文件集成版 (Number Workbench Single File)
作者：陆山君 (Lu Shanjun)
版本：2.0 单文件版
更新日期：2026-10-09

📋 功能特性
✅ 极速号码清洗和过滤（NumPy 二进制解析）
✅ PyQt6 现代化 UI 界面（Fluent Design）
✅ 数据验证与质量检查
✅ 批量格式化导出工具
✅ 纯文件处理模式（无数据库依赖）
✅ 一键构建可执行文件

🚀 使用方法
python customer_number_processor_single.py           # 运行 GUI 程序
python customer_number_processor_single.py console   # 控制台工具模式
python customer_number_processor_single.py build     # 构建 exe 文件

运行环境：Python 3.10+；依赖 numpy、PyQt6。
构建依赖：pyinstaller（仅构建时需要）。
构建产物：dist/号码工作台.exe。
输出目录必须可写；临时文件在目标目录创建。
"""

from __future__ import annotations

import sys
import os
import re
import logging
import time
import shutil
import subprocess
import codecs
import csv
import heapq
import pickle
import tempfile
import threading
import json
import uuid
from logging.handlers import RotatingFileHandler
from array import array
from contextlib import contextmanager, ExitStack
from datetime import datetime
from typing import List, Dict, Any, Callable, Optional, Tuple, Iterable, Iterator, Sequence, Set, TextIO

import numpy as np
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLineEdit, QLabel, QFileDialog,
    QMessageBox, QTextEdit, QProgressBar, QStatusBar,
    QDialog, QDialogButtonBox, QPlainTextEdit, QFrame, QGridLayout,
    QStackedWidget, QScrollArea, QTabWidget, QSplitter, QComboBox,
    QCheckBox, QGroupBox, QSpinBox, QDoubleSpinBox, QTableView,
    QHeaderView, QInputDialog, QToolBar, QFormLayout, QSizePolicy, QListWidget
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, pyqtSlot, QTimer, QSettings, QUrl, QAbstractTableModel, QSize
from PyQt6.QtGui import (
    QFont, QDragEnterEvent, QDropEvent, QDesktopServices,
    QKeySequence, QShortcut, QCloseEvent, QResizeEvent,
    QIcon, QPixmap, QPainter, QLinearGradient, QColor, QPen,
    QAction, QActionGroup
)
from library_matcher import scan_library_directories

# ============================================================
# 配置和日志
# ============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
_LOG_DIR = os.path.join(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir(), 'NumberWorkbench', 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
_file_log = RotatingFileHandler(os.path.join(_LOG_DIR, 'workbench.log'), maxBytes=5*1024*1024,
                                backupCount=3, encoding='utf-8')
_file_log.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
logging.getLogger().addHandler(_file_log)

APP_NAME = "号码工作台"
PROGRESS_CLASSIFY = 15
PROGRESS_PARSE = 55
PROGRESS_FILTER = 29
PREVIEW_LIMIT = 100
SKIP_EXAMPLES_LIMIT = 5

APP_VERSION = "2.0 Single File Edition"
AUTHOR = "陆山君 (Lu Shanjun)"

# 分类库文件名
CLASSIFY_FILES = {
    'invalid': 'invalid_customers.txt',
    'valid':   'valid_customers.txt',
    'opened':  'opened_customers.txt',
}

# ============================================================
# 基础工具函数（纯文件处理核心）
# ============================================================
_NON_DIGIT = re.compile(r'\D')
_SPLIT = re.compile(r'[,\s;]+')
_FORMATTED_US = re.compile(
    r'(?<![\w+])(?:\+?1[ \t.-]+[0-9]{10}|'
    r'(?:\+?1[ \t.-]*)?(?:\([0-9]{3}\)[ \t.-]*|'
    r'[0-9]{3}[ \t.-]+)[0-9]{3}[ \t.-]+[0-9]{4})(?![0-9])'
)
_READ_BYTES = 1 << 20
_BLOCK_NUMBERS = 65_536
_MAX_RECORD = 1 << 20
_INT64_MAX_TEXT = str(np.iinfo(np.int64).max)
_INT64_MAX = int(np.iinfo(np.int64).max)
_work_progress = threading.local()


def _check_interruption() -> None:
    if QThread.currentThread().isInterruptionRequested():
        raise InterruptedError("任务已取消")


@contextmanager
def _operation_progress(callback: Callable[[int], None]) -> Iterator[None]:
    previous = getattr(_work_progress, 'callback', None)
    _work_progress.callback = callback
    try:
        yield
    finally:
        _work_progress.callback = previous


def _report_progress(done: int, total: int) -> None:
    _check_interruption()
    callback = getattr(_work_progress, 'callback', None)
    if callback:
        callback(min(99, int(done * 100 / total)) if total else 99)


def _same_file(first: str, second: str) -> bool:
    if os.path.normcase(os.path.realpath(first)) == os.path.normcase(os.path.realpath(second)):
        return True
    try:
        return os.path.samefile(first, second)
    except OSError:
        return False


def _check_output_paths(inputs: Iterable[str], outputs: Iterable[str]) -> None:
    outputs = [os.fspath(p) for p in outputs if p]
    for index, output in enumerate(outputs):
        for other in outputs[:index] + [p for p in inputs if p]:
            if _same_file(output, other):
                raise ValueError(f"输出路径与输入或其他输出重复：{output}")


def _write_journal(path, document):
    fd, temporary = tempfile.mkstemp(dir=os.path.dirname(path), suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(document, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _recover_transaction(journal):
    with open(journal, encoding='utf-8') as stream:
        document = json.load(stream)
    # Journals are recovery instructions: reject paths outside the scanned directory.
    directory = os.path.dirname(os.path.abspath(journal))
    for entry in document['entries']:
        for key in ('target', 'backup', 'temporary'):
            if os.path.dirname(os.path.abspath(entry[key])) != directory:
                raise ValueError('事务恢复路径超出目标目录')
    if document['state'] != 'committed':
        for entry in reversed(document['entries']):
            if os.path.exists(entry['backup']):
                # Copy before replacement keeps recovery idempotent if interrupted again.
                fd, restore = tempfile.mkstemp(dir=directory, suffix='.restore')
                os.close(fd)
                try:
                    shutil.copyfile(entry['backup'], restore)
                    os.replace(restore, entry['target'])
                finally:
                    if os.path.exists(restore):
                        os.unlink(restore)
            elif not entry['existed'] and os.path.exists(entry['target']):
                os.unlink(entry['target'])
    for entry in document['entries']:
        for key in ('backup', 'temporary'):
            if os.path.exists(entry[key]):
                os.unlink(entry[key])
    os.unlink(journal)


def recover_output_transactions(directory):
    for name in sorted(os.listdir(directory)):
        if name.startswith('.number-transaction-') and name.endswith('.json'):
            _recover_transaction(os.path.join(directory, name))


@contextmanager
def _output_directory_lock(directory):
    import msvcrt
    # OS releases this lock on process termination; the empty lock file may remain.
    with open(os.path.join(directory, '.number-workbench.lock'), 'a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('输出目录正在被其他任务使用，请稍后重试') from exc
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


@contextmanager
def _atomic_text_outputs(paths: Sequence[str]) -> Iterator[List[TextIO]]:
    if not paths:
        raise ValueError('至少需要一个输出路径')
    directory = os.path.dirname(os.path.abspath(paths[0]))
    with _output_directory_lock(directory):
        with _atomic_text_outputs_unlocked(paths) as streams:
            yield streams


@contextmanager
def _atomic_text_outputs_unlocked(paths: Sequence[str]) -> Iterator[List[TextIO]]:
    """Fixed-path transaction: rollback on exceptions, journal recovery on next access.

    Multiple files must share one directory. This is not simultaneous group visibility.
    """
    _check_output_paths([], paths)
    targets = [os.path.abspath(path) for path in paths]
    directories = {os.path.dirname(path) for path in targets}
    if len(directories) != 1:
        raise ValueError('多文件事务的输出必须在同一目录')
    directory = directories.pop()
    recover_output_transactions(directory)
    entries, streams = [], []
    journal = os.path.join(directory, '.number-transaction-' + uuid.uuid4().hex + '.json')
    document = {'state': 'pending', 'entries': entries}
    try:
        for target in targets:
            fd, temporary = tempfile.mkstemp(prefix='.number-workbench-', suffix='.tmp', dir=directory)
            streams.append(os.fdopen(fd, 'w', encoding='utf-8', buffering=_READ_BYTES))
            entries.append(dict(target=target, temporary=temporary,
                                backup=temporary + '.bak', existed=os.path.exists(target)))
        yield streams
        _check_interruption()
        for stream in streams:
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
        _write_journal(journal, document)
        for entry in entries:
            if entry['existed']:
                # Keep the old target visible until its single-file atomic replacement.
                snapshot = entry['backup'] + '.snapshot'
                try:
                    with open(entry['target'], 'rb') as original, open(snapshot, 'wb') as backup:
                        shutil.copyfileobj(original, backup, _READ_BYTES)
                        backup.flush()
                        os.fsync(backup.fileno())
                    os.replace(snapshot, entry['backup'])
                finally:
                    if os.path.exists(snapshot):
                        os.unlink(snapshot)
        for entry in entries:
            os.replace(entry['temporary'], entry['target'])
        document['state'] = 'committed'
        _write_journal(journal, document)
    except BaseException as exc:
        for stream in streams:
            stream.close()
        if os.path.exists(journal):
            try:
                _recover_transaction(journal)
            except Exception as recovery_error:
                raise RuntimeError(f'提交失败且回滚失败；保留恢复清单 {journal}：{recovery_error}') from exc
        raise
    finally:
        for stream in streams:
            stream.close()
        if not os.path.exists(journal):
            for entry in entries:
                if os.path.exists(entry['temporary']):
                    os.unlink(entry['temporary'])
    # Cleanup errors retain a committed journal; next recovery only removes backups.
    _recover_transaction(journal)


def _iter_text_records(stream: TextIO) -> Iterator[str]:
    count = 0
    while True:
        line = stream.readline(_MAX_RECORD + 1)
        if not line:
            return
        if len(line) > _MAX_RECORD:
            raise ValueError(f"单条记录超过 {_MAX_RECORD:,} 字符，请先分行")
        count += 1
        if count % 256 == 0:
            _check_interruption()
        yield line


def _tokens_from_line(line: str) -> Iterator[str]:
    offset = 0
    for match in _FORMATTED_US.finditer(line):
        for token in _SPLIT.split(line[offset:match.start()]):
            if token:
                yield token
        yield match.group()
        offset = match.end()
    for token in _SPLIT.split(line[offset:]):
        if token:
            yield token


def _checked_number(digits: str, context: str) -> int:
    # Equal-length ASCII decimal strings have numeric ordering; equality is allowed.
    significant = digits.lstrip('0') or '0'
    if (len(significant) > 19 or
            (len(significant) == 19 and significant > _INT64_MAX_TEXT)):
        raise ValueError(f"号码超出 int64 范围：{context}")
    try:
        number = int(significant)
    except ValueError as exc:
        raise ValueError(f"不能转换为整数：{context}") from exc
    if number > _INT64_MAX:
        raise ValueError(f"号码超出 int64 范围：{context}")
    return number


def _report_warning(message: str) -> None:
    logger.warning(message)
    signal = getattr(QThread.currentThread(), 'log', None)
    if signal is not None and hasattr(signal, 'emit'):
        signal.emit(message)


def _iter_file_numbers_policy(path: str, skip_invalid: bool) -> Iterator[int]:
    enc = _detect_encoding(path)
    size = os.path.getsize(path)
    skipped = 0
    examples = []
    with open(path, 'r', encoding=enc) as stream:
        for line_number, line in enumerate(_iter_text_records(stream), 1):
            if line_number % 256 == 0:
                _report_progress(stream.buffer.tell(), size)
            record = line.strip()
            plain = record.isdecimal()
            for token in (record,) if plain else _tokens_from_line(record):
                digits = token if plain else clean_one(token)
                if not digits:
                    continue
                try:
                    number = (int(digits) if len(digits) < 19 else
                              _checked_number(digits, f"{path}:{line_number}"))
                except ValueError:
                    if not skip_invalid:
                        raise
                    skipped += 1
                    if len(examples) < SKIP_EXAMPLES_LIMIT:
                        examples.append(str(line_number))
                    continue
                yield number
        _report_progress(size, size)
    if skipped:
        _report_warning(f"分类库 {path}：跳过 {skipped:,} 个无法转换为 int64 的号码；"
                        f"示例行号：{', '.join(examples)}")


def _iter_file_numbers(path: str) -> Iterator[int]:
    yield from _iter_file_numbers_policy(path, skip_invalid=False)


def clean_one(s: Any) -> str:
    """清洗为纯数字（美国 11 位，1 前缀保留）"""
    if s is None:
        return ''
    return _NON_DIGIT.sub('', str(s))


def normalize_us_number(d: str) -> str:
    """规范化为美国 11 位格式"""
    if not d or not d.isdecimal():
        return ''
    if len(d) == 10:
        return '1' + d
    if len(d) == 11 and d[0] == '1':
        return d
    return ''


def _detect_encoding(path: str) -> str:
    """自动检测文件编码"""
    explicit = getattr(QThread.currentThread(), 'input_encoding', None) or os.environ.get('NUMBER_WORKBENCH_ENCODING')
    if explicit:
        codecs.lookup(explicit)
        return explicit
    with open(path, 'rb') as f:
        head = f.read(8192)
    if head.startswith(codecs.BOM_UTF8):
        return 'utf-8-sig'
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return 'utf-16'
    # BOM takes precedence; ASCII is valid UTF-8. GB18030 also covers GBK.
    # Sampling is heuristic: later bytes may still fail strict decoding.
    for enc in ('utf-8', 'gb18030'):
        try:
            decoder = codecs.getincrementaldecoder(enc)(errors='strict')
            with open(path, 'rb') as stream:
                while True:
                    block = stream.read(_READ_BYTES)
                    decoder.decode(block, final=not block)
                    _check_interruption()
                    if not block:
                        break
            return enc
        except UnicodeDecodeError:
            continue
    raise UnicodeError('文件不是有效 UTF-8 或 GB18030，请显式指定编码')


def _safe_int(value: Any, fallback: int) -> int:
    """安全转换整数"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


# ============================================================
# 文件处理模块（替代数据库功能）
# ============================================================

def load_classify_set(paths: Iterable[str]) -> Set[int]:
    """加载分类库集合"""
    s = set()
    for path in paths:
        if not path:
            continue
        s.update(_iter_file_numbers_policy(path, skip_invalid=True))
    _check_interruption()
    return s


def parse_source_fast(path: str) -> np.ndarray:
    """O(N) result; O(block bytes) workspace, O(N) final concatenation."""
    chunks = []
    pending = b''
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        while True:
            block = f.read(_READ_BYTES)
            if not block:
                data = pending
                pending = b''
                final = True
            else:
                data = pending + block
                boundary = data.rfind(b'\n') + 1
                if not boundary:
                    # Fall back on the first block without a newline; pending is
                    # always a suffix of one block, so it cannot grow unbounded.
                    logger.info("非固定行格式，使用逐记录解析")
                    chunks.clear()
                    del data, block, pending
                    return _parse_source_slow(path)
                pending = data[boundary:]
                data = data[:boundary]
                final = False
            if data:
                try:
                    raw = np.frombuffer(data, dtype=np.uint8)  # Zero-copy O(1) view.
                    chunks.append(_parse_11digit_fast(raw))
                except ValueError:
                    logger.info("行边界或字符不符合快路径，使用逐记录解析")
                    chunks.clear()
                    del raw, data, block
                    return _parse_source_slow(path)
            _report_progress(f.tell(), size)
            if final:
                break
    if not chunks:
        return np.empty(0, dtype=np.int64)
    if len(chunks) == 1:
        return chunks[0]
    result = np.empty(sum(len(chunk) for chunk in chunks), dtype=np.int64)
    offset = 0
    for chunk in chunks:
        _check_interruption()
        result[offset:offset + len(chunk)] = chunk
        offset += len(chunk)
    return result  # O(N), peak roughly 16*N bytes; cancellable between chunks.


def _parse_11digit_fast(raw: np.ndarray) -> np.ndarray:
    """Validate every record, then accumulate columns with O(B) workspace."""
    if raw.ndim != 1 or raw.dtype != np.uint8:
        raise ValueError("快路径需要一维 uint8 字节数组")
    if not len(raw):
        return np.empty(0, dtype=np.int64)
    if raw[-1] != 10:
        ending = [13, 10] if len(raw) > 12 and raw[11] == 13 and raw[12] == 10 else [10]
        raw = np.concatenate((raw, np.array(ending, dtype=np.uint8)))  # O(bytes).
    records = None
    for width in (12, 13):
        if len(raw) % width:
            continue
        candidate = raw.reshape(-1, width)  # O(1) view.
        if not np.all(candidate[:, -1] == 10):
            continue
        if width == 13 and not np.all(candidate[:, -2] == 13):
            continue
        records = candidate
        break
    if records is None:
        raise ValueError("快路径行长度不一致")
    result = np.zeros(len(records), dtype=np.int64)  # O(B), not O(11*B) int64.
    for column in range(11):
        digits = records[:, column]
        if np.any(digits < 48) or np.any(digits > 57):  # O(B) bool temporary.
            raise ValueError("快路径记录含非 ASCII 数字")
        result *= 10
        result += digits
        result -= 48
    return result


def _parse_source_slow(path: str) -> np.ndarray:
    """O(N) packed storage; frombuffer adds an O(1) view, no Python-int list."""
    packed = array('q')
    for number in _iter_file_numbers(path):
        packed.append(number)
    _check_interruption()
    return np.frombuffer(packed, dtype=np.int64)


def _canonical_keys(values: np.ndarray) -> np.ndarray:
    keys = values.copy()  # O(B) or O(M); source/output digits stay unchanged.
    ten_digit = (keys >= 1_000_000_000) & (keys < 10_000_000_000)
    keys[ten_digit] += 10_000_000_000
    return keys


def _membership_index(classify_set: Iterable[int]) -> np.ndarray:
    _check_interruption()
    values = np.fromiter(classify_set, dtype=np.int64, count=len(classify_set))  # O(M).
    keys = _canonical_keys(values)
    _check_interruption()
    keys.sort(kind='quicksort')  # In-place, O(log M) sorting workspace.
    _check_interruption()
    return keys


def _membership_mask(values: np.ndarray, sorted_keys: np.ndarray) -> np.ndarray:
    if not len(sorted_keys):
        return np.zeros(len(values), dtype=bool)  # O(B).
    keys = _canonical_keys(values)
    indices = np.searchsorted(sorted_keys, keys)  # O(B) intp, O(B log M) time.
    np.minimum(indices, len(sorted_keys) - 1, out=indices)
    return sorted_keys[indices] == keys  # O(B) gathered int64 + boolean result.


def _write_number_block(stream: TextIO, values: np.ndarray) -> None:
    if len(values):
        stream.write('\n'.join(map(str, values.tolist())) + '\n')  # O(B) text/list.


def filter_arrays(source_arr: np.ndarray, classify_set: Set[int], output_path: str, removed_path: Optional[str]=None) -> Tuple[int, int, int, List[str]]:
    """
    剔除分类库中的号码并写入文件
    
    Returns: (total, kept, removed, preview)
    """
    if source_arr.ndim != 1 or source_arr.dtype != np.int64:
        raise ValueError("源号码必须是一维 int64 数组")
    paths = [output_path] + ([removed_path] if removed_path else [])
    keys = _membership_index(classify_set)
    total = len(source_arr)
    kept = removed = 0
    # Empty input still commits empty output files and returns zero counts.
    preview = []
    # Extra NumPy/text memory is O(M+B), excluding the O(N) input and Python set.
    with _atomic_text_outputs(paths) as streams:
        for start in range(0, total, _BLOCK_NUMBERS):
            block = source_arr[start:start + _BLOCK_NUMBERS]  # O(1) view.
            mask = _membership_mask(block, keys)
            kept_block = block[~mask]  # O(B), no full-file copies.
            kept += len(kept_block)
            removed += int(np.count_nonzero(mask))
            preview.extend(str(x) for x in kept_block[:PREVIEW_LIMIT - len(preview)].tolist())
            _write_number_block(streams[0], kept_block)
            if removed_path:
                _write_number_block(streams[1], block[mask])  # O(B).
            _report_progress(start + len(block), total)
    return total, kept, removed, preview


def normalize_txt(input_path: str, output_path: str, progress_cb: Optional[Callable[[int], None]]=None, log_cb: Optional[Callable[[str], None]]=None) -> Tuple[int, int]:
    """把任意格式 txt 规范化为'每行一个 11 位数字'"""
    enc = _detect_encoding(input_path)
    total_input = 0
    valid_output = 0
    skipped = 0

    buf = []

    def flush(f: Any) -> None:
        nonlocal valid_output
        if buf:
            f.write('\n'.join(buf))
            f.write('\n')
            valid_output += len(buf)
            buf.clear()

    _check_output_paths([input_path], [output_path])
    size = os.path.getsize(input_path)
    with open(input_path, 'r', encoding=enc) as fin, \
         _atomic_text_outputs([output_path]) as streams:
        fout = streams[0]
        for line_number, line in enumerate(_iter_text_records(fin), 1):
            line = line.strip()
            if not line:
                continue
            for raw in _tokens_from_line(line):
                if not raw:
                    continue
                total_input += 1
                d = clean_one(raw)
                if not d:
                    skipped += 1
                    continue
                d = normalize_us_number(d)
                if d:
                    buf.append(str(int(d)))
                else:
                    skipped += 1

                if len(buf) >= _BLOCK_NUMBERS:
                    flush(fout)
            if line_number % 256 == 0:
                _report_progress(fin.buffer.tell(), size)
                if progress_cb:
                    progress_cb(total_input)

        flush(fout)
        _report_progress(size, size)
        if progress_cb:
            progress_cb(total_input)

    # Success statistics are reported only after atomic output commit succeeds.
    if log_cb:
        log_cb(f"输入行数：{total_input:,}")
        log_cb(f"输出号码：{valid_output:,}")
        log_cb(f"跳过异常：{skipped:,}")

    return total_input, valid_output


# ============================================================
# 数据验证模块（原 verify_data.py 功能）
# ============================================================

class DataValidator:
    """数据质量验证器"""
    
    @staticmethod
    def validate_format(number: str) -> Tuple[bool, str]:
        """验证号码格式是否合法"""
        if not number:
            return False, "空值"
        
        digits = clean_one(number)
        
        if not digits:
            return False, "无数字字符"
        
        if len(digits) == 11:
            if digits[0] != '1':
                return False, "11 位北美号码必须以 1 开头"
            digits = digits[1:]
        elif len(digits) != 10:
            return False, f"长度 {len(digits)} 超出范围 (10 或 11)"
        return True, "有效"

    @staticmethod
    def validate_npa_nxx(number: str) -> Tuple[bool, List[str]]:
        valid, reason = DataValidator.validate_format(number)
        if not valid:
            return False, [reason]
        digits = str(int(clean_one(number))).zfill(10)[-10:]
        errors = []
        if digits[0] in '01':
            errors.append('区号无效')
        if digits[3] in '01':
            errors.append('前缀无效')
        return not errors, errors

    def validate_batch(self, numbers: Iterable[str]) -> Dict[str, List[str]]:
        """批量验证号码列表"""
        results = {
            'valid': [],
            'invalid_format': [],
            'invalid_npanxx': [],
            'duplicates': []
        }
        
        seen = set()
        
        for index, num in enumerate(numbers):
            if index % 256 == 0:
                _check_interruption()
            cleaned = clean_one(num)
            
            if not cleaned:
                results['invalid_format'].append(num)
                continue
            
            canonical = normalize_us_number(cleaned)
            if not canonical:
                results['invalid_format'].append(num)
                continue
            key = canonical
            if key in seen:
                results['duplicates'].append(num)
                continue
            else:
                seen.add(key)
            
            fmt_ok, _ = self.validate_format(num)
            if not fmt_ok:
                results['invalid_format'].append(num)
                continue
            
            npa_ok, _ = self.validate_npa_nxx(num)
            if not npa_ok:
                results['invalid_npanxx'].append(num)
            else:
                results['valid'].append(num)
        
        return results
    
    def check_file_quality(self, file_path: str, statistics_only: bool = True) -> Dict[str, Any]:
        # Priority: structural error -> duplicate -> NPA/NXX error -> valid.
        # Exact deduplication still requires O(U) memory; samples are bounded.
        results = {key: [] for key in ('valid', 'invalid_format', 'invalid_npanxx', 'duplicates')}
        counts = dict.fromkeys(results, 0)
        seen = set()
        try:
            with open(file_path, 'r', encoding=_detect_encoding(file_path)) as stream:
                for line in _iter_text_records(stream):
                    number = line.strip()
                    if not number:
                        continue
                    canonical = normalize_us_number(clean_one(number))
                    if not canonical:
                        key = 'invalid_format'
                    elif canonical in seen:
                        key = 'duplicates'
                    else:
                        seen.add(canonical)
                        key = 'valid' if self.validate_npa_nxx(number)[0] else 'invalid_npanxx'
                    counts[key] += 1
                    if not statistics_only or len(results[key]) < SKIP_EXAMPLES_LIMIT:
                        results[key].append(number)
        except InterruptedError:
            raise
        except Exception as exc:
            return {'error': f'读取失败：{exc}'}
        total = sum(counts.values())
        stats = dict(file=file_path, total_lines=total, valid_numbers=counts['valid'],
                     invalid_format=counts['invalid_format'], invalid_npanxx=counts['invalid_npanxx'],
                     duplicates=counts['duplicates'], validity_rate=100 * counts['valid'] / total if total else 0)
        return {'statistics': stats, 'results': results, 'statistics_only': statistics_only}


# ============================================================
# 导出格式化模块（原 format_export.py 功能）
# ============================================================

def _external_sorted_numbers(numbers: Iterable[str], directory: str) -> Iterator[str]:
    """Preserve sorted(numbers), with O(B + run count) RAM and <=32 open runs."""
    def records(stream):
        while True:
            try:
                yield pickle.load(stream)
            except EOFError:
                return

    serial = 0

    def save_run(values):
        nonlocal serial
        filename = os.path.join(directory, f'run-{serial}.bin')
        serial += 1
        with open(filename, 'wb') as stream:
            for index, value in enumerate(values):
                if index % 256 == 0:
                    _check_interruption()
                pickle.dump(value, stream, protocol=pickle.HIGHEST_PROTOCOL)
        return filename

    paths = []
    chunk = []
    for value in numbers:
        chunk.append(value)
        if len(chunk) >= _BLOCK_NUMBERS:
            chunk.sort()
            paths.append(save_run(chunk))
            chunk.clear()
    if not paths:
        yield from sorted(chunk)
        return
    if chunk:
        chunk.sort()
        paths.append(save_run(chunk))
    while len(paths) > 32:
        merged_paths = []
        for start in range(0, len(paths), 32):
            group = paths[start:start + 32]
            with ExitStack() as stack:
                inputs = [stack.enter_context(open(path, 'rb')) for path in group]
                merged_paths.append(save_run(heapq.merge(*(records(f) for f in inputs))))
            for path in group:
                os.unlink(path)
        paths = merged_paths
    with ExitStack() as stack:
        inputs = [stack.enter_context(open(path, 'rb')) for path in paths]
        yield from heapq.merge(*(records(f) for f in inputs))


class FormatExporter:
    """批量号码格式化和导出器"""
    
    @staticmethod
    def format_us_standard(number: str) -> str:
        """转换为美国标准格式：(XXX) XXX-XXXX"""
        digits = normalize_us_number(clean_one(number))
        if not digits:
            return number
        number = digits[1:]
        
        area_code = number[:3]
        prefix = number[3:6]
        line_num = number[6:]
        
        return f"({area_code}) {prefix}-{line_num}"
    
    @staticmethod
    def format_digital_separated(number: str) -> str:
        """数字间隔格式：XXX-XXX-XXXX"""
        digits = normalize_us_number(clean_one(number))
        if not digits:
            return number
        number = digits[1:]
        
        return f"{number[:3]}-{number[3:6]}-{number[6:]}"
    
    @staticmethod
    def format_e164(number: str) -> str:
        """E.164 国际标准格式：+1XXXXXXXXXX"""
        digits = normalize_us_number(clean_one(number))
        return f"+{digits}" if digits else number or ""
    
    @staticmethod
    def export_to_txt(output_path: str, numbers: Iterable[str], format_func: Optional[Callable[[str], str]]=None) -> int:
        """导出到 txt 文件"""
        count = 0
        temporary_root = (os.environ.get('NUMBER_WORKBENCH_TEMP_DIR') or
                          os.path.dirname(os.path.abspath(output_path)))
        with tempfile.TemporaryDirectory(prefix='number-sort-', dir=temporary_root) as directory, \
             _atomic_text_outputs([output_path]) as streams:
            for num in _external_sorted_numbers(numbers, directory):
                if count % 256 == 0:
                    _check_interruption()
                formatted = format_func(num) if format_func else num
                streams[0].write(f"{formatted}\n")
                count += 1
        return count
    
    @staticmethod
    def export_to_csv(output_path: str, data: Sequence[Sequence[Any]], headers: Optional[Sequence[str]]=None) -> int:
        """导出为 CSV 格式"""
        with _atomic_text_outputs([output_path]) as streams:
            f = streams[0]
            reconfigure = getattr(f, 'reconfigure', None)
            if not callable(reconfigure):
                raise RuntimeError("CSV 输出流不支持安全的换行配置")
            reconfigure(newline='')
            f.write('\ufeff')
            writer = csv.writer(f, lineterminator='\n')
            if headers:
                writer.writerow(headers)
            
            for row in data:
                _check_interruption()
                writer.writerow(row)
        return len(data)


# ============================================================
# 后台线程模块
# ============================================================

class RecoveryThread(QThread):
    log = pyqtSignal(str)

    def __init__(self, directories):
        super().__init__()
        self.directories = set(directories)

    def run(self):
        for directory in self.directories:
            try:
                _check_interruption()
                if not any(name.startswith('.number-transaction-') and name.endswith('.json')
                           for name in os.listdir(directory)):
                    continue
                with _output_directory_lock(directory):
                    recover_output_transactions(directory)
                self.log.emit(f'已恢复输出目录：{directory}')
            except InterruptedError:
                return
            except Exception as exc:
                logger.exception('启动恢复失败')
                self.log.emit(f'恢复失败，恢复材料已保留：{directory}: {exc}')


class ProcessThread(QThread):
    """处理线程"""
    progress = pyqtSignal(int)
    stage = pyqtSignal(str)
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, source_path: str, output_path: str, removed_path: Optional[str], classify_paths: Iterable[str]) -> None:
        super().__init__()
        self.source_path = source_path
        self.output_path = output_path
        self.removed_path = removed_path
        self.classify_paths = classify_paths
        self._progress_value = 0

    def _emit_progress(self, value):
        self._progress_value = max(self._progress_value, value)
        self.progress.emit(self._progress_value)

    def run(self) -> None:
        try:
            t_start = time.time()
            self._progress_value = 0
            _check_output_paths([self.source_path] + list(self.classify_paths),
                                [self.output_path, self.removed_path])

            self.stage.emit("正在加载分类库…")
            self.log.emit("加载分类库...")
            t0 = time.time()
            with _operation_progress(lambda value: self._emit_progress(value * PROGRESS_CLASSIFY // 100)):
                classify_set = load_classify_set(self.classify_paths)
            self.log.emit(f"→ {len(classify_set):,} 个分类号码 ({time.time()-t0:.2f}s)")
            self._emit_progress(15)

            self.stage.emit("正在解析源文件…")
            self.log.emit("解析源文件...")
            t0 = time.time()
            with _operation_progress(lambda value: self._emit_progress(PROGRESS_CLASSIFY + value * PROGRESS_PARSE // 100)):
                source_arr = parse_source_fast(self.source_path)
            self.log.emit(f"→ {len(source_arr):,} 个源号码 ({time.time()-t0:.2f}s)")
            self._emit_progress(70)

            self.stage.emit("正在匹配号码并写入结果…")
            self.log.emit("剔除...")
            t0 = time.time()
            with _operation_progress(lambda value: self._emit_progress(PROGRESS_CLASSIFY + PROGRESS_PARSE + value * PROGRESS_FILTER // 100)):
                total, kept, removed, preview = filter_arrays(
                    source_arr, classify_set,
                    self.output_path, self.removed_path)
            self.log.emit(f"→ 保留 {kept:,}，剔除 {removed:,} ({time.time()-t0:.2f}s)")
            self._emit_progress(100)

            elapsed = time.time() - t_start
            self.log.emit(f"✅ 完成！总耗时 {elapsed:.2f} 秒")
            self.stage.emit("处理完成")

            self.done.emit({
                'total': total, 'kept': kept, 'removed': removed,
                'output': self.output_path,
                'removed_output': self.removed_path,
                'preview': preview,
                'elapsed': elapsed,
            })
        except Exception as e:
            if isinstance(e, InterruptedError):
                self.stage.emit("已取消")
                self.log.emit("任务已取消")
                return
            logger.exception("处理失败")
            self.stage.emit("处理失败")
            self.error.emit(str(e))


class NormalizeThread(QThread):
    """规范化线程"""
    progress = pyqtSignal(int)
    stage = pyqtSignal(str)
    log = pyqtSignal(str)
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, input_path: str, output_path: str) -> None:
        super().__init__()
        self.input_path = input_path
        self.output_path = output_path

    def run(self) -> None:
        try:
            t0 = time.time()
            self.stage.emit("正在规范化号码文件…")
            self.log.emit(f"规范化：{os.path.basename(self.input_path)}")
            with _operation_progress(self.progress.emit):
                total_in, total_out = normalize_txt(
                    self.input_path, self.output_path, log_cb=self.log.emit,
                )
            elapsed = time.time() - t0
            self.progress.emit(100)
            self.log.emit(f"✅ 规范化完成 ({elapsed:.2f}s)")
            self.stage.emit("规范化完成")
            self.done.emit({
                'input': self.input_path,
                'output': self.output_path,
                'total_in': total_in,
                'total_out': total_out,
                'elapsed': elapsed,
            })
        except Exception as e:
            if isinstance(e, InterruptedError):
                self.stage.emit("已取消")
                self.log.emit("任务已取消")
                return
            logger.exception("规范化失败")
            self.stage.emit("规范化失败")
            self.error.emit(str(e))


class CategoryCountThread(QThread):
    """后台统计各分类文件中的唯一号码"""
    counted = pyqtSignal(dict, dict, dict)
    error = pyqtSignal(str)
    log = pyqtSignal(str)

    def __init__(self, paths: Iterable[str]) -> None:
        super().__init__()
        self.paths = dict(paths)

    def run(self) -> None:
        counts = {'invalid': 0, 'valid': 0, 'opened': 0}
        for key in counts:
            path = self.paths.get(key)
            if not path:
                continue
            try:
                counts[key] = len(load_classify_set([path]))
            except Exception as exc:
                logger.exception("分类统计失败：%s", path)
                self.error.emit(f"{path}: {exc}")
                if isinstance(exc, InterruptedError):
                    return

        self.counted.emit(
            {'invalid': counts['invalid']},
            {'valid': counts['valid']},
            {'opened': counts['opened']}
        )


class FileStatusThread(QThread):
    counted = pyqtSignal(str, object, object)
    error = pyqtSignal(str)

    def __init__(self, path: str, key: str) -> None:
        super().__init__()
        self.path = path
        self.key = key

    def run(self) -> None:
        try:
            # Best-effort statistics: concurrent edits can make size/count disagree.
            size = os.path.getsize(self.path)
            with open(self.path, 'r', encoding=_detect_encoding(self.path)) as stream:
                count = sum(1 for _ in _iter_text_records(stream))
            _check_interruption()
            self.counted.emit(self.path, size, count)
        except Exception as exc:
            logger.exception("文件统计失败：%s", self.path)
            self.error.emit(str(exc))


class VerifyExportThread(QThread):
    done = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, mode: str, input_path: str, output_path: Optional[str]=None, format_func: Optional[Callable[[str], str]]=None) -> None:
        super().__init__()
        self.mode = mode
        self.input_path = input_path
        self.output_path = output_path
        self.format_func = format_func

    def run(self) -> None:
        try:
            if self.mode == 'analyze':
                result = DataValidator().check_file_quality(self.input_path)
                if 'error' in result:
                    raise ValueError(result['error'])
                self.done.emit({'mode': 'analyze', 'statistics': result['statistics']})
            else:
                _check_output_paths([self.input_path], [self.output_path])
                with open(self.input_path, 'r', encoding=_detect_encoding(self.input_path)) as stream:
                    numbers = (line.strip() for line in _iter_text_records(stream) if line.strip())
                    count = FormatExporter.export_to_txt(self.output_path, numbers, self.format_func)
                self.done.emit({'mode': 'export', 'count': count})
        except Exception as exc:
            if isinstance(exc, InterruptedError):
                self.done.emit({'mode': 'cancelled'})
                return
            logger.exception("验证或导出失败")
            self.error.emit(str(exc))


# ============================================================
# GUI 界面模块
# ============================================================

# Shared template: every dimension and color comes from StyleHelper tokens.
_QSS_TEMPLATE = """
QMainWindow, QMainWindow QWidget {
    font-family: $font_ui;
    font-size: ${body}px;
    color: $primary;
}
QMainWindow, QWidget[role="root"], QWidget[role="page"], QScrollArea {
    background: $base;
}
QWidget[role="content"], QLabel { background: transparent; }
QLabel[role="title"] { font-size: ${title}px; font-weight: $weight_emphasis; }
QLabel[role="subtitle"] { font-size: ${body}px; font-weight: $weight_emphasis; }
QLabel[role="caption"] { font-size: ${caption}px; color: $secondary; }
QLabel[role="metric"] { font-family: $font_mono; font-size: ${title}px; font-weight: $weight_emphasis; }
QLabel[tone="success"] { color: $success; }
QLabel[tone="warning"] { color: $warning; }
QLabel[tone="danger"] { color: $danger; }
QLabel[tone="neutral"] { color: $neutral; }
QLabel[role="statusHint"] { font-size: ${caption}px; color: $tertiary; }
QFrame[role="card"], QWidget[role="metricCard"] {
    background: $raised;
    border: ${border_width}px solid $border;
    border-radius: ${radius}px;
}
QFrame[role="card"] { border: ${border_width}px solid $border; }
QGroupBox {
    background: $raised;
    border: ${border_width}px solid $border;
    border-radius: ${radius}px;
    margin-top: ${space_medium}px;
    padding-top: ${space_small}px;
    font-weight: $weight_emphasis;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: $zero ${space_small}px;
    color: $primary;
    font-size: ${body}px;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $raised;
    border: ${border_width}px solid $border;
    border-radius: ${radius}px;
    min-height: ${control_content_height}px;
    padding: ${space_small}px ${space_medium}px;
    selection-background-color: $accent_fill;
    selection-color: $on_accent;
}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {
    border-color: $secondary;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: $accent;
}
QLineEdit:focus {
    border: ${focus_border_width}px solid $accent;
    padding: ${focus_padding_vertical}px ${focus_padding_horizontal}px;
}
QLineEdit:disabled, QComboBox:disabled { color: $tertiary; background: $overlay; }
QComboBox QAbstractItemView {
    background: $overlay; border: ${border_width}px solid $border;
    selection-background-color: $accent_fill; selection-color: $on_accent;
    padding: ${space_small}px;
}
QPushButton {
    background: transparent;
    border: ${border_width}px solid $border;
    border-radius: ${radius}px;
    padding: ${space_small}px ${space_medium}px;
    min-height: ${button_content_height}px;
    font-weight: $weight_regular;
}
QPushButton:hover { background: $overlay; border-color: $secondary; }
QPushButton:pressed { background: $border; }
QPushButton[variant="secondary"]:hover { background: $overlay; border-color: $secondary; }
QPushButton[variant="secondary"]:pressed { background: $border; }
QPushButton[variant="primary"] {
    background: $accent_fill; border: none; color: $on_accent;
    min-height: ${primary_button_content_height}px;
}
QPushButton[variant="primary"]:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton[variant="primary"]:pressed { background: $accent_pressed; border-color: $accent_pressed; }
QPushButton[variant="danger"] { color: $danger; border-color: $danger; background: transparent; }
QPushButton[variant="danger"]:hover { background: $overlay; color: $danger; border-color: $danger; }
QPushButton[variant="danger"]:pressed { background: $overlay; color: $danger; border-color: $danger; }
QPushButton:focus { border-color: $accent; }
QPushButton[variant="primary"]:disabled, QPushButton[variant="secondary"]:disabled,
QPushButton[variant="danger"]:disabled, QPushButton:disabled {
    background: $overlay; color: $tertiary; border-color: $border;
}
QTextEdit, QPlainTextEdit {
    background: $log_background;
    border: ${border_width}px solid $border;
    border-radius: ${radius}px;
    padding: ${space_medium}px;
    font-family: $font_mono;
    font-size: ${caption}px;
    selection-background-color: $accent_fill;
    selection-color: $on_accent;
}
QTextEdit:hover, QPlainTextEdit:hover { border-color: $secondary; }
QTextEdit:focus, QPlainTextEdit:focus { border-color: $accent; }
QProgressBar {
    background: $overlay; border: ${border_width}px solid $border;
    border-radius: ${progress_radius}px;
    min-height: ${progress_content_height}px; max-height: ${progress_content_height}px;
}
QProgressBar::chunk { background: $accent; border-radius: ${progress_chunk_radius}px; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab {
    background: transparent; color: $secondary;
    border: none; border-bottom: ${indicator_width}px solid transparent;
    padding: ${space_medium}px ${space_large}px;
    margin-right: ${space_medium}px;
    font-weight: $weight_emphasis;
}
QTabBar::tab:hover { color: $primary; border-bottom-color: $border; }
QTabBar::tab:selected { color: $primary; border-bottom-color: $accent; background: transparent; }
QTabBar::tab:disabled { color: $tertiary; }
QScrollArea { border: none; }
QScrollBar:vertical {
    background: transparent; width: ${scrollbar_width}px;
    margin: $zero;
}
QScrollBar::handle:vertical {
    background: $border; border-radius: ${scrollbar_radius}px;
    min-height: ${space_large}px;
}
QScrollBar::handle:vertical:hover { background: $secondary; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: $zero; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: ${scrollbar_width}px; }
QScrollBar::handle:horizontal { background: $border; border-radius: ${scrollbar_radius}px; min-width: ${space_large}px; }
QScrollBar::handle:horizontal:hover { background: $secondary; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: $zero; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QMenuBar, QStatusBar { background: $base; color: $secondary; border: none; }
QStatusBar { padding: $zero ${space_medium}px; }
QMenuBar::item { padding: ${space_small}px ${space_medium}px; background: transparent; }
QMenuBar::item:selected, QMenuBar::item:pressed { background: $overlay; color: $primary; }
QMenu { background: $raised; border: ${border_width}px solid $border; padding: ${space_small}px; }
QMenu::item { padding: ${space_small}px ${space_medium}px; border-radius: ${radius}px; }
QMenu::item:selected { background: $overlay; }
QMenu::item:disabled { color: $tertiary; }
QMenu::separator { height: ${border_width}px; background: $border; margin: ${space_small}px; }
QSplitter::handle { background: $border; }
QSplitter::handle:hover { background: $accent; }
QToolTip {
    background: $overlay; color: $primary; border: ${border_width}px solid $border;
    padding: ${space_small}px; border-radius: ${radius}px;
}
/* Modal dialogs own their surfaces and controls; never inherit the OS theme. */
QMessageBox, QMessageBox QWidget {
    background: $raised;
    color: $primary;
    font-family: $font_ui;
    font-size: ${body}px;
}
QMessageBox QLabel { background: transparent; color: $primary; }
QMessageBox QDialogButtonBox { background: $raised; }
QMessageBox QPushButton {
    background: $raised; color: $primary;
    border: ${border_width}px solid $border;
    border-radius: ${radius}px;
    padding: ${space_small}px ${space_medium}px;
    min-height: ${button_content_height}px;
}
QMessageBox QPushButton:hover { background: $overlay; border-color: $secondary; }
QMessageBox QPushButton:pressed { background: $border; }
QMessageBox QPushButton:focus { border-color: $accent; }
QMessageBox QPushButton:default {
    background: $accent_fill; color: $on_accent; border: none;
    min-height: ${primary_button_content_height}px;
}
QMessageBox QPushButton:default:hover { background: $accent_hover; }
QMessageBox QPushButton:default:pressed { background: $accent_pressed; }
QMessageBox QPushButton:disabled, QMessageBox QPushButton:default:disabled {
    background: $overlay; color: $tertiary; border: ${border_width}px solid $border;
    min-height: ${button_content_height}px;
}
QMessageBox QTextEdit {
    background: $overlay; color: $primary;
    border: ${border_width}px solid $border;
    font-family: $font_ui;
}

/* Navigation rail: content-sized width, soft selection, no native frame. */
QListWidget[role="navigation"] {
    background: $base; color: $primary; border: none; outline: none;
    padding: ${space_small}px; font-size: ${body}px;
}
QListWidget[role="navigation"]::item {
    background: transparent; border: none; border-radius: ${radius}px;
    min-height: ${button_height}px;
    padding: ${space_small}px ${space_medium}px;
    margin-bottom: ${space_small}px;
}
QListWidget[role="navigation"]::item:hover { background: $overlay; }
QListWidget[role="navigation"]::item:selected {
    background: $navigation_selected; color: $accent;
}
QListWidget[role="navigation"]::item:selected:hover { background: $navigation_selected; }

/* Page container: all pages reuse the existing surfaces and layout tokens. */
QStackedWidget[role="pageContainer"] {
    background: $base; border: none; padding: ${space_small}px;
}
QStackedWidget[role="pageContainer"] > QWidget[role="page"] { background: $base; }
QStackedWidget[role="pageContainer"] QHeaderView::section {
    background: $overlay; color: $secondary; border: none;
    padding: ${space_small}px; font-size: ${caption}px;
}
"""


class StyleHelper:
    """Central design tokens and shared Fluent UI construction helpers."""

    # Only three nonzero spacing values, one radius, and three type sizes.
    SPACING = {'none': 0, 'small': 8, 'medium': 16, 'large': 24}
    RADIUS = 8
    OPACITY = {'navigation_selected': 0.12}
    FONT_SIZES = {'caption': 12, 'body': 14, 'title': 22}
    FONTS = {'ui': '"Microsoft YaHei UI"',
             'mono': '"Consolas"'}
    SIZES = {'button_height': 40, 'input_height': 40, 'progress_height': 8,
             'border_width': 1, 'focus_border_width': 1, 'indicator_width': 2, 'scrollbar_width': 8,
             'log_height': 192, 'report_height': 240, 'browse_width': 80,
             'action_width': 144, 'window_min_width': 960, 'window_min_height': 640,
             'window_width': 1200, 'window_height': 800,
             'processing_left_min_width': 400, 'processing_right_min_width': 440,
             'processing_left_width': 440, 'processing_right_width': 680,
             'verify_options_width': 280, 'verify_report_width': 640,
             'metric_height': 104, 'label_width': 72,
             'weight_regular': 500, 'weight_emphasis': 600}
    COLORS = {
        'light': {
            'base': '#f5f4f2', 'raised': '#fefdfb', 'overlay': '#eceae6',
            'border': '#dcd9d3', 'primary': '#262522', 'secondary': '#686660',
            'tertiary': '#827e76', 'neutral': '#74716b',
            'accent': '#b65a28', 'accent_fill': '#b65a28',
            'accent_hover': '#a75022', 'accent_pressed': '#984a20',
            'danger': '#ad5855', 'success': '#477a5a', 'warning': '#74716b',
            'on_accent': '#fefdfb', 'log_background': '#fefdfb',
        },
        'dark': {
            'base': '#252628', 'raised': '#303134', 'overlay': '#3b3d40',
            'border': '#505256', 'primary': '#e5e4e0', 'secondary': '#b3b0a9',
            'tertiary': '#99968f', 'neutral': '#b3b0a9',
            'accent': '#e99b67', 'accent_fill': '#e99b67',
            'accent_hover': '#f0ad80', 'accent_pressed': '#d88b58',
            'danger': '#d58e88', 'success': '#8bb89a', 'warning': '#b3b0a9',
            'on_accent': '#292723', 'log_background': '#303134',
        },
    }

    @staticmethod
    def _render_style(theme: str) -> str:
        from string import Template
        tokens = dict(StyleHelper.COLORS[theme])
        accent = QColor(tokens['accent'])
        tokens['navigation_selected'] = (
            f"rgba({accent.red()}, {accent.green()}, {accent.blue()}, "
            f"{StyleHelper.OPACITY['navigation_selected']})")
        tokens.update(StyleHelper.FONT_SIZES)
        tokens.update({'space_' + key: value for key, value in StyleHelper.SPACING.items()})
        tokens.update(radius=StyleHelper.RADIUS)
        tokens.update({'font_' + key: value for key, value in StyleHelper.FONTS.items()})
        tokens.update(StyleHelper.SIZES)
        border_growth = StyleHelper.SIZES['focus_border_width'] - StyleHelper.SIZES['border_width']
        # Qt does not clamp oversized radii: cap at half the track height for a pill.
        tokens.update(zero=0, progress_radius=min(StyleHelper.RADIUS,
                                               StyleHelper.SIZES['progress_height'] // 2),
                      scrollbar_radius=StyleHelper.SPACING['small'],
                      progress_content_height=StyleHelper.SIZES['progress_height'] -
                      2 * StyleHelper.SIZES['border_width'],
                      progress_chunk_radius=min(StyleHelper.RADIUS,
                                               (StyleHelper.SIZES['progress_height'] -
                                                2 * StyleHelper.SIZES['border_width']) // 2),
                      focus_padding_vertical=StyleHelper.SPACING['small'] - border_growth,
                      focus_padding_horizontal=StyleHelper.SPACING['medium'] - border_growth,
                      primary_button_content_height=StyleHelper.SIZES['button_height'] -
                      2 * StyleHelper.SPACING['small'],
                      control_content_height=StyleHelper.SIZES['input_height'] -
                      2 * (StyleHelper.SPACING['small'] + StyleHelper.SIZES['border_width']),
                      button_content_height=StyleHelper.SIZES['button_height'] -
                      2 * (StyleHelper.SPACING['small'] + StyleHelper.SIZES['border_width']))
        return Template(_QSS_TEMPLATE).substitute(tokens)

    @staticmethod
    def get_fluent_style(theme: str = 'light') -> str:
        """Return the cached application-wide QSS for a named theme."""
        return StyleHelper._render_style(theme)

    @staticmethod
    def configure_layout(layout: Any, spacing: str = 'medium', margin: str = 'medium') -> None:
        layout.setSpacing(StyleHelper.SPACING[spacing])
        inset = StyleHelper.SPACING[margin]
        layout.setContentsMargins(inset, inset, inset, inset)

    @staticmethod
    def configure_button(button: QPushButton, variant: str = 'secondary') -> None:
        button.setProperty('variant', variant)
        button.setFixedHeight(StyleHelper.SIZES['button_height'])
        button.setCursor(Qt.CursorShape.PointingHandCursor)

    @staticmethod
    def scroll_page(widget: QWidget) -> Tuple[QScrollArea, QVBoxLayout]:
        """Install the same resize-safe scrolling container on both tabs."""
        widget.setProperty('role', 'page')
        root = QVBoxLayout(widget)
        StyleHelper.configure_layout(root, 'none', 'none')
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.verticalScrollBar().setCursor(Qt.CursorShape.PointingHandCursor)
        scroll.horizontalScrollBar().setCursor(Qt.CursorShape.PointingHandCursor)
        content = QWidget()
        content.setProperty('role', 'content')
        layout = QVBoxLayout(content)
        StyleHelper.configure_layout(layout, 'large', 'large')
        scroll.setWidget(content)
        root.addWidget(scroll)
        return scroll, layout

    @staticmethod
    def metric_card(label: str, tone: Optional[str] = None) -> Tuple[QFrame, QLabel]:
        card = QFrame()
        card.setProperty('role', 'card')
        card.setFixedHeight(StyleHelper.SIZES['metric_height'])
        card.setMinimumWidth(0)
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        StyleHelper.configure_layout(layout, 'medium', 'medium')
        caption = QLabel(label)
        caption.setProperty('role', 'caption')
        value = QLabel('0')
        value.setProperty('role', 'metric')
        if tone:
            value.setProperty('tone', tone)
        value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        value.setMinimumWidth(0)
        layout.addWidget(value, 1)
        layout.addWidget(caption)
        return card, value





class NavigationList(QListWidget):
    """A narrow, content-sized rail; its width does not absorb window growth."""

    def sizeHint(self):
        text_width = max((self.fontMetrics().horizontalAdvance(self.item(i).text())
                          for i in range(self.count())), default=0)
        padding = 2 * (StyleHelper.SPACING['large'] + StyleHelper.SPACING['medium'])
        return QSize(max(self.minimumWidth(), text_width + padding + 2 * self.spacing()),
                     super().sizeHint().height())


class MainUI(QMainWindow):
    """主窗口"""
    
    def __init__(self) -> None:
        super().__init__()
        settings_path = os.path.join(os.path.dirname(_LOG_DIR), 'settings.ini')
        first_file_settings = not os.path.exists(settings_path)
        self.settings = QSettings(settings_path, QSettings.Format.IniFormat)
        self.settings.setAtomicSyncRequired(True)
        if first_file_settings:
            legacy = QSettings("号码工作台", "SingleFileEdition")
            for key in ('themeMode', 'geometry', 'windowState'):
                if legacy.contains(key):
                    self.settings.setValue(key, legacy.value(key))
            self.settings.sync()
            if self.settings.status() != QSettings.Status.NoError:
                logger.warning('无法迁移用户界面设置到 %s', settings_path)
        self.theme_mode = 'light'
        self._init_ui()
        self._load_settings()
        self.statusBar().showMessage("准备就绪")
    
    def _init_ui(self):
        """初始化界面"""
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(StyleHelper.SIZES['window_min_width'], StyleHelper.SIZES['window_min_height'])
        self.resize(StyleHelper.SIZES['window_width'], StyleHelper.SIZES['window_height'])
        
        # 主布局
        main_widget = QWidget()
        main_widget.setProperty('role', 'root')
        self.setCentralWidget(main_widget)
        layout = QHBoxLayout(main_widget)
        StyleHelper.configure_layout(layout, 'medium', 'large')
        
        self.navigation = NavigationList()
        self.navigation.setProperty('role', 'navigation')
        self.navigation.addItems(['号码处理', '验证与导出', '运行状态'])
        self.navigation.setMinimumWidth(
            max(self.navigation.fontMetrics().horizontalAdvance(self.navigation.item(i).text())
                for i in range(self.navigation.count())) + 2 * (StyleHelper.SPACING['large'] + StyleHelper.SPACING['small']))
        self.navigation.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self.navigation.setSpacing(StyleHelper.SPACING['small'])
        self.navigation.setFrameShape(QFrame.Shape.NoFrame)
        self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.navigation.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pages = QStackedWidget()
        self.pages.setProperty('role', 'pageContainer')
        # Retain the existing public container reference for UI callers.
        self.tabs = self.pages
        self.processing_tab = ProcessingTab(self)
        self.verify_tab = VerifyExportTab(self)
        self.running_page = self.processing_tab.running_page
        self.pages.addWidget(self.processing_tab)
        self.pages.addWidget(self.verify_tab)
        self.pages.addWidget(self.running_page)
        self.navigation.currentRowChanged.connect(self._navigate_to_page)
        self.navigation.setCurrentRow(0)
        layout.addWidget(self.navigation)
        layout.addWidget(self.pages, 1)

        # 状态栏
        self.statusBar().showMessage("准备就绪")
        
        # 菜单栏
        self._create_menu_bar()
        
        # 拖拽支持
        self.setAcceptDrops(True)
    
    def _navigate_to_page(self, index):
        self.pages.setCurrentIndex(index)

    def _create_menu_bar(self):
        """创建菜单栏"""
        menubar = self.menuBar()
        menubar.setCursor(Qt.CursorShape.PointingHandCursor)
        
        # 文件菜单
        file_menu = menubar.addMenu("文件(&F)")
        
        open_action = QAction("打开文件(&O)...", file_menu)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self._open_file)
        file_menu.addAction(open_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("退出(&X)", file_menu)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 操作菜单
        op_menu = menubar.addMenu("操作(&A)")
        
        process_action = QAction("开始处理(&P)", op_menu)
        process_action.setShortcut("Ctrl+Enter")
        process_action.triggered.connect(lambda: self.processing_tab.start_processing())
        op_menu.addAction(process_action)
        
        refresh_action = QAction("刷新统计(&R)", op_menu)
        refresh_action.setShortcut("Ctrl+R")
        refresh_action.triggered.connect(self._refresh_stats)
        op_menu.addAction(refresh_action)

        appearance_menu = menubar.addMenu("外观(&V)")
        self.theme_group = QActionGroup(self)
        self.theme_group.setExclusive(True)
        self.light_theme_action = QAction("浅色模式", appearance_menu)
        self.dark_theme_action = QAction("深色模式", appearance_menu)
        self.light_theme_action.setCheckable(True)
        self.dark_theme_action.setCheckable(True)
        self.light_theme_action.setToolTip("使用 Windows 11 Fluent 浅色外观")
        self.dark_theme_action.setToolTip("使用 Windows 11 Fluent 深色外观")
        self.theme_group.addAction(self.light_theme_action)
        self.theme_group.addAction(self.dark_theme_action)
        self.light_theme_action.triggered.connect(lambda checked: checked and self._set_theme('light'))
        self.dark_theme_action.triggered.connect(lambda checked: checked and self._set_theme('dark'))
        appearance_menu.addAction(self.light_theme_action)
        appearance_menu.addAction(self.dark_theme_action)
        
        # 帮助菜单
        help_menu = menubar.addMenu("帮助(&H)")
        
        about_action = QAction("关于(&A)", help_menu)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)
        for action in menubar.actions():
            menu = action.menu()
            if menu is not None:
                menu.setCursor(Qt.CursorShape.PointingHandCursor)
    
    def _load_settings(self):
        """加载保存的设置"""
        saved_theme = self.settings.value("themeMode", "light")
        self._set_theme(saved_theme if saved_theme in ('light', 'dark') else 'light', persist=False)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        
        window_state = self.settings.value("windowState")
        if window_state:
            self.restoreState(window_state)
    
    def save_settings(self) -> None:
        """保存设置"""
        self.settings.setValue("themeMode", self.theme_mode)
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("windowState", self.saveState())

    def _set_theme(self, theme, persist=True):
        """Apply and optionally persist the selected application appearance."""
        if theme not in ('light', 'dark'):
            return
        self.theme_mode = theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(StyleHelper.get_fluent_style(theme))
        self.light_theme_action.setChecked(theme == 'light')
        self.dark_theme_action.setChecked(theme == 'dark')
        if persist:
            self.settings.setValue("themeMode", theme)
        self.statusBar().showMessage("已切换为浅色模式" if theme == 'light' else "已切换为深色模式", 2500)
    
    
    def _open_file(self):
        """打开文件对话框"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if filepath:
            self.processing_tab.set_source_file(filepath)
    
    def _refresh_stats(self):
        """刷新统计信息"""
        self.processing_tab.refresh_category_stats()
    
    def _show_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于",
            f"<h3>{APP_NAME}</h3>"
            f"版本：{APP_VERSION}<br>"
            f"作者：{AUTHOR}<br>"
            f"纯文件处理模式 - 不依赖数据库<br>"
            f"© 2026 陆山君"
        )
    
    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
    
    def dropEvent(self, event: QDropEvent) -> None:
        """拖拽放下事件"""
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if os.path.isfile(path):
                self.processing_tab.on_drop_file(path)
    
    def closeEvent(self, event: QCloseEvent) -> None:
        """关闭事件"""
        workers = self.processing_tab._workers | self.verify_tab._workers
        running = [worker for worker in workers if worker.isRunning()]
        if running:
            self._closing = True
            for worker in running:
                worker.requestInterruption()
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        self.save_settings()
        event.accept()


class ProcessingTab(QWidget):
    """处理标签页"""
    
    def __init__(self, parent: Optional[QWidget]=None) -> None:
        super().__init__()
        self.parent = parent
        self.process_thread = None
        self.normalize_thread = None
        self.category_thread = None
        self._workers = set()
        self._status_threads = {}
        self._category_refresh_pending = False
        self._library_locks = {key: False for key in CLASSIFY_FILES.values()}
        self.library_state_labels = {}
        self._library_sources = {}
        self._init_ui()
        # External libraries live beside the script (or the packaged executable).
        application_path = sys.executable if getattr(sys, 'frozen', False) else __file__
        startup_directory = os.path.dirname(os.path.abspath(application_path))
        self._library_program_directory = startup_directory
        self._load_library_choices()
        recovery = RecoveryThread([startup_directory, os.getcwd()])
        self._track_worker(recovery)
        recovery.log.connect(self._log, Qt.ConnectionType.QueuedConnection)
        recovery.start()
        self._auto_detect_category_directory(startup_directory, '程序目录')
        self.source_path_edit.editingFinished.connect(self._source_library_scan)

    def _track_worker(self, worker):
        worker.setParent(self)
        worker.input_encoding = self.encoding_combo.currentData()
        self._workers.add(worker)
        worker.finished.connect(self._worker_finished, Qt.ConnectionType.QueuedConnection)

    @pyqtSlot()
    def _worker_finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        if worker is self.process_thread:
            self.process_thread = None
        if worker is self.normalize_thread:
            self.normalize_thread = None
        if worker is self.category_thread:
            self.category_thread = None
            if self._category_refresh_pending:
                self._category_refresh_pending = False
                if not getattr(self.parent, '_closing', False):
                    self.refresh_category_stats()
        if isinstance(worker, FileStatusThread):
            self._status_threads.pop(worker.key, None)
            edit = self.category_files[worker.key]
            if edit.text() != worker.path and not getattr(self.parent, '_closing', False):
                self._update_cat_status(edit)
        busy = self.process_thread is not None or self.normalize_thread is not None
        self.process_btn.setEnabled(not busy)
        self.normalize_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy and not self.cancel_btn.property('cancellationPending'))
        worker.deleteLater()

    def _category_paths(self):
        return {name: self.category_files[filename].text()
                for name, filename in CLASSIFY_FILES.items()}
    
    def _init_ui(self):
        self.scroll_area, layout = StyleHelper.scroll_page(self)
        StyleHelper.configure_layout(layout, 'small', 'medium')
        title = QLabel('号码处理')
        title.setProperty('role', 'title')
        description = QLabel('选择源文件和分类库，再开始删除或规范化。')
        description.setProperty('role', 'caption')
        layout.addWidget(title)
        layout.addWidget(description)
        self.encoding_combo = QComboBox()
        for title, encoding in [('编码：自动检测', None), ('UTF-8', 'utf-8-sig'),
                                ('GB18030 / GBK', 'gb18030'), ('UTF-16（带 BOM）', 'utf-16')]:
            self.encoding_combo.addItem(title, encoding)
        self._create_source_section(layout)
        self._create_category_section(layout)
        self._create_button_section(layout)
        layout.addStretch()
        # Existing status widgets are constructed once; the status page will own them.
        self.running_page = QWidget(self)
        self.running_scroll, status_layout = StyleHelper.scroll_page(self.running_page)
        StyleHelper.configure_layout(status_layout, 'small', 'medium')
        title = QLabel('运行状态')
        title.setProperty('role', 'title')
        status_layout.addWidget(title)
        self._create_stats_section(status_layout)
        self._create_progress_section(status_layout)
        self._create_log_section(status_layout)
        status_layout.addStretch()
        self.source_path_edit.textChanged.connect(self._sync_source_presentation)
        for edit in self.category_files.values():
            edit.textChanged.connect(self._sync_source_presentation)
        self._sync_source_presentation()

    def _sync_source_presentation(self):
        """Presentation only: no file I/O or processing decisions."""
        source = self.source_path_edit.text().strip()
        self.source_status.setText('已选择 · ' + os.path.basename(source) if source else '未加载')
        self.source_status.setToolTip(source)
        if self.process_thread is not None or self.normalize_thread is not None:
            return
        self.progress_label.setVisible(bool(source))
        self.progress_bar.setVisible(bool(source))
        if source:
            self.stage_label.setText('准备就绪')
        elif any(edit.text().strip() for edit in self.category_files.values()):
            self.stage_label.setText('请选择源文件')
        else:
            self.stage_label.setText('未加载文件')
    
    def _input_row(self, label, edit, browse, status, form):
        row = QWidget()
        row.setProperty('role', 'content')
        path = QHBoxLayout(row)
        StyleHelper.configure_layout(path, 'small', 'none')
        browse.setFixedWidth(StyleHelper.SIZES['browse_width'])
        edit.setMinimumWidth(0)
        status.setProperty('role', 'statusHint')
        status.setWordWrap(False)
        status.setFixedWidth(StyleHelper.SIZES['action_width'])
        status.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        path.addWidget(edit, 1)
        path.addWidget(browse)
        path.addWidget(status)
        caption = QLabel(label)
        caption.setMinimumWidth(StyleHelper.SIZES['label_width'])
        form.addRow(caption, row)

    def _create_source_section(self, layout):
        self.input_card = QFrame()
        self.input_card.setProperty('role', 'card')
        self.input_card_layout = QVBoxLayout(self.input_card)
        StyleHelper.configure_layout(self.input_card_layout, 'small', 'medium')
        title = QLabel('输入文件')
        title.setProperty('role', 'subtitle')
        heading = QHBoxLayout()
        StyleHelper.configure_layout(heading, 'small', 'none')
        heading.addWidget(title)
        heading.addStretch()
        self.input_card_layout.addLayout(heading)
        self.input_form = QFormLayout()
        StyleHelper.configure_layout(self.input_form, 'small', 'none')
        self.input_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.input_form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.input_form.addRow(QLabel('编码'), self.encoding_combo)
        self.source_path_edit = QLineEdit()
        self.source_path_edit.setPlaceholderText('选择或拖入源文件')
        self.source_status = QLabel('未加载')
        browse_btn = QPushButton('浏览')
        StyleHelper.configure_button(browse_btn)
        browse_btn.clicked.connect(self.select_source_file)
        self._input_row('源文件', self.source_path_edit, browse_btn,
                        self.source_status, self.input_form)
        self.input_card_layout.addLayout(self.input_form)
        layout.addWidget(self.input_card)

    def _create_category_section(self, layout):
        self.category_files = {}
        self.category_labels = {}
        names = {'invalid': '无效号码', 'valid': '有效号码', 'opened': '已开户号码'}
        # The three library kinds share the input form; 无效号码 is the fourth row,
        # directly below 已开户号码, with no collapsible wrapper or toggle signal.
        for cat_name in ('valid', 'opened', 'invalid'):
            cat_key = CLASSIFY_FILES[cat_name]
            path_edit = QLineEdit()
            path_edit.setPlaceholderText(cat_key)
            status = QLabel('未加载')
            # Existing statistics labels and their worker connections stay intact.
            # Discovery state is separate so asynchronous counts cannot overwrite it.
            status.setParent(self.input_card)
            status.hide()
            discovery_status = QLabel('未找到请手动选择')
            self.library_state_labels[cat_key] = discovery_status
            browse_btn = QPushButton('浏览')
            StyleHelper.configure_button(browse_btn)
            browse_btn.clicked.connect(lambda checked, p=path_edit: self._select_cat_file(p))
            self._input_row(names[cat_name], path_edit, browse_btn, discovery_status, self.input_form)
            self.category_files[cat_key] = path_edit
            self.category_labels[cat_key] = status
            path_edit.textEdited.connect(lambda text, key=cat_key: self._manual_library_edited(key))
            path_edit.editingFinished.connect(lambda key=cat_key: self._commit_manual_library(key))

    def _create_button_section(self, layout):
        """创建按钮区域"""
        button_layout = QGridLayout()
        StyleHelper.configure_layout(button_layout, 'medium', 'none')
        
        self.process_btn = QPushButton("开始删除")
        StyleHelper.configure_button(self.process_btn, 'primary')
        self.process_btn.setMinimumWidth(StyleHelper.SIZES['action_width'])
        self.process_btn.setDefault(True)
        self.process_btn.clicked.connect(self.start_processing)
        
        self.normalize_btn = QPushButton("规范化文件")
        StyleHelper.configure_button(self.normalize_btn)
        self.normalize_btn.clicked.connect(self.start_normalize)
        
        self.browse_all_btn = QPushButton("浏览所有")
        StyleHelper.configure_button(self.browse_all_btn)
        self.browse_all_btn.clicked.connect(self.browse_all_files)
        
        button_layout.addWidget(self.process_btn, 0, 0, 1, 4)
        button_layout.addWidget(self.normalize_btn, 1, 0)
        button_layout.addWidget(self.browse_all_btn, 1, 1)
        button_layout.setColumnStretch(0, 1)
        button_layout.setColumnStretch(1, 1)
        
        self.cancel_btn = QPushButton("取消任务")
        StyleHelper.configure_button(self.cancel_btn, 'danger')
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self.cancel_tasks)
        self.rescan_btn = QPushButton('重新扫描')
        StyleHelper.configure_button(self.rescan_btn, 'secondary')
        self.rescan_btn.clicked.connect(self.rescan_library_files)
        button_layout.addWidget(self.rescan_btn, 1, 2)
        button_layout.addWidget(self.cancel_btn, 1, 3)
        button_layout.setColumnStretch(2, 1)
        button_layout.setColumnStretch(3, 1)
        layout.addLayout(button_layout)
    
    def cancel_tasks(self):
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.setProperty('cancellationPending', True)
        for worker in tuple(self._workers):
            if worker.isRunning():
                worker.requestInterruption()
        self.stage_label.setText('正在取消：当前 NumPy 操作完成后响应')

    def _create_progress_section(self, layout):
        """创建进度区域（普通区块，不额外包卡片）"""
        section = QWidget()
        progress_layout = QVBoxLayout(section)
        StyleHelper.configure_layout(progress_layout, 'small', 'none')
        
        self.stage_label = QLabel("准备就绪")
        self.stage_label.setProperty('role', 'subtitle')
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(StyleHelper.SIZES['progress_height'])
        self.progress_label = QLabel("0%")
        self.progress_label.setProperty('role', 'caption')
        self.progress_bar.valueChanged.connect(lambda value: self.progress_label.setText(f"{value}%"))
        progress_heading = QHBoxLayout()
        StyleHelper.configure_layout(progress_heading, 'medium', 'none')
        progress_heading.addWidget(self.stage_label)
        progress_heading.addStretch()
        progress_heading.addWidget(self.progress_label)
        
        progress_layout.addLayout(progress_heading)
        progress_layout.addWidget(self.progress_bar)
        
        hint = QLabel('任务的进度和状态会在这里更新。')
        hint.setProperty('role', 'caption')
        progress_layout.addWidget(hint)
        layout.addWidget(section)
    
    def _create_log_section(self, layout):
        group = QFrame()
        group.setProperty('role', 'card')
        # Fixed height: long output scrolls inside the log area instead of stretching the page.
        group.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        group_layout = QVBoxLayout(group)
        StyleHelper.configure_layout(group_layout, 'small', 'medium')
        title = QLabel('操作日志')
        title.setProperty('role', 'subtitle')
        clear_btn = QPushButton('清空日志')
        StyleHelper.configure_button(clear_btn, 'secondary')
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(StyleHelper.SIZES['log_height'])
        self.log_text.setPlaceholderText('操作记录将在这里显示。')
        clear_btn.clicked.connect(self.log_text.clear)
        toolbar = QHBoxLayout()
        StyleHelper.configure_layout(toolbar, 'small', 'none')
        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(clear_btn)
        group_layout.addLayout(toolbar)
        group_layout.addWidget(self.log_text)
        layout.addWidget(group)

    def _create_stats_section(self, layout):
        """创建统计区域"""
        section = QVBoxLayout()
        StyleHelper.configure_layout(section, 'medium', 'none')
        self.stats_label = QLabel("分类库大小：未加载")
        self.stats_label.setProperty('role', 'caption')
        self.stats_label.setWordWrap(True)
        section.addWidget(self.stats_label)
        cards = QHBoxLayout()
        StyleHelper.configure_layout(cards, 'medium', 'none')
        cards.setSpacing(StyleHelper.SPACING['medium'])
        self.category_metrics = {}
        tones = {'invalid': 'danger', 'valid': 'success', 'opened': 'neutral'}
        for key, label in (('invalid', '无效号码'), ('valid', '有效号码'), ('opened', '已开户号码')):
            card, value = StyleHelper.metric_card(label, tones[key])
            caption = card.layout().takeAt(card.layout().count() - 1).widget()
            card.layout().insertWidget(0, caption)
            # A display label shares the existing metric's parent; updates are not reconnected.
            status = QLabel('分类库大小见上方')
            status.setProperty('role', 'statusHint')
            card.layout().addWidget(status)
            self.category_metrics[key] = value
            cards.addWidget(card, 1)
        section.addLayout(cards)
        layout.addLayout(section)
    
    # ------------------ 事件处理 ------------------
    
    def select_source_file(self) -> None:
        """选择源文件"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择源文件", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if filepath:
            self.set_source_file(filepath)
    
    def _select_cat_file(self, edit_field):
        """选择分类库文件"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择分类库", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if filepath:
            edit_field.setText(filepath)
            for key, edit in self.category_files.items():
                if edit is edit_field:
                    self._manual_library_edited(key)
                    self._save_library_choice(key, sync=True)
                    break
            self._update_cat_status(edit_field)
    
    def _update_cat_status(self, edit_field):
        """更新分类库状态"""
        path = edit_field.text()
        for key, edit in self.category_files.items():
            if edit == edit_field:
                if key in self._status_threads:
                    return
                if not path or not os.path.isfile(path):
                    self.category_labels[key].setText("✗ 未找到")
                    return
                self.category_labels[key].setText("统计中...")
                worker = FileStatusThread(path, key)
                self._status_threads[key] = worker
                self._track_worker(worker)
                worker.counted.connect(self._file_status_done, Qt.ConnectionType.QueuedConnection)
                worker.error.connect(self._file_status_error, Qt.ConnectionType.QueuedConnection)
                worker.start()
                break

    @pyqtSlot(str, object, object)
    def _file_status_done(self, path, size, count):
        worker = self.sender()
        if self.category_files[worker.key].text() == path:
            self.category_labels[worker.key].setText(f"✓ {size / 1024:.1f} KB | {count:,} 行")

    @pyqtSlot(str)
    def _file_status_error(self, message):
        worker = self.sender()
        if self.category_files[worker.key].text() == worker.path:
            self.category_labels[worker.key].setText("✗ 读取失败")
            self._log(message)
    
    def set_source_file(self, path: str) -> None:
        """设置源文件路径"""
        self.source_path_edit.setText(path)
        self.auto_detect_category_files(path)

    def auto_detect_category_files(self, source_path: str) -> None:
        """Fill exact standard filenames beside a confirmed source; keep missing entries."""
        if not source_path or not os.path.isfile(source_path):
            return
        self._auto_detect_category_directory(os.path.dirname(os.path.abspath(source_path)),
                                             '源文件目录')

    def _auto_detect_category_directory(self, directory: str, role: str = '目录') -> None:
        self._scan_library_choices([(directory, role)], replace_unlocked=False)

    def _library_log(self, message, level='info'):
        getattr(logger, level)(message)
        self._log(message)

    def _load_library_choices(self):
        self.library_settings = self.parent.settings
        for kind, key in CLASSIFY_FILES.items():
            locked = self.library_settings.value(f'libraries/{kind}/locked', False, type=bool)
            self._library_locks[key] = locked
            if locked:
                path = self.library_settings.value(f'libraries/{kind}/path', '', type=str)
                self.category_files[key].setText(path)
                self.library_state_labels[key].setText('已手动指定')
                self.library_state_labels[key].setToolTip('已锁定，自动扫描跳过；' + (path or '手动清空'))
                self._library_log(f'{kind}：已恢复手动锁定；{path or "手动清空"}')
                if path:
                    if not os.path.isfile(path):
                        self._library_log(f'{kind}：手动路径不存在或不是文件，保留锁定，请手动重新选择：{path}', 'warning')
                    self._update_cat_status(self.category_files[key])
        if self.library_settings.status() != QSettings.Status.NoError:
            self._library_log('库配置读取失败，请检查用户 AppData 设置文件；现有可读取设置仍保留。', 'warning')

    def _save_library_choice(self, key, sync=False):
        kind = next(kind for kind, filename in CLASSIFY_FILES.items() if filename == key)
        self.library_settings.setValue(f'libraries/{kind}/locked', self._library_locks[key])
        self.library_settings.setValue(f'libraries/{kind}/path', self.category_files[key].text())
        if sync:
            self.library_settings.sync()
            if self.library_settings.status() != QSettings.Status.NoError:
                self._library_log('手动锁定配置保存失败；本次会话仍保持锁定，请检查 AppData 写入权限。', 'warning')

    def _manual_library_edited(self, key):
        was_locked = self._library_locks[key]
        self._library_locks[key] = True
        self.library_state_labels[key].setText('已手动指定')
        self.library_state_labels[key].setToolTip('已锁定，自动扫描跳过；' +
                                                 (self.category_files[key].text() or '手动清空'))
        # setValue is buffered; editingFinished/browse commits the atomic INI write.
        self._save_library_choice(key)
        if not was_locked:
            self._library_log(f'{key}：用户手动指定，已锁定；后续扫描不覆盖。')

    def _commit_manual_library(self, key):
        if self._library_locks[key]:
            self._save_library_choice(key, sync=True)
            self._library_log(f'{key}：保存手动路径：{self.category_files[key].text() or "手动清空"}')
            self._update_cat_status(self.category_files[key])

    def _source_library_scan(self):
        self.auto_detect_category_files(self.source_path_edit.text())

    def rescan_library_files(self):
        directories = [(self._library_program_directory, '程序目录')]
        source = self.source_path_edit.text().strip()
        if source:
            directories.append((os.path.dirname(os.path.abspath(source)), '源文件目录'))
        else:
            self._library_log('源文件未选择：重新扫描只扫描程序目录。')
        self._scan_library_choices(directories, replace_unlocked=True)

    def _scan_library_choices(self, directories, replace_unlocked=False):
        started = time.perf_counter()
        ranked = [(os.path.abspath(os.fspath(path)), role) for path, role in directories]
        self._library_log('扫描目录（按优先级，靠前者优先）：' + '；'.join(
            f'{index}) {role} {path}' for index, (path, role) in enumerate(ranked, start=1)))
        self._library_log('多候选规则：程序目录优先于源文件目录；同目录内精确名优先于关键词；'
                          '同级按修改时间新的优先，同时间按完整路径排序。')
        result = scan_library_directories([path for path, _ in ranked])
        for message in result.messages:
            self._library_log(message.text, message.level)
        changed = False
        for kind, key in CLASSIFY_FILES.items():
            edit = self.category_files[key]
            if self._library_locks[key]:
                self._library_log(f'{kind}：已手动指定，跳过锁定行。')
                continue
            if edit.text().strip() and not replace_unlocked:
                self._library_log(f'{kind}：已有自动发现路径，补扫不覆盖：{edit.text()}')
                continue
            match = result.matches[kind]
            path = match.path if match else ''
            changed = changed or edit.text() != path
            edit.setText(path)
            label = self.library_state_labels[key]
            label.setText('自动发现' if match else '未找到请手动选择')
            label.setToolTip(match.source + '；' + match.path if match else '未找到可读 TXT 库文件，请手动选择。')
            if match:
                self._library_sources[key] = match.source
                self._update_cat_status(edit)
                self._library_log(f'{kind}：自动发现并填入 {path}；来源：{match.source}')
            else:
                self._library_sources.pop(key, None)
        if changed:
            self.refresh_category_stats()
        self._library_log(f'库文件识别结束：{(time.perf_counter() - started) * 1000:.2f} ms；仅扫描目录直接文件。')
    
    def on_drop_file(self, path: str) -> None:
        """拖拽文件处理"""
        if os.path.isfile(path):
            self.set_source_file(path)
            self.parent.statusBar().showMessage(f"已加载：{os.path.basename(path)}")
    
    def browse_all_files(self) -> None:
        """浏览所有文件"""
        self.select_source_file()
        for edit in self.category_files.values():
            if not edit.text():
                self._select_cat_file(edit)
                break
    
    def refresh_category_stats(self) -> None:
        """刷新分类库统计"""
        if self.category_thread is not None:
            self._category_refresh_pending = True
            return
        paths = self._category_paths()
        self.category_thread = CategoryCountThread(paths)
        self._track_worker(self.category_thread)
        self.category_thread.counted.connect(self._update_category_stats, Qt.ConnectionType.QueuedConnection)
        self.category_thread.error.connect(self._log, Qt.ConnectionType.QueuedConnection)
        self.category_thread.log.connect(self._log, Qt.ConnectionType.QueuedConnection)
        self.category_thread.start()
    
    @pyqtSlot(dict, dict, dict)
    def _update_category_stats(self, invalid, valid, opened):
        """更新分类库统计"""
        worker = self.sender()
        if isinstance(worker, CategoryCountThread) and worker.paths != self._category_paths():
            return
        for key, counts in (('invalid', invalid), ('valid', valid), ('opened', opened)):
            self.category_metrics[key].setText(f"{counts[key]:,}")
        stats = []
        if invalid['invalid']:
            stats.append(f"无效号码库：{invalid['invalid']:,} 个")
        if valid['valid']:
            stats.append(f"有效号码库：{valid['valid']:,} 个")
        if opened['opened']:
            stats.append(f"已开户库：{opened['opened']:,} 个")
        
        self.stats_label.setText("\n".join(stats) if stats else "分类库大小：未加载")
    
    def start_processing(self) -> None:
        """开始处理"""
        if self.process_thread is not None or self.normalize_thread is not None:
            return
        source_path = self.source_path_edit.text()
        
        if not source_path or not os.path.exists(source_path):
            QMessageBox.warning(self, "警告", "请首先选择源文件！")
            return
        
        classify_paths = [edit.text() for edit in self.category_files.values()]
        
        output_dir = os.path.dirname(source_path) or '.'
        output_path = os.path.join(output_dir, f"保留号码_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        removed_path = os.path.join(output_dir, f"剔除号码_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        
        self.process_thread = ProcessThread(source_path, output_path, removed_path, classify_paths)
        self._track_worker(self.process_thread)
        self.cancel_btn.setProperty('cancellationPending', False)
        self.cancel_btn.setEnabled(True)
        self.process_btn.setEnabled(False)
        self.normalize_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.process_thread.progress.connect(self.progress_bar.setValue, Qt.ConnectionType.QueuedConnection)
        self.process_thread.stage.connect(self.stage_label.setText, Qt.ConnectionType.QueuedConnection)
        self.process_thread.log.connect(self._log, Qt.ConnectionType.QueuedConnection)
        self.process_thread.done.connect(self._on_processing_done, Qt.ConnectionType.QueuedConnection)
        self.process_thread.error.connect(self._on_processing_error, Qt.ConnectionType.QueuedConnection)
        self.process_thread.start()
        # Switch once at task start; completion never changes the selected page.
        self.parent.navigation.setCurrentRow(2)
    
    def start_normalize(self) -> None:
        """开始规范化"""
        if self.process_thread is not None or self.normalize_thread is not None:
            return
        source_path = self.source_path_edit.text()
        if not source_path or not os.path.exists(source_path):
            QMessageBox.warning(self, "警告", "请选择源文件！")
            return
        
        output_dir = os.path.dirname(source_path) or '.'
        basename = os.path.splitext(os.path.basename(source_path))[0]
        output_path = os.path.join(output_dir, f"{basename}_normalized.txt")
        
        self.normalize_thread = NormalizeThread(source_path, output_path)
        self._track_worker(self.normalize_thread)
        self.cancel_btn.setProperty('cancellationPending', False)
        self.cancel_btn.setEnabled(True)
        self.process_btn.setEnabled(False)
        self.normalize_btn.setEnabled(False)
        self.progress_bar.setValue(0)
        self.normalize_thread.progress.connect(self.progress_bar.setValue, Qt.ConnectionType.QueuedConnection)
        self.normalize_thread.stage.connect(self.stage_label.setText, Qt.ConnectionType.QueuedConnection)
        self.normalize_thread.log.connect(self._log, Qt.ConnectionType.QueuedConnection)
        self.normalize_thread.done.connect(self._on_normalize_done, Qt.ConnectionType.QueuedConnection)
        self.normalize_thread.error.connect(self._on_processing_error, Qt.ConnectionType.QueuedConnection)
        self.normalize_thread.start()
        # Switch once at task start; completion never changes the selected page.
        self.parent.navigation.setCurrentRow(2)
    
    @pyqtSlot(str)
    def _log(self, msg):
        """记录日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {msg}")
    
    @pyqtSlot(dict)
    def _on_processing_done(self, result):
        """处理完成回调"""
        self.progress_bar.setValue(100)
        
        summary = (
            f"\n{'='*50}\n"
            f"✅ 处理完成！\n"
            f"{'='*50}\n"
            f"总记录数：{result['total']:,}\n"
            f"保留号码：{result['kept']:,}\n"
            f"剔除号码：{result['removed']:,}\n"
            f"成功率：{result['kept']/result['total']*100 if result['total'] else 0:.2f}%\n"
            f"处理时间：{result['elapsed']:.2f}秒\n"
            f"{'='*50}\n"
            f"输出文件:\n"
            f"  ✓ 保留：{result['output']}\n"
            f"  ✓ 剔除：{result['removed_output']}"
        )
        
        self._log(summary)
        
        if getattr(self.parent, '_closing', False):
            return
        QMessageBox.information(
            self, "处理完成",
            f"成功保留 {result['kept']:,} 个号码\n"
            f"共剔除 {result['removed']:,} 个号码\n"
            f"总耗时：{result['elapsed']:.2f}秒"
        )
    
    @pyqtSlot(dict)
    def _on_normalize_done(self, result):
        """规范化完成回调"""
        self.progress_bar.setValue(100)
        skipped = result['total_in'] - result['total_out']
        
        summary = (
            f"\n{'='*50}\n"
            f"✅ 规范化完成！\n"
            f"{'='*50}\n"
            f"输入行数：{result['total_in']:,}\n"
            f"输出号码：{result['total_out']:,}\n"
            f"跳过记录：{skipped:,}\n"
            f"处理时间：{result['elapsed']:.2f}秒\n"
            f"{'='*50}\n"
            f"输出文件：{result['output']}"
        )
        
        self._log(summary)
        if skipped and not getattr(self.parent, '_closing', False):
            QMessageBox.warning(
                self, "规范化完成",
                f"已输出 {result['total_out']:,} 个号码，跳过 {skipped:,} 条记录。\n"
                "仅接受 10 位本地号码或以 1 开头的 11 位号码。原文件未更改。"
            )
    
    @pyqtSlot(str)
    def _on_processing_error(self, error_msg):
        """处理错误"""
        self._log(f"❌ 错误：{error_msg}")
        if getattr(self.parent, '_closing', False):
            return
        QMessageBox.critical(self, "错误", f"处理失败：{error_msg}")


class EmptyResultsModel(QAbstractTableModel):
    """Display-only result placeholder: the existing worker publishes statistics only."""
    def rowCount(self, parent=None):
        return 0

    def columnCount(self, parent=None):
        return 3

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            return ('号码', '分类', '原因')[section]
        return None


class VerifyExportTab(QWidget):
    """验证与导出标签页"""
    
    def __init__(self, parent: Optional[QWidget]=None) -> None:
        super().__init__()
        self.parent = parent
        self._workers = set()
        self._task_thread = None
        self._init_ui()
        self.cancel_btn = QPushButton('取消分析 / 导出（等待当前操作完成）')
        self.cancel_btn.clicked.connect(self._cancel_task)
        self.page_layout.insertWidget(self.page_layout.count() - 1, self.cancel_btn)

    def _cancel_task(self):
        if self._task_thread is not None:
            self._task_thread.requestInterruption()
    
    def _init_ui(self):
        self.scroll_area, layout = StyleHelper.scroll_page(self)
        StyleHelper.configure_layout(layout, 'small', 'medium')
        self.page_layout = layout
        title = QLabel('验证与导出')
        title.setProperty('role', 'title')
        layout.addWidget(title)
        self.verify_panel = VerifyPanel()
        layout.addWidget(self.verify_panel)
        self._create_file_section(self.verify_panel.layout())
        self.encoding_combo = QComboBox()
        for title, encoding in [('编码：自动检测', None), ('UTF-8', 'utf-8-sig'),
                                ('GB18030 / GBK', 'gb18030'), ('UTF-16（带 BOM）', 'utf-16')]:
            self.encoding_combo.addItem(title, encoding)
        encoding_form = QFormLayout()
        StyleHelper.configure_layout(encoding_form, 'small', 'none')
        encoding_form.addRow('编码', self.encoding_combo)
        self.verify_panel.layout().addLayout(encoding_form)

        results_card = QFrame()
        results_card.setProperty('role', 'card')
        results = QVBoxLayout(results_card)
        StyleHelper.configure_layout(results, 'small', 'medium')
        label = QLabel('结果列表')
        label.setProperty('role', 'caption')
        results.addWidget(label)
        self.results_table = QTableView()
        self.results_table.setProperty('role', 'card')
        self.results_table.viewport().setProperty('role', 'content')
        self.results_model = EmptyResultsModel(self.results_table)
        self.results_table.setModel(self.results_model)
        self.results_table.setMinimumHeight(StyleHelper.SIZES['log_height'])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.results_table.verticalHeader().hide()
        self.results_table.setShowGrid(False)
        self.results_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        results.addWidget(self.results_table)
        hint = QLabel('暂无号码明细。当前分析提供汇总统计，可在上方查看。')
        hint.setProperty('role', 'caption')
        hint.setWordWrap(True)
        results.addWidget(hint)
        layout.addWidget(results_card)

        export_card = QFrame()
        export_card.setProperty('role', 'card')
        options = QVBoxLayout(export_card)
        StyleHelper.configure_layout(options, 'small', 'medium')
        label = QLabel('导出')
        label.setProperty('role', 'caption')
        options.addWidget(label)
        form = QFormLayout()
        StyleHelper.configure_layout(form, 'small', 'none')
        self.export_target_hint = QLabel('点击导出后选择保存位置')
        self.export_target_hint.setProperty('role', 'caption')
        form.addRow('目标文件', self.export_target_hint)
        self.export_scope = QComboBox()
        self.export_scope.addItem('整个输入文件')
        self.export_scope.setEnabled(False)
        form.addRow('导出范围', self.export_scope)
        self.format_combo = QComboBox()
        self.format_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        for name, func in [('原始格式', None), ('美国标准格式', FormatExporter.format_us_standard),
                           ('间隔分隔格式', FormatExporter.format_digital_separated),
                           ('E.164 格式', FormatExporter.format_e164)]:
            self.format_combo.addItem(name, func)
        form.addRow('号码格式', self.format_combo)
        options.addLayout(form)
        export_btn = QPushButton('导出当前号码')
        StyleHelper.configure_button(export_btn, 'primary')
        export_btn.setDefault(True)
        export_btn.clicked.connect(self.export_current_numbers)
        options.addWidget(export_btn)
        help_label = QLabel('按当前格式导出整个输入文件；保存位置在导出时选择。')
        help_label.setProperty('role', 'caption')
        help_label.setWordWrap(True)
        options.addWidget(help_label)
        layout.addWidget(export_card)
        layout.addStretch()

    def _create_file_section(self, layout):
        """创建文件选择区域"""
        group = QFrame()
        group.setProperty('role', 'content')
        group_layout = QHBoxLayout()
        StyleHelper.configure_layout(group_layout, 'small', 'none')
        
        self.file_path_edit = QLineEdit()
        self.file_path_edit.setPlaceholderText("选择要分析的文件")
        
        browse_btn = QPushButton("浏览")
        StyleHelper.configure_button(browse_btn)
        browse_btn.setFixedWidth(StyleHelper.SIZES['browse_width'])
        browse_btn.clicked.connect(self.select_file)
        
        analyze_btn = QPushButton("分析质量")
        StyleHelper.configure_button(analyze_btn, 'primary')
        analyze_btn.clicked.connect(self.analyze_file)
        
        group_layout.addWidget(self.file_path_edit)
        group_layout.addWidget(browse_btn)
        group_layout.addWidget(analyze_btn)
        
        group.setLayout(group_layout)
        layout.addWidget(group)
    
    def select_file(self) -> None:
        """选择文件"""
        filepath, _ = QFileDialog.getOpenFileName(
            self, "选择文件", "", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if filepath:
            self.file_path_edit.setText(filepath)
    
    def analyze_file(self) -> None:
        """分析文件质量"""
        if self._task_thread is not None:
            return
        filepath = self.file_path_edit.text()
        if not filepath or not os.path.exists(filepath):
            QMessageBox.warning(self, "警告", "请选择一个文件！")
            return
        
        self._start_task(VerifyExportThread('analyze', filepath))

    def _start_task(self, worker):
        worker.setParent(self)
        self._task_thread = worker
        worker.input_encoding = self.encoding_combo.currentData()
        self._workers.add(worker)
        worker.done.connect(self._on_task_done, Qt.ConnectionType.QueuedConnection)
        worker.error.connect(self._on_task_error, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(self._task_finished, Qt.ConnectionType.QueuedConnection)
        worker.start()
        # Switch once at task start; completion never changes the selected page.
        self.parent.navigation.setCurrentRow(2)

    @pyqtSlot()
    def _task_finished(self):
        worker = self.sender()
        self._workers.discard(worker)
        if worker is self._task_thread:
            self._task_thread = None
        worker.deleteLater()

    @pyqtSlot(str)
    def _on_task_error(self, message):
        if not getattr(self.parent, '_closing', False):
            QMessageBox.critical(self, "错误", message)

    @pyqtSlot(dict)
    def _on_task_done(self, result):
        if getattr(self.parent, '_closing', False):
            return
        if result['mode'] == 'cancelled':
            QMessageBox.information(self, '取消', '任务已取消')
            return
        if result['mode'] == 'export':
            QMessageBox.information(self, "完成", f"已导出 {result['count']:,} 个号码")
            return
        stats = result['statistics']
        self.verify_panel._show_statistics(stats)
        
        msg = (
            f"{'='*50}\n"
            f"📊 文件质量分析报告\n"
            f"{'='*50}\n"
            f"文件：{stats['file']}\n"
            f"总行数：{stats['total_lines']:,}\n"
            f"有效号码：{stats['valid_numbers']:,}\n"
            f"格式错误：{stats['invalid_format']:,}\n"
            f"NPANXX 错误：{stats['invalid_npanxx']:,}\n"
            f"重复号码：{stats['duplicates']:,}\n"
            f"有效率：{stats['validity_rate']:.2f}%\n"
            f"{'='*50}"
        )
        
        QMessageBox.information(self, "质量分析", msg)
    
    def export_current_numbers(self) -> None:
        """导出当前号码"""
        if self._task_thread is not None:
            return
        filepath = self.file_path_edit.text()
        if not filepath or not os.path.exists(filepath):
            QMessageBox.warning(self, "警告", "请选择一个文件！")
            return
        
        format_func = self.format_combo.currentData()
        
        out_path, _ = QFileDialog.getSaveFileName(
            self, "保存文件", "output.txt", "文本文件 (*.txt)"
        )
        
        if out_path:
            self._start_task(VerifyExportThread('export', filepath, out_path, format_func))


class VerifyPanel(QFrame):
    """验证面板"""
    
    def __init__(self) -> None:
        super().__init__()
        self.setProperty('role', 'metricCard')
        layout = QVBoxLayout(self)
        StyleHelper.configure_layout(layout, 'small', 'medium')
        title = QLabel('校验汇总')
        title.setProperty('role', 'caption')
        layout.addWidget(title)
        self.metrics = {}
        cards = QHBoxLayout()
        StyleHelper.configure_layout(cards, 'small', 'none')
        for key, label, tone in (('total_lines', '总记录', None),
                                 ('valid_numbers', '校验通过', 'success'),
                                 ('validity_rate', '有效率', None)):
            card, value = StyleHelper.metric_card(label, tone)
            value.setText('—')
            self.metrics[key] = value
            cards.addWidget(card, 1)
        layout.addLayout(cards)
        self.verify_text = QTextEdit()
        self.verify_text.setReadOnly(True)
        self.verify_text.setFixedHeight(StyleHelper.SIZES['input_height'] * 3)
        self.verify_text.setPlaceholderText('分析后显示格式错误、NPA/NXX 错误和重复号码等统计。')
        layout.addWidget(self.verify_text)
        note = QLabel('去重后总数：现有统计未单独提供。')
        note.setProperty('role', 'caption')
        layout.addWidget(note)

    def _show_statistics(self, stats: Dict[str, Any]) -> None:
        for key, value in self.metrics.items():
            value.setText(f"{stats[key]:.2f}%" if key == 'validity_rate' else f"{stats[key]:,}")
        self.verify_text.setPlainText(
            f"文件：{stats['file']}\n\n"
            f"总记录数    {stats['total_lines']:,}\n"
            f"有效号码    {stats['valid_numbers']:,}\n"
            f"格式错误    {stats['invalid_format']:,}\n"
            f"NPANXX 错误 {stats['invalid_npanxx']:,}\n"
            f"重复号码    {stats['duplicates']:,}\n\n"
            f"有效率      {stats['validity_rate']:.2f}%"
        )


# ============================================================
# 命令行工具（Console Mode）
# ============================================================

class ConsoleTools:
    """命令行工具接口"""
    
    @staticmethod
    def show_help() -> None:
        """显示帮助信息"""
        print("""
=================================================================
号码工作台 - 命令行工具 (Console Tools)
用法：python customer_number_processor_single.py <命令> [参数]

命令列表:
=================================================================
1. process <源文件> <输出文件> <分类文件>...
   → 执行号码剔除过滤
   
2. normalize <输入文件> <输出文件>
   → 规范化号码文件格式
   
3. validate <文件路径>
   → 验证文件质量并生成报告
   
4. export <输入文件> <输出文件> [--format=standard|digital|e164]
   → 格式化导出号码
   
5. build
   → 构建单个 exe 可执行文件
   
6. recover <输出目录>
   → 恢复该目录遗留的输出事务

可选：--encoding utf-8|gb18030|utf-16（默认自动检测）

7. help
   → 显示此帮助信息
=================================================================
""")
    
    @staticmethod
    def process_command(args: Sequence[str]) -> int:
        """处理命令"""
        if len(args) < 3:
            print("错误：process 命令需要至少 3 个参数")
            print("用法：process <源文件> <输出文件> <分类文件>...")
            return 2
        
        source = args[0]
        output = args[1]
        classify_files = args[2:]
        
        if not os.path.exists(source):
            print(f"错误：源文件不存在 - {source}")
            return 2
        
        print(f"正在处理：{source}")
        _check_output_paths([source] + classify_files, [output])
        classify_set = load_classify_set(classify_files)
        print(f"加载了 {len(classify_set):,} 个分类号码")
        
        source_arr = parse_source_fast(source)
        print(f"解析得到 {len(source_arr):,} 个源号码")
        
        total, kept, removed, _ = filter_arrays(
            source_arr, classify_set, output
        )
        
        print(f"\n处理完成!")
        print(f"总记录数：{total:,}")
        print(f"保留号码：{kept:,}")
        print(f"剔除号码：{removed:,}")
        print(f"输出文件：{output}")
        return 0

    @staticmethod
    def normalize_command(args: Sequence[str]) -> int:
        """规范化命令"""
        if len(args) < 2:
            print("错误：normalize 命令需要 2 个参数")
            print("用法：normalize <输入文件> <输出文件>")
            return 2
        
        input_path, output_path = args[0], args[1]
        
        if not os.path.exists(input_path):
            print(f"错误：输入文件不存在 - {input_path}")
            return 2
        
        print(f"正在规范化：{input_path}")
        total_in, total_out = normalize_txt(input_path, output_path)
        
        print(f"\n规范化完成!")
        print(f"输入行数：{total_in:,}")
        print(f"输出号码：{total_out:,}")
        print(f"输出文件：{output_path}")
        return 0

    @staticmethod
    def validate_command(args: Sequence[str]) -> int:
        """验证命令"""
        if len(args) < 1:
            print("错误：validate 命令需要 1 个参数")
            print("用法：validate <文件路径>")
            return 2
        
        filepath = args[0]
        if not os.path.exists(filepath):
            print(f"错误：文件不存在 - {filepath}")
            return 2
        
        print(f"正在验证：{filepath}")
        validator = DataValidator()
        result = validator.check_file_quality(filepath)
        
        if 'error' in result:
            print(f"错误：{result['error']}")
            return 2
        
        stats = result['statistics']
        
        print(f"\n{'='*60}")
        print("📊 文件质量分析报告")
        print(f"{'='*60}")
        print(f"文件：{stats['file']}")
        print(f"总行数：{stats['total_lines']:,}")
        print(f"有效号码：{stats['valid_numbers']:,}")
        print(f"格式错误：{stats['invalid_format']:,}")
        print(f"NPANXX 错误：{stats['invalid_npanxx']:,}")
        print(f"重复号码：{stats['duplicates']:,}")
        print(f"有效率：{stats['validity_rate']:.2f}%")
        print(f"{'='*60}")
        return 0

    @staticmethod
    def export_command(args: Sequence[str]) -> int:
        """导出命令"""
        if len(args) < 2:
            print("错误：export 命令需要至少 2 个参数")
            print("用法：export <输入文件> <输出文件> [--format=standard|digital|e164]")
            return 2
        
        input_path = args[0]
        output_path = args[1]
        format_str = None
        
        for arg in args[2:]:
            if arg.startswith('--format='):
                format_str = arg.split('=')[1]
        
        format_map = {
            'standard': FormatExporter.format_us_standard,
            'digital': FormatExporter.format_digital_separated,
            'e164': FormatExporter.format_e164,
        }
        
        if format_str is not None and format_str not in format_map:
            return 2
        format_func = format_map.get(format_str)
        
        if not os.path.exists(input_path):
            print(f"错误：输入文件不存在 - {input_path}")
            return 2
        
        print(f"正在导出：{input_path}")
        _check_output_paths([input_path], [output_path])
        with open(input_path, 'r', encoding=_detect_encoding(input_path)) as f:
            numbers = (line.strip() for line in _iter_text_records(f) if line.strip())
            count = FormatExporter.export_to_txt(output_path, numbers, format_func)
        print(f"\n导出完成!")
        print(f"导出号码：{count:,}")
        print(f"输出文件：{output_path}")
        return 0

    @staticmethod
    def build_command() -> int:
        """构建命令"""
        print(f"🔨 开始构建可执行文件...\n")
        
        try:
            # Resolve the executable and pass arguments without a shell.
            executable = sys.executable
            if executable is None:
                raise FileNotFoundError('pyinstaller')
            cmd = [
                executable, '-m', 'PyInstaller',
                '--onefile',
                '--console',
                '--name', APP_NAME,
                '--clean',
                os.path.abspath(__file__),
            ]
            
            print(">".join(cmd))
            result = subprocess.run(cmd, stdin=subprocess.DEVNULL)
            
            if result.returncode == 0:
                print(f"\n✨ 构建成功!")
                print(f"📍 位置：dist/{APP_NAME}.exe")
            else:
                print(f"\n❌ 构建失败，请确保已安装 PyInstaller:")
                print("pip install pyinstaller")
                return 1
                
        except FileNotFoundError:
            print("❌ 错误：找不到 PyInstaller")
            print("请先安装：pip install pyinstaller")
            return 1

        return 0


# ============================================================
# 主入口
# ============================================================

def _dispatch_console() -> int:
    args = sys.argv[1:]
    if '--encoding' in args:
        index = args.index('--encoding')
        if index + 1 >= len(args):
            return 2
        os.environ['NUMBER_WORKBENCH_ENCODING'] = args[index + 1]
        del args[index:index + 2]
    if args and args[0].lower() == 'console':
        args = args[1:]
    if not args or args[0].lower() in ('help', '--help', '-h'):
        ConsoleTools.show_help()
        return 0
    if args[0].lower() == 'recover':
        if len(args) != 2:
            return 2
        with _output_directory_lock(os.path.abspath(args[1])):
            recover_output_transactions(os.path.abspath(args[1]))
        return 0
    commands = dict(process=ConsoleTools.process_command, normalize=ConsoleTools.normalize_command,
                    validate=ConsoleTools.validate_command, export=ConsoleTools.export_command,
                    build=lambda args: ConsoleTools.build_command())
    command = commands.get(args[0].lower())
    if command is None:
        print(f'未知命令：{args[0]}', file=sys.stderr)
        return 2
    return command(args[1:])


def main() -> None:
    """主函数"""
    if len(sys.argv) > 1:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None and hasattr(stream, 'reconfigure'):
                stream.reconfigure(errors='backslashreplace')
        try:
            status = _dispatch_console()
        except Exception as exc:
            logger.exception("命令执行失败")
            print(f"错误：{exc}", file=sys.stderr)
            sys.exit(1)
        sys.exit(status)
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setStyleSheet(StyleHelper.get_fluent_style())
    window = MainUI()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
