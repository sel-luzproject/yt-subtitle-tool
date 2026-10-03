"""Local SRT review app: FastAPI backend serving the processed videos.

Run: python app/server.py  (from the project root), or double-click 啟動字幕機.bat in an installed copy
Opens on http://127.0.0.1:8420
"""
import difflib
import glob
import json
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

WORK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORK))
from config import load_config, save_config  # noqa: E402

VIDEOS_DIR = WORK / 'videos'
GLOSSARY_PATH = WORK / 'glossary.txt'
CHANNEL_LIST_PATH = WORK / 'channel_all.json'
CHANNEL_STATUS_PATH = WORK / 'channel_status.json'
QUEUE_PATH = WORK / 'queue.json'

app = FastAPI()


class SetupBody(BaseModel):
    gemini_api_key: str
    channel_url: str


def _batch_pids():
    r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                         "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
                         "Where-Object { $_.CommandLine -match 'watchdog_and_run.py|batch_process.py' } | "
                         "Select-Object -ExpandProperty ProcessId"],
                        capture_output=True, text=True, timeout=45)
    return [int(x) for x in r.stdout.split() if x.strip().isdigit()]


@app.get('/api/batch/status')
def batch_status():
    return {'running': bool(_batch_pids())}


@app.post('/api/batch/start')
def batch_start():
    if _batch_pids():
        return {'ok': True, 'already_running': True}
    env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
    log = open(WORK / 'batch_log.txt', 'a', encoding='utf-8')
    subprocess.Popen([sys.executable, str(WORK / 'watchdog_and_run.py')], cwd=str(WORK), env=env,
                      stdout=log, stderr=subprocess.STDOUT,
                      creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return {'ok': True, 'already_running': False}


@app.get('/api/setup')
def get_setup():
    cfg = load_config()
    return {'setup_complete': cfg['setup_complete'], 'channel_url': cfg['channel_url'],
            'has_api_key': bool(cfg['gemini_api_key'])}


@app.post('/api/setup')
def post_setup(body: SetupBody):
    api_key = body.gemini_api_key.strip()
    channel_url = body.channel_url.strip()
    if not api_key or not channel_url:
        return {'ok': False, 'error': '請輸入 API Key 和頻道網址'}
    save_config(gemini_api_key=api_key, channel_url=channel_url, setup_complete=True)

    from check_gpu import check_gpu
    gpu_ok, gpu_msg = check_gpu()

    try:
        r = subprocess.run([sys.executable, str(WORK / 'refresh_channel.py')],
                            cwd=str(WORK), capture_output=True, text=True, encoding='utf-8', errors='replace',
                            env={**os.environ, 'PYTHONIOENCODING': 'utf-8'}, timeout=900)
        fetch_output = r.stdout[-2000:] + r.stderr[-500:]
    except Exception as e:
        fetch_output = f'(頻道清單抓取發生問題，可以稍後在「頻道影片清單」頁面手動建立佇列: {e})'
    return {'ok': True, 'fetch_output': fetch_output, 'gpu_ok': gpu_ok, 'gpu_msg': gpu_msg}

_titles_cache = None


def get_titles():
    """id -> title, sourced from channel_all.json (authoritative, fetched directly from YouTube)."""
    global _titles_cache
    if _titles_cache is None:
        _titles_cache = {}
        if CHANNEL_LIST_PATH.exists():
            for v in json.loads(CHANNEL_LIST_PATH.read_text(encoding='utf-8-sig')):
                _titles_cache[v['id']] = v['title']
    return _titles_cache


class TITLES:
    @staticmethod
    def get(vid, default=None):
        return get_titles().get(vid, default)


def srt_time_to_sec(t):
    m = re.match(r'(\d+):(\d+):(\d+)[,.](\d+)', t)
    if not m:
        return 0.0
    h, mn, s, ms = m.groups()
    return int(h) * 3600 + int(mn) * 60 + int(s) + int(ms) / 1000


def parse_srt(path):
    cues = []
    text = open(path, encoding='utf-8-sig').read().strip()
    if not text:
        return cues
    for block in text.split('\n\n'):
        lines = block.strip().split('\n')
        if len(lines) < 2:
            continue
        m = re.match(r'(.+?)\s*-->\s*(.+)', lines[1])
        if not m:
            continue
        start, end = srt_time_to_sec(m.group(1)), srt_time_to_sec(m.group(2))
        cues.append({'start': start, 'end': end, 'text': '\n'.join(lines[2:])})
    return cues


def parse_tsv(path):
    rows = []
    if not os.path.exists(path):
        return rows
    lines = open(path, encoding='utf-8-sig').read().strip().split('\n')
    header = lines[0].split('\t')
    for line in lines[1:]:
        vals = line.split('\t')
        rows.append(dict(zip(header, vals)))
    return rows


def loose_time_to_sec(t):
    parts = t.strip().split(':')
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return h * 3600 + m * 60 + s


def video_ids():
    """IDs of every video that has ever been processed (has a corrected SRT), audio or not."""
    return sorted(p.stem.removesuffix('_corrected') for p in WORK.glob('*_corrected.srt'))


_order_cache = None


def get_upload_order():
    """id -> position in the channel's newest-first listing (0 = newest). Lower is newer."""
    global _order_cache
    if _order_cache is None:
        _order_cache = {}
        if CHANNEL_LIST_PATH.exists():
            for v in json.loads(CHANNEL_LIST_PATH.read_text(encoding='utf-8-sig')):
                _order_cache[v['id']] = v.get('order', 999999)
    return _order_cache


@app.get('/api/videos')
def list_videos():
    out = []
    for vid in video_ids():
        corrected = WORK / f'{vid}_corrected.srt'
        changes = WORK / f'{vid}_corrected.srt.changes.tsv'
        if not corrected.exists():
            continue
        cues = parse_srt(corrected)
        duration = cues[-1]['end'] if cues else 0
        edit_rows = [r for r in parse_tsv(changes) if r.get('accepted') == 'True']
        out.append({
            'id': vid,
            'title': TITLES.get(vid, vid),
            'duration': duration,
            'cue_count': len(cues),
            'edit_count': len(edit_rows),
            'has_audio': find_audio(vid) is not None,
            'is_final': (WORK / f'{vid}_final.srt').exists(),
            'user_finished': is_user_finished(vid),
        })
    order = get_upload_order()
    out.sort(key=lambda v: order.get(v['id'], 999999))
    return out


@app.get('/api/videos/{vid}')
def get_video(vid: str):
    final = WORK / f'{vid}_final.srt'
    src = final if final.exists() else WORK / f'{vid}_corrected.srt'
    cues = parse_srt(src)

    changes = parse_tsv(WORK / f'{vid}_corrected.srt.changes.tsv')
    edits_by_time = {}
    rejected_by_time = {}
    for r in changes:
        try:
            t = round(loose_time_to_sec(r['time']))
        except Exception:
            continue
        if r.get('accepted') == 'True':
            edits_by_time[t] = r
        elif r.get('reason') != 'same/empty':   # an echo isn't a real suggestion worth surfacing
            rejected_by_time[t] = r

    review = parse_tsv(WORK / f'{vid}.srt.review.tsv')
    low_conf_times = set()
    for r in review:
        try:
            low_conf_times.add(round(loose_time_to_sec(r['time'])))
        except Exception:
            pass

    glossary_hits = parse_tsv(WORK / f'{vid}_final.srt.glossary.tsv')
    glossary_times = set()
    for r in glossary_hits:
        try:
            glossary_times.add(round(loose_time_to_sec(r['time'])))
        except Exception:
            pass

    for i, c in enumerate(cues):
        c['idx'] = i
        t = round(c['start'])
        edit = None
        for dt in (0, -1, 1):
            if (t + dt) in edits_by_time:
                edit = edits_by_time[t + dt]
                break
        c['ai_edited'] = bool(edit)
        c['original_text'] = edit['old'] if edit else None
        c['low_confidence'] = any((t + dt) in low_conf_times for dt in (0, -1, 1))
        c['glossary_edited'] = any((t + dt) in glossary_times for dt in (0, -1, 1))

        rejected = None
        for dt in (0, -1, 1):
            if (t + dt) in rejected_by_time:
                rejected = rejected_by_time[t + dt]
                break
        # only worth surfacing if it still matches the CURRENT text - if the cue was already
        # edited (by AI or by hand) since, the old rejected proposal is no longer relevant
        c['rejected_suggestion'] = rejected['new'] if rejected and rejected['old'] == c['text'] else None

    return {
        'id': vid,
        'title': TITLES.get(vid, vid),
        'cues': cues,
        'is_final': final.exists(),
        'has_audio': find_audio(vid) is not None,
        'user_finished': is_user_finished(vid),
    }


def diff_pairs(old, new):
    """Character-level replace-only diff pairs between two strings, e.g. '金氏盾' vs '金士頓' -> [('氏', '士')]."""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    pairs = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'replace':
            pairs.append((old[i1:i2], new[j1:j2]))
    return pairs


@app.get('/api/videos/{vid}/glossary-candidates')
def glossary_candidates(vid: str):
    """Diff the user's final manual edits against the AI-corrected baseline and surface
    short, clean word-substitution patterns that look reusable across other videos."""
    final = WORK / f'{vid}_final.srt'
    corrected = WORK / f'{vid}_corrected.srt'
    if not final.exists() or not corrected.exists():
        return []

    final_cues = parse_srt(final)
    corrected_cues = parse_srt(corrected)
    n = min(len(final_cues), len(corrected_cues))

    existing = {(e['wrong'], e['right']) for e in read_glossary()}
    counts = {}
    for i in range(n):
        old_text, new_text = corrected_cues[i]['text'], final_cues[i]['text']
        if old_text == new_text:
            continue
        for old_sub, new_sub in diff_pairs(old_text, new_text):
            if not old_sub or not new_sub:
                continue
            # single-character "wrong" side is too risky: common hanzi (在/去/是/的...) appear
            # constantly with correct meaning, so a blind literal replace would corrupt them elsewhere
            if len(old_sub) < 2 or len(old_sub) > 8 or len(new_sub) > 8:
                continue
            if not re.search(r'[^\W\d_]', old_sub, re.UNICODE):   # skip pure punctuation/digits
                continue
            if (old_sub, new_sub) in existing:
                continue
            key = (old_sub, new_sub)
            counts[key] = counts.get(key, 0) + 1

    # only surface patterns that repeated within this video - a single one-off is unlikely to be reusable
    return [{'wrong': w, 'right': r, 'count': c} for (w, r), c in sorted(counts.items(), key=lambda x: -x[1]) if c >= 2]


AUDIO_MIME = {'.wav': 'audio/wav', '.webm': 'audio/webm', '.m4a': 'audio/mp4', '.opus': 'audio/ogg', '.mp3': 'audio/mpeg'}


def find_audio(vid):
    for p in VIDEOS_DIR.glob(f'{vid}.*'):
        if p.suffix.lower() in AUDIO_MIME:
            return p
    return None


@app.get('/api/videos/{vid}/audio')
def get_audio(vid: str):
    path = find_audio(vid)
    if not path:
        return PlainTextResponse('audio not available (may have been cleaned up after review)', status_code=404)
    return FileResponse(path, media_type=AUDIO_MIME.get(path.suffix.lower(), 'application/octet-stream'))


FINISHED_LIST_PATH = WORK / 'finished_videos.json'


def is_user_finished(vid):
    """True only if the user explicitly clicked 完成校對並釋放音檔 - not merely 'no audio file
    present', since audio can also be missing due to an interrupted/incomplete download."""
    if not FINISHED_LIST_PATH.exists():
        return False
    return vid in json.loads(FINISHED_LIST_PATH.read_text(encoding='utf-8-sig'))


@app.post('/api/videos/{vid}/finish')
def finish_video(vid: str):
    path = find_audio(vid)
    freed_mb = 0
    if path:
        freed_mb = round(path.stat().st_size / 1024 / 1024, 1)
        path.unlink()
    finished = json.loads(FINISHED_LIST_PATH.read_text(encoding='utf-8-sig')) if FINISHED_LIST_PATH.exists() else []
    if vid not in finished:
        finished.append(vid)
    FINISHED_LIST_PATH.write_text(json.dumps(finished, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'ok': True, 'freed_mb': freed_mb}


class SaveBody(BaseModel):
    cues: list


def fmt_srt_time(t):
    ms = int(round(t * 1000))
    h, r = divmod(ms, 3600000)
    m, r = divmod(r, 60000)
    s, ms = divmod(r, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


@app.post('/api/videos/{vid}/save')
def save_video(vid: str, body: SaveBody):
    path = WORK / f'{vid}_final.srt'
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for i, c in enumerate(body.cues, 1):
            f.write(f"{i}\n{fmt_srt_time(c['start'])} --> {fmt_srt_time(c['end'])}\n{c['text']}\n\n")
    return {'ok': True, 'path': str(path)}


@app.get('/api/videos/{vid}/export')
def export_video(vid: str):
    final = WORK / f'{vid}_final.srt'
    src = final if final.exists() else WORK / f'{vid}_corrected.srt'
    return FileResponse(src, media_type='application/x-subrip', filename=f'{vid}.srt')


def write_srt(path, cues):
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{fmt_srt_time(c['start'])} --> {fmt_srt_time(c['end'])}\n{c['text']}\n\n")


def read_glossary():
    entries = []
    if GLOSSARY_PATH.exists():
        for line in open(GLOSSARY_PATH, encoding='utf-8-sig'):
            line = line.strip()
            if line and not line.startswith('#') and '=>' in line:
                a, b = line.split('=>', 1)
                entries.append({'wrong': a.strip(), 'right': b.strip()})
    return entries


def write_glossary(entries):
    with open(GLOSSARY_PATH, 'w', encoding='utf-8', newline='\n') as f:
        f.write('# wrong=>right  (one per line; applied after Traditional conversion)\n')
        for e in entries:
            f.write(f"{e['wrong']}=>{e['right']}\n")


@app.get('/api/glossary')
def get_glossary():
    return read_glossary()


class GlossaryBody(BaseModel):
    wrong: str
    right: str
    apply_to: str = 'current'   # 'current' or 'all'
    vid: str | None = None


@app.post('/api/glossary')
def add_glossary(body: GlossaryBody):
    entries = read_glossary()
    if not any(e['wrong'] == body.wrong and e['right'] == body.right for e in entries):
        entries.append({'wrong': body.wrong, 'right': body.right})
        write_glossary(entries)

    targets = video_ids() if body.apply_to == 'all' else ([body.vid] if body.vid else [])
    applied = {}
    for vid in targets:
        final = WORK / f'{vid}_final.srt'
        corrected = WORK / f'{vid}_corrected.srt'
        src = final if final.exists() else corrected
        if not src.exists():
            continue
        cues = parse_srt(src)
        hits = []
        for c in cues:
            n = c['text'].count(body.wrong)
            if n:
                c['text'] = c['text'].replace(body.wrong, body.right)
                hits.append(c)
        if hits:
            write_srt(final, cues)
            hit_log = WORK / f'{vid}_final.srt.glossary.tsv'
            is_new = not hit_log.exists()
            with open(hit_log, 'a', encoding='utf-8-sig', newline='\n') as f:
                if is_new:
                    f.write('time\twrong\tright\n')
                for c in hits:
                    t = int(c['start'])
                    f.write(f"{t//3600}:{t%3600//60:02d}:{t%60:02d}\t{body.wrong}\t{body.right}\n")
        applied[vid] = len(hits)

    return {'glossary': read_glossary(), 'applied': applied}


class GlossaryDeleteBody(BaseModel):
    wrong: str
    right: str


@app.delete('/api/glossary')
def delete_glossary(body: GlossaryDeleteBody):
    entries = [e for e in read_glossary() if not (e['wrong'] == body.wrong and e['right'] == body.right)]
    write_glossary(entries)
    return entries


def read_channel_status():
    if CHANNEL_STATUS_PATH.exists():
        # utf-8-sig tolerates a BOM (e.g. if the file was ever touched by PowerShell's `-Encoding utf8`)
        return json.loads(CHANNEL_STATUS_PATH.read_text(encoding='utf-8-sig'))
    return {}


def write_channel_status(status):
    CHANNEL_STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding='utf-8')


@app.get('/api/channel')
def get_channel():
    if not CHANNEL_LIST_PATH.exists():
        return []
    videos = json.loads(CHANNEL_LIST_PATH.read_text(encoding='utf-8-sig'))
    status = read_channel_status()
    done_ids = {vid for vid in video_ids() if (WORK / f'{vid}_corrected.srt').exists()}
    out = []
    for v in videos:
        st = status.get(v['id'], {})
        out.append({**v, 'skip': bool(st.get('skip')), 'done': v['id'] in done_ids})
    return out


class ChannelStatusBody(BaseModel):
    updates: dict   # {video_id: bool skip}


@app.post('/api/channel/status')
def update_channel_status(body: ChannelStatusBody):
    status = read_channel_status()
    for vid, skip in body.updates.items():
        status.setdefault(vid, {})['skip'] = bool(skip)
    write_channel_status(status)
    return {'ok': True}


@app.post('/api/queue/build')
def build_queue():
    videos = json.loads(CHANNEL_LIST_PATH.read_text(encoding='utf-8-sig')) if CHANNEL_LIST_PATH.exists() else []
    status = read_channel_status()
    done_ids = {vid for vid in video_ids() if (WORK / f'{vid}_corrected.srt').exists()}
    todo = [v for v in videos
            if not status.get(v['id'], {}).get('skip')
            and v['id'] not in done_ids
            and (v.get('duration') or 0) >= 300]
    todo.sort(key=lambda v: v.get('duration') or 0)   # shortest first
    QUEUE_PATH.write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding='utf-8')
    return {'queued': len(todo)}


@app.get('/api/queue/progress')
def queue_progress():
    progress_path = WORK / 'batch_progress.json'
    usage_path = WORK / 'gemini_daily_usage.json'
    progress = json.loads(progress_path.read_text(encoding='utf-8-sig')) if progress_path.exists() else {}
    usage = json.loads(usage_path.read_text(encoding='utf-8-sig')) if usage_path.exists() else {}

    # compute real progress straight from disk state, not the batch script's internal loop counter
    # (that counter is scoped to one phase's loop and gets misleading once phases run repeatedly)
    queue = json.loads(QUEUE_PATH.read_text(encoding='utf-8-sig')) if QUEUE_PATH.exists() else []
    prep_done = sum(1 for v in queue if (WORK / f"{v['id']}.srt").exists()
                     or (WORK / f"{v['id']}_corrected.srt").exists())
    corrected_done = sum(1 for v in queue if (WORK / f"{v['id']}_corrected.srt").exists())

    return {**progress, 'gemini_usage': usage, 'total': len(queue),
            'prep_done_count': prep_done, 'corrected_done_count': corrected_done}


app.mount('/', StaticFiles(directory=str(Path(__file__).parent / 'static'), html=True), name='static')

if __name__ == '__main__':
    import uvicorn
    port = int(os.environ.get('YT_PORT', 8420))
    url = f'http://127.0.0.1:{port}'
    print(f'Starting server at {url}')
    if not os.environ.get('YT_NO_BROWSER'):
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
