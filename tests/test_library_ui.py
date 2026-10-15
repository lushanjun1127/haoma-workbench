import ast
from collections import Counter
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'windows')
import pytest
from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QFileDialog
import customer_number_processor_single as m


@pytest.fixture
def ui(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    app.setStyle('Fusion')
    program = tmp_path / 'program'
    program.mkdir()
    appdata = tmp_path / 'appdata'
    (appdata / 'logs').mkdir(parents=True)
    monkeypatch.setattr(m, '__file__', str(program / 'main.py'))
    monkeypatch.setattr(m, '_LOG_DIR', str(appdata / 'logs'))
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / 'legacy'))
    windows = []
    def make():
        window = m.MainUI()
        windows.append(window)
        window.show()
        app.processEvents()
        return window
    yield app, program, make
    for window in windows:
        window.close()
        deadline = time.monotonic() + 10
        while window.isVisible() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(.01)
        assert not window.isVisible()
        window.settings.sync()


def write_libraries(directory, kinds=('invalid', 'opened', 'valid')):
    for kind in kinds:
        (directory / (kind + '_customers.txt')).write_text('12125551234\n', encoding='utf-8')


def test_startup_source_supplement_and_manual_rescan(ui, tmp_path):
    app, program, make = ui
    write_libraries(program, ('invalid',))
    window = make()
    tab = window.processing_tab
    assert tab.category_files[m.CLASSIFY_FILES['invalid']].text() == str(program / 'invalid_customers.txt')
    assert tab.library_state_labels[m.CLASSIFY_FILES['valid']].text() == '未找到请手动选择'
    source_dir = tmp_path / 'source'
    source_dir.mkdir()
    write_libraries(source_dir, ('opened', 'valid'))
    source = source_dir / 'source.txt'
    source.write_text('12125551234\n')
    tab.set_source_file(str(source))
    assert tab.category_files[m.CLASSIFY_FILES['valid']].text() == str(source_dir / 'valid_customers.txt')
    assert tab.category_files[m.CLASSIFY_FILES['invalid']].text() == str(program / 'invalid_customers.txt')
    assert all(label.text() == '自动发现' for label in tab.library_state_labels.values())
    # 后台统计线程可能仍持有该文件句柄；Windows 文件锁下需等其释放后再删除。
    deadline = time.monotonic() + 5
    while True:
        try:
            (source_dir / 'valid_customers.txt').unlink()
            break
        except PermissionError:
            if time.monotonic() > deadline:
                raise
            app.processEvents()
            time.sleep(.02)
    tab.rescan_btn.click()
    assert tab.category_files[m.CLASSIFY_FILES['valid']].text() == ''
    assert tab.library_state_labels[m.CLASSIFY_FILES['valid']].text() == '未找到请手动选择'


def test_manual_browse_lock_persists_and_never_overwritten(ui, tmp_path):
    app, program, make = ui
    write_libraries(program)
    manual = tmp_path / 'manual.txt'
    manual.write_text('12125559999\n')
    window = make()
    tab = window.processing_tab
    key = m.CLASSIFY_FILES['valid']
    with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(manual), '')):
        tab._select_cat_file(tab.category_files[key])
    tab.rescan_btn.click()
    assert tab._library_locks[key]
    assert tab.category_files[key].text() == str(manual)
    assert tab.library_state_labels[key].text() == '已手动指定'
    deadline = time.monotonic() + 5
    while tab._workers and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert tab.library_state_labels[key].text() == '已手动指定'  # Counts cannot overwrite state.
    second = make().processing_tab
    assert second._library_locks[key]
    assert second.category_files[key].text() == str(manual)
    assert second.library_state_labels[key].text() == '已手动指定'
    assert Path(window.settings.fileName()).parent.name == 'appdata'
    assert not list(program.glob('*.ini'))


def test_manual_typing_and_clear_remain_locked(ui):
    app, program, make = ui
    window = make()
    tab = window.processing_tab
    key = m.CLASSIFY_FILES['opened']
    edit = tab.category_files[key]
    edit.setFocus()
    QTest.keyClicks(edit, 'manual-path.txt')
    QTest.keyClick(edit, Qt.Key.Key_Return)
    app.processEvents()
    assert tab._library_locks[key]
    assert tab.library_state_labels[key].text() == '已手动指定'
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    QTest.keyClick(edit, Qt.Key.Key_Return)
    write_libraries(program)
    tab.rescan_btn.click()
    assert edit.text() == ''
    assert make().processing_tab._library_locks[key]


def test_frozen_reuses_executable_directory(ui, monkeypatch, tmp_path):
    app, program, make = ui
    packaged = tmp_path / 'delivery'
    packaged.mkdir()
    write_libraries(packaged)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(packaged / 'workbench.exe'))
    tab = make().processing_tab
    assert tab._library_program_directory == str(packaged)
    assert all(Path(edit.text()).parent == packaged for edit in tab.category_files.values())


def test_legacy_theme_migration_and_restart(ui):
    app, program, make = ui
    legacy = QSettings('号码工作台', 'SingleFileEdition')
    legacy.setValue('themeMode', 'dark')
    legacy.sync()
    first = make()
    assert first.theme_mode == 'dark'
    first._set_theme('light')
    first.settings.sync()
    assert make().theme_mode == 'light'


def test_conflict_and_missing_directory_are_logged(ui, tmp_path):
    app, program, make = ui
    write_libraries(program, ('valid',))
    (program / 'valid-copy.txt').write_text('12125551234\n')
    tab = make().processing_tab
    assert '多匹配' in tab.log_text.toPlainText()
    assert '精确名优先' in tab.log_text.toPlainText()
    tab._auto_detect_category_directory(str(tmp_path / 'missing'))
    assert '目录不存在' in tab.log_text.toPlainText()


def test_protected_core_and_existing_connections_unchanged():
    root = Path(__file__).resolve().parents[1]
    baseline = root / 'tests/fixtures/protected_core_baseline.py'
    if not baseline.is_file():
        pytest.skip('缺少保护核心基线快照 tests/fixtures/protected_core_baseline.py')
    old = ast.parse(baseline.read_text(encoding='utf-8'))
    new = ast.parse((root / 'customer_number_processor_single.py').read_text(encoding='utf-8'))
    old_defs = {n.name: n for n in old.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    new_defs = {n.name: n for n in new.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
    for name, definition in old_defs.items():
        if name not in ('MainUI', 'ProcessingTab'):
            assert ast.dump(definition) == ast.dump(new_defs[name]), name
    old_main = {n.name: n for n in old_defs['MainUI'].body if isinstance(n, ast.FunctionDef)}
    new_main = {n.name: n for n in new_defs['MainUI'].body if isinstance(n, ast.FunctionDef)}
    for name, definition in old_main.items():
        if name != '__init__':
            assert ast.dump(definition) == ast.dump(new_main[name]), name
    old_ui = {n.name: n for n in old_defs['ProcessingTab'].body if isinstance(n, ast.FunctionDef)}
    new_ui = {n.name: n for n in new_defs['ProcessingTab'].body if isinstance(n, ast.FunctionDef)}
    for name in ('_track_worker', '_worker_finished', '_update_cat_status', '_file_status_done',
                 '_file_status_error', 'start_processing', 'start_normalize', 'cancel_tasks',
                 '_on_processing_done', '_on_normalize_done', '_on_processing_error',
                 'refresh_category_stats', '_update_category_stats', '_log'):
        assert ast.dump(old_ui[name]) == ast.dump(new_ui[name]), name
    def connections(tree):
        return Counter(ast.dump(n) for n in ast.walk(tree)
                       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'connect')
    # 基线快照 tests/fixtures/protected_core_baseline.py 取自 v1.0 源码，
    # 已按需求删除"更多输入：无效号码库"折叠按钮，故其 extra_toggle 连接不计入基线。
    baseline = Counter({dump: count for dump, count in connections(old).items()
                        if 'extra_toggle' not in dump})
    assert not (baseline - connections(new))
