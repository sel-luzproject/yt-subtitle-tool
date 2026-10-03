"""Stage a relocatable customer distribution: packaging/build/YT字幕機/

  python/            copy of this machine's python.org Python (no site-packages) + product dependencies
  app/ + *.py        only the scripts the subtitle product needs (no highlight/render experiments)
  啟動字幕機.bat      double-click launcher (starts the server, opens the browser)

Run with the dev Python:  python packaging/build_dist.py
Then compile packaging/installer.iss with Inno Setup to get the single setup .exe.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import os
# stage outside OneDrive: its sync client locks freshly written files/dirs and breaks the build
OUT = Path(os.environ.get('LOCALAPPDATA', str(ROOT))) / 'YTSubtitleBuild' / 'YT字幕機'
BASE_PY = Path(sys.base_prefix)

APP_SCRIPTS = ['config.py', 'check_gpu.py', 'batch_process.py', 'watchdog_and_run.py', 'refresh_channel.py',
               'transcribe_words.py', 'build_srt.py', 'correct_srt.py', 'phonetic_guard.py']
APP_DATA = ['prompt_c.txt']
STARTER_GLOSSARY = '# wrong=>right  (one per line; applied after Traditional conversion)\n'

LAUNCHER = '''@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
echo 正在啟動 YT 字幕機，請稍候（瀏覽器會自動開啟）...
echo 關閉此視窗即可結束程式。
"%~dp0python\\python.exe" app\\server.py
pause
'''


def copy_python():
    dst = OUT / 'python'
    dst.mkdir(parents=True)
    for name in ('python.exe', 'pythonw.exe', 'python3.dll', 'python313.dll', 'vcruntime140.dll',
                 'vcruntime140_1.dll', 'LICENSE.txt'):
        shutil.copy2(BASE_PY / name, dst / name)
    shutil.copytree(BASE_PY / 'DLLs', dst / 'DLLs')
    shutil.copytree(BASE_PY / 'Lib', dst / 'Lib',
                    ignore=shutil.ignore_patterns('site-packages', '__pycache__', 'test', 'tests', 'idlelib',
                                                  'tkinter', 'turtledemo', 'ensurepip'))
    (dst / 'Lib' / 'site-packages').mkdir()


def _requirement_names():
    names = []
    for line in (ROOT / 'packaging' / 'requirements-product.txt').read_text().splitlines():
        m = re.match(r'[A-Za-z0-9_.\-]+', line.strip())
        if m:
            names.append(m.group(0))
    return names


def install_deps():
    """Copy the product's dependency closure straight from the (already installed, tested) dev venv
    using each distribution's RECORD - avoids re-downloading ~1.5GB of CUDA wheels on every build."""
    from importlib import metadata
    from packaging.requirements import Requirement
    site = OUT / 'python' / 'Lib' / 'site-packages'
    todo, seen = _requirement_names(), set()
    while todo:
        name = todo.pop()
        key = re.sub(r'[-_.]+', '-', name).lower()
        if key in seen:
            continue
        seen.add(key)
        dist = metadata.distribution(name)
        for req in dist.requires or []:
            r = Requirement(req)
            if r.marker is None or r.marker.evaluate({'extra': ''}):
                todo.append(r.name)
        for f in dist.files or []:
            src = Path(dist.locate_file(f))
            if '__pycache__' in src.parts or not src.is_file() or '..' in Path(f).parts:
                continue
            dst = site / f
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    print(f'copied {len(seen)} distributions: {sorted(seen)}')


def copy_app():
    for f in APP_SCRIPTS + APP_DATA:
        shutil.copy2(ROOT / f, OUT / f)
    (OUT / 'glossary.txt').write_text(STARTER_GLOSSARY, encoding='utf-8')
    app_dst = OUT / 'app'
    app_dst.mkdir()
    shutil.copy2(ROOT / 'app' / 'server.py', app_dst / 'server.py')
    static_dst = app_dst / 'static'
    static_dst.mkdir()
    for f in ('index.html', 'channel.html', 'setup.html', 'setup-gate.js'):
        shutil.copy2(ROOT / 'app' / 'static' / f, static_dst / f)
    (OUT / '啟動字幕機.bat').write_text(LAUNCHER, encoding='utf-8')
    shutil.copy2(ROOT / 'packaging' / '使用說明.html', OUT / '使用說明.html')
    for f in ('LICENSE', 'THIRD_PARTY_NOTICES.md'):
        shutil.copy2(ROOT / f, OUT / f)
    (OUT / 'videos').mkdir()


def main():
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    copy_python()
    install_deps()
    copy_app()
    size = sum(f.stat().st_size for f in OUT.rglob('*') if f.is_file()) / 1024**3
    print(f'staged {OUT}  ({size:.2f} GB)')


if __name__ == '__main__':
    main()
