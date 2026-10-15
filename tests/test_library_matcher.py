import os
from unittest.mock import patch

import pytest

from library_matcher import match_library_name, scan_library_directories


@pytest.mark.parametrize('name,kind', [
    ('invalid_customers.txt', 'invalid'), ('backup-invalid-2026.txt', 'invalid'),
    ('opened_customers.txt', 'opened'), ('valid_customers.txt', 'valid'),
    ('VALID_CUSTOMERS.TXT', 'valid'), ('有效号码库.txt', 'valid'),
    ('无效号码库.txt', 'invalid'), ('已开户号码.txt', 'opened'),
    ('invalid_opened_valid.txt', 'invalid'), ('opened_valid.txt', 'opened')])
def test_kind_priority_and_boundaries(name, kind):
    assert match_library_name(name).kind == kind


@pytest.mark.parametrize('name', ['validation.json', 'validation.txt', 'valid.csv',
                                 'invalid.json', 'opened.dat', 'notvalid.txt',
                                 'valid123.txt', 'invalid_customers.txt.bak'])
def test_rejected_names(name):
    assert match_library_name(name) is None


@pytest.mark.parametrize('name', ['workbench.log', 'app.log', 'valid.log', 'invalid.log',
                                 'opened.log', 'number-workbench.log',
                                 'valid_customers.txt.log', '有效号码库.log'])
def test_log_files_are_never_matched(name):
    """日志文件即使含关键词也不得命中：匹配器只接受 .txt 后缀。"""
    assert match_library_name(name) is None


def test_zero_matches_and_no_recursion(tmp_path):
    child = tmp_path / 'child'
    child.mkdir()
    (child / 'valid_customers.txt').write_text('123')
    result = scan_library_directories([str(tmp_path)])
    assert all(candidate is None for candidate in result.matches.values())
    assert sum('零匹配' in message.text for message in result.messages) == 3


def test_directory_priority_then_exact_then_newest(tmp_path):
    program, source = tmp_path / 'program', tmp_path / 'source'
    program.mkdir(); source.mkdir()
    program_keyword = program / 'valid-backup.txt'
    source_exact = source / 'valid_customers.txt'
    for file in (program_keyword, source_exact):
        file.write_text('123')
    os.utime(program_keyword, ns=(100, 100))
    os.utime(source_exact, ns=(200, 200))
    # 程序目录优先于源文件目录：源目录候选虽是精确名且更新，仍选程序目录候选。
    result = scan_library_directories([str(program), str(source)])
    assert result.matches['valid'].path == str(program_keyword)
    assert not result.matches['valid'].exact
    assert any('多匹配' in message.text and '精确名优先' in message.text for message in result.messages)
    assert any('优先级' in message.text for message in result.messages)
    # 调换顺序后源目录成为优先目录，精确名生效。
    flipped = scan_library_directories([str(source), str(program)])
    assert flipped.matches['valid'].path == str(source_exact)
    assert flipped.matches['valid'].exact


def test_same_directory_exact_then_newest_and_stable_tie(tmp_path):
    first = tmp_path / 'a'
    first.mkdir()
    exact = first / 'valid_customers.txt'
    keyword = first / 'valid-backup.txt'
    exact.write_text('123'); keyword.write_text('123')
    os.utime(exact, ns=(100, 100)); os.utime(keyword, ns=(200, 200))
    result = scan_library_directories([str(first)])
    assert result.matches['valid'].path == str(exact)  # 同目录内精确名优先于更新的关键词
    assert result.matches['valid'].exact
    # 另一目录只有两个关键词候选：按修改时间较新优先，时间相同按完整路径排序。
    second = tmp_path / 'b'
    second.mkdir()
    older = second / 'valid-backup.txt'
    newer = second / 'zzz-valid.txt'
    older.write_text('123'); newer.write_text('123')
    os.utime(older, ns=(100, 100)); os.utime(newer, ns=(300, 300))
    assert scan_library_directories([str(second)]).matches['valid'].path == str(newer)
    os.utime(older, ns=(300, 300))
    assert scan_library_directories([str(second)]).matches['valid'].path == str(older)


def test_missing_directory(tmp_path):
    result = scan_library_directories([str(tmp_path / 'missing')])
    assert any('目录不存在' in message.text for message in result.messages)


def test_permission_error(tmp_path):
    with patch('library_matcher.os.scandir', side_effect=PermissionError('denied')):
        result = scan_library_directories([str(tmp_path)])
    assert any('无权限' in message.text for message in result.messages)


def test_locked_candidate_falls_back(tmp_path):
    first, second = tmp_path / 'valid_customers.txt', tmp_path / 'valid-backup.txt'
    first.write_text('123'); second.write_text('456')
    original_open = open
    def locked(path, *args, **kwargs):
        if os.fspath(path) == str(first):
            raise PermissionError('sharing violation')
        return original_open(path, *args, **kwargs)
    with patch('library_matcher.open', side_effect=locked):
        result = scan_library_directories([str(tmp_path)])
    assert result.matches['valid'].path == str(second)
    assert any('占用' in message.text for message in result.messages)


def test_duplicate_directory_and_directory_named_like_library(tmp_path):
    (tmp_path / 'invalid_customers.txt').mkdir()
    (tmp_path / 'valid_customers.txt').write_text('123')
    result = scan_library_directories([str(tmp_path), str(tmp_path)])
    assert any('重复目录' in message.text for message in result.messages)
    assert any('不是普通文件' in message.text for message in result.messages)
    assert result.matches['invalid'] is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows sharing modes')
def test_actual_windows_exclusive_file_lock(tmp_path):
    import ctypes
    from ctypes import wintypes
    target = tmp_path / 'opened_customers.txt'
    target.write_text('12125551234\n')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW(str(target), 0x80000000, 0, None, 3, 0x80, None)
    assert handle != ctypes.c_void_p(-1).value
    try:
        result = scan_library_directories([str(tmp_path)])
        assert result.matches['opened'] is None
        assert any('占用' in message.text for message in result.messages)
    finally:
        kernel.CloseHandle(handle)
