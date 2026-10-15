"""Verify bundled matcher, external libraries, CLI, AppData, and persistent locks."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from PyQt6.QtCore import QSettings

root = Path(__file__).resolve().parents[1]
delivery = root / 'dist/library-auto'
exe = next(delivery.glob('*.exe'))
original_files = sorted(path.name for path in delivery.iterdir())
assert len(original_files) == 5
report = {}

with tempfile.TemporaryDirectory() as temporary:
    working = Path(temporary)
    appdata = working / 'appdata'
    environment = dict(os.environ, LOCALAPPDATA=str(appdata), QT_QPA_PLATFORM='offscreen')
    source, output = working / 'input.txt', working / 'output.txt'
    source.write_text('2125551234\n')
    def cli(name, args, expected):
        result = subprocess.run([str(exe), *args], cwd=working, env=environment,
                                stdin=subprocess.DEVNULL, capture_output=True, timeout=45)
        assert result.returncode == expected, (name, result.stdout, result.stderr)
        report[name] = result.returncode
    cli('help', ['help'], 0)
    cli('normalize', ['normalize', str(source), str(output)], 0)
    assert output.read_text().strip() == '12125551234'
    cli('console_alias', ['console', 'normalize', str(source), str(output)], 0)
    cli('missing_input', ['normalize', 'missing.txt', str(output)], 2)
    source.write_text('12125551234\n12125559999\n')
    library = working / 'classify.txt'
    library.write_text('2125551234\n')
    cli('process', ['process', str(source), str(output), str(library)], 0)
    assert output.read_text().strip() == '12125559999'

    logfile = appdata / 'NumberWorkbench/logs/workbench.log'
    def gui(required):
        previous = logfile.read_text(encoding='utf-8') if logfile.exists() else ''
        process = subprocess.Popen([str(exe)], cwd=working, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                assert process.poll() is None, 'GUI exited early'
                if logfile.exists():
                    current = logfile.read_text(encoding='utf-8')[len(previous):]
                    if all(fragment in current for fragment in required):
                        time.sleep(.5)
                        assert process.poll() is None
                        return current
                time.sleep(.1)
            raise AssertionError('GUI discovery log timed out')
        finally:
            # Terminate only the test bootloader and its own child process.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            process.wait(timeout=15)

    first_log = gui([f'{kind}：自动发现并填入 {delivery / (kind + "_customers.txt")}'
                     for kind in ('invalid', 'opened', 'valid')])
    report['frozen_program_directory_discovery'] = True
    settings_path = appdata / 'NumberWorkbench/settings.ini'
    settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
    manual = working / 'manual.txt'
    manual.write_text('12125559999\n')
    settings.setValue('libraries/valid/locked', True)
    settings.setValue('libraries/valid/path', str(manual))
    settings.sync()
    assert settings.status() == QSettings.Status.NoError
    second_log = gui([f'valid：已恢复手动锁定；{manual}', 'valid：已手动指定，跳过锁定行。'])
    assert f'valid：自动发现并填入 {delivery / "valid_customers.txt"}' not in second_log
    report['frozen_restart_manual_lock'] = True
    assert settings_path.exists() and logfile.exists()
    report['settings_and_logs_in_appdata'] = True
    assert sorted(path.name for path in delivery.iterdir()) == original_files
    report['delivery_directory_unmodified_by_startup'] = True

report['delivery_files'] = original_files
def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()

report['source_sha256'] = digest(root / 'customer_number_processor_single.py')
report['matcher_sha256'] = digest(root / 'library_matcher.py')
report['exe_sha256'] = digest(exe)
destination = root / 'artifacts/library-discovery/exe-check.json'
destination.parent.mkdir(parents=True, exist_ok=True)
fd, temporary = tempfile.mkstemp(dir=destination.parent)
with os.fdopen(fd, 'w', encoding='utf-8') as stream:
    json.dump(report, stream, ensure_ascii=False, indent=2)
os.replace(temporary, destination)
print(report)
