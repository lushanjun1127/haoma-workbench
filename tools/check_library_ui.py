"""Capture library discovery states with isolated user configuration."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'windows'
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from PyQt6.QtWidgets import QApplication, QFileDialog
import customer_number_processor_single as m

app = QApplication([])
app.setStyle('Fusion')
destination = root / 'artifacts/library-discovery'
destination.mkdir(parents=True, exist_ok=True)

def save_picture(window, name):
    fd, temporary = tempfile.mkstemp(dir=destination, suffix='.png')
    os.close(fd)
    try:
        assert window.grab().save(temporary, 'PNG')
        os.replace(temporary, destination / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

with tempfile.TemporaryDirectory() as temporary:
    config = Path(temporary) / 'appdata'
    (config / 'logs').mkdir(parents=True)
    m._LOG_DIR = str(config / 'logs')
    window = m.MainUI()
    window.show()
    tab = window.processing_tab
    for _ in range(30):
        app.processEvents()
        time.sleep(.01)
    assert all(label.text() == '自动发现' for label in tab.library_state_labels.values())
    for theme in ('light', 'dark'):
        window._set_theme(theme, persist=False)
        window.resize(1200, 800)
        app.processEvents()
        save_picture(window, f'startup-{theme}.png')

    samples = Path(temporary) / 'samples'
    samples.mkdir()
    (samples / 'opened_customers.txt').write_text('12125551234\n')
    key = m.CLASSIFY_FILES['valid']
    with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(root / key), '')):
        tab._select_cat_file(tab.category_files[key])
    tab._library_program_directory = str(samples)
    tab.rescan_library_files()
    for theme in ('light', 'dark'):
        window._set_theme(theme, persist=False)
        for width, height in ((1200, 800), (960, 640)):
            window.resize(width, height)
            app.processEvents()
            for label in tab.library_state_labels.values():
                assert label.geometry().right() < label.parentWidget().width()
            tab.scroll_area.ensureWidgetVisible(tab.rescan_btn)
            app.processEvents()
            position = tab.rescan_btn.mapTo(tab.scroll_area.viewport(), tab.rescan_btn.rect().center())
            assert tab.scroll_area.viewport().rect().contains(position)
            save_picture(window, f'states-{theme}-{width}.png')
    report = {'states': {key: label.text() for key, label in tab.library_state_labels.items()},
              'minimum_window_rescan_accessible': True,
              'settings_location': window.settings.fileName(),
              'themes': ['light', 'dark']}
    window.close()
    deadline = time.monotonic() + 10
    while window.isVisible() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert not window.isVisible()
    window.settings.sync()
    fd, temporary_report = tempfile.mkstemp(dir=destination)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    os.replace(temporary_report, destination / 'ui-check.json')
    print(report)
