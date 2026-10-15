"""Read-only, deterministic, one-level library discovery; no Qt dependency.

English boundaries treat underscores/punctuation as separators, and letters/digits
as word characters. Chinese keywords allow adjacent Chinese text (有效号码库),
but are not embedded in Latin words. Each filename belongs to at most one kind.

Candidate priority: earlier scan directories win over later ones (the caller
passes the program directory before the source-file directory), then exact
standard names over keyword matches within a directory, then newer modification
time, then full path. Only direct entries of each directory are read.
"""
from dataclasses import dataclass
import os
from pathlib import Path
import re
from typing import Dict, Iterable, Optional, Tuple


KINDS = ('invalid', 'opened', 'valid')
EXACT_NAMES = {kind: kind + '_customers.txt' for kind in KINDS}
_KEYWORDS = {'invalid': ('invalid', '无效'), 'opened': ('opened', '已开户'),
             'valid': ('valid', '有效')}
_PATTERNS = {kind: tuple((word, re.compile(r'(?<![a-z0-9])' + re.escape(word) +
                                         r'(?![a-z0-9])', re.IGNORECASE))
                         for word in words) for kind, words in _KEYWORDS.items()}


@dataclass(frozen=True)
class NameMatch:
    kind: str
    exact: bool
    keyword: str


@dataclass(frozen=True)
class LibraryCandidate:
    path: str
    directory: str
    kind: str
    exact: bool
    keyword: str
    size: int
    modified_ns: int
    priority: int = 0

    @property
    def source(self) -> str:
        return f"{self.directory}；{'精确名' if self.exact else '整词关键词'}：{self.keyword}"


@dataclass(frozen=True)
class ScanMessage:
    level: str
    text: str


@dataclass(frozen=True)
class ScanResult:
    matches: Dict[str, Optional[LibraryCandidate]]
    messages: Tuple[ScanMessage, ...]


def match_library_name(filename: str) -> Optional[NameMatch]:
    """Pure filename matcher: .txt only, exact names first, fixed kind priority."""
    if Path(filename).suffix.casefold() != '.txt':
        return None
    folded = filename.casefold()
    for kind in KINDS:
        if folded == EXACT_NAMES[kind]:
            return NameMatch(kind, True, EXACT_NAMES[kind])
    stem = Path(filename).stem
    for kind in KINDS:
        for keyword, pattern in _PATTERNS[kind]:
            if pattern.search(stem):
                return NameMatch(kind, False, keyword)
    return None


def scan_library_directories(directories: Iterable[str]) -> ScanResult:
    """Scan direct entries only. Return paths, provenance, and all diagnostics.

    O(F + K log K) time, O(K) memory: F directory entries, K .txt candidates.
    Candidate readability uses one byte, never parses or loads a library.
    Filesystem errors are recorded, and scanning continues with other entries.

    Candidate priority, highest first: earlier directories in ``directories``
    (the caller passes the program directory before the source-file directory),
    then exact standard names before keyword matches inside one directory, then
    newer modification time, then full path. Only one level is scanned; no
    subdirectory is ever entered.
    """
    candidates = {kind: [] for kind in KINDS}
    messages = []
    seen = set()
    for priority, requested in enumerate(directories):
        directory = os.path.abspath(os.fspath(requested))
        identity = os.path.normcase(os.path.realpath(directory))
        if identity in seen:
            messages.append(ScanMessage('info', f'重复目录已跳过：{directory}'))
            continue
        seen.add(identity)
        count = 0
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    match = match_library_name(entry.name)
                    if match is None:
                        continue
                    try:
                        if not entry.is_file():
                            messages.append(ScanMessage('info', f'不是普通文件，已跳过：{entry.path}'))
                            continue
                        stat = entry.stat()
                        # Detect sharing violations/permissions before selecting a candidate.
                        with open(entry.path, 'rb') as stream:
                            stream.read(1)
                        candidate = LibraryCandidate(os.path.abspath(entry.path), directory,
                                                     match.kind, match.exact, match.keyword,
                                                     stat.st_size, stat.st_mtime_ns, priority)
                        candidates[match.kind].append(candidate)
                        count += 1
                    except OSError as exc:
                        messages.append(ScanMessage('warning',
                            f'候选不可读取（占用、权限或已消失），已跳过：{entry.path}；{exc}'))
        except FileNotFoundError as exc:
            messages.append(ScanMessage('warning', f'扫描目录不存在：{directory}；{exc}'))
        except PermissionError as exc:
            messages.append(ScanMessage('warning', f'无权限扫描目录：{directory}；{exc}'))
        except OSError as exc:
            messages.append(ScanMessage('warning', f'扫描目录失败：{directory}；{exc}'))
        messages.append(ScanMessage('info',
            f'一级扫描结束：{directory}；优先级 {priority + 1}；可读候选 {count} 个；不进入子目录'))

    selected = {}
    for kind in KINDS:
        ordered = sorted(candidates[kind], key=lambda item: (
            item.priority, not item.exact, -item.modified_ns,
            os.path.normcase(item.path), item.path))
        selected[kind] = ordered[0] if ordered else None
        if not ordered:
            messages.append(ScanMessage('info', f'{kind}：零匹配，未找到请手动选择'))
        elif len(ordered) > 1:
            details = '；'.join(f'{item.path}（目录优先级={item.priority + 1}，'
                               f'精确名={item.exact}，mtime_ns={item.modified_ns}）'
                               for item in ordered)
            messages.append(ScanMessage('info',
                f'{kind}：多匹配 {len(ordered)} 个；规则：目录优先级靠前者优先，其次精确名优先，'
                f'其次修改时间较新，同时间按完整路径排序；选择 {ordered[0].path}；全部候选：{details}'))
        else:
            messages.append(ScanMessage('info', f'{kind}：选择 {ordered[0].path}；来源：{ordered[0].source}'))
    return ScanResult(selected, tuple(messages))
