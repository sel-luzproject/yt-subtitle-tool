"""Batch-process the queue (queue.json): download -> transcribe -> build SRT -> Gemini-correct.

Usage: python batch_process.py
Resumable: skips any video that already has a *_corrected.srt.

Two decoupled phases per run:
  Phase 1 (prepare): download + transcribe + build SRT for every queued video. This is GPU/network
                      bound only, no daily cap, so it races ahead of the Gemini quota instead of
                      stopping the moment the quota runs out.
  Phase 2 (correct):  Gemini-correct every prepared-but-uncorrected SRT, up to today's free-tier
                      budget (gemini_daily_usage.json). Re-run any time (or via the daily scheduled
                      task) to correct the next day's worth of backlog built up by phase 1.

Audio is downloaded in its native compressed format (no WAV conversion - faster-whisper/ffmpeg
reads it directly) so disk usage stays low. Delete a video's audio from the review app once
you've finished checking it ("完成校對並釋放音檔" button) to keep the backlog's footprint small.
"""
import json
import math
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yt_dlp

WORK = Path(__file__).resolve().parent
VIDEOS_DIR = WORK / 'videos'
VIDEOS_DIR.mkdir(exist_ok=True)

QUEUE_PATH = WORK / 'queue.json'
USAGE_PATH = WORK / 'gemini_daily_usage.json'
PROGRESS_PATH = WORK / 'batch_progress.json'
GLOSSARY_PATH = WORK / 'glossary.txt'
PROMPT_PATH = WORK / 'prompt_c.txt'
SKIP_LIST_PATH = WORK / 'unavailable_videos.json'

AUDIO_EXTENSIONS = {'.wav', '.webm', '.m4a', '.opus', '.mp3', '.aac', '.ogg'}
DAILY_BUDGET = 480              # stay under the 500 RPD free-tier cap
EST_SECONDS_PER_REQUEST = 150   # ~1 Gemini request per 2.5 min of stream at CHUNK=50 (was 300 at CHUNK=100)

PERMANENT_ERRORS = ['sign in to confirm your age', 'private video', 'video unavailable',
                     'has been removed', 'account associated with this video has been terminated',
                     'this video is not available', 'join this channel', 'members-only']

PY = sys.executable


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8-sig'))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')


def today_usage():
    """Gemini's free-tier RPD resets at midnight Pacific time, not local midnight - key the
    counter off the Pacific calendar date so a run shortly after the real reset (mid-afternoon
    Taiwan time) sees a fresh budget instead of the stale Taiwan-date count."""
    u = load_json(USAGE_PATH, {})
    today = str(datetime.now(ZoneInfo('America/Los_Angeles')).date())
    if u.get('date') != today:
        u = {'date': today, 'requests': 0}
        save_json(USAGE_PATH, u)
    return u


def add_usage(n):
    u = today_usage()
    u['requests'] += n
    save_json(USAGE_PATH, u)
    return u['requests']


def write_progress(**kw):
    prog = load_json(PROGRESS_PATH, {})
    prog.update(kw)
    prog['updated_at'] = time.time()
    save_json(PROGRESS_PATH, prog)


def run(cmd, timeout):
    return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace',
                           timeout=timeout, cwd=str(WORK))


def download_audio(vid, url):
    # ignore .part/.ytdl etc. - those are incomplete downloads (e.g. left behind by a killed
    # process), not usable audio; without this check they get mistaken for a finished download
    existing = [p for p in VIDEOS_DIR.glob(f'{vid}.*') if p.suffix in AUDIO_EXTENSIONS]
    if existing:
        return existing[0]
    for stray in VIDEOS_DIR.glob(f'{vid}.*'):
        stray.unlink(missing_ok=True)
    ydl_opts = {'format': 'ba', 'outtmpl': str(VIDEOS_DIR / f'{vid}.%(ext)s'), 'quiet': True, 'no_warnings': True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        msg = str(e)
        print(f'  ✗ yt-dlp error for {vid}: {msg[:300]}')
        if any(sig in msg.lower() for sig in PERMANENT_ERRORS):
            skip_list = load_json(SKIP_LIST_PATH, {})
            skip_list[vid] = msg[:200]
            save_json(SKIP_LIST_PATH, skip_list)
            print(f'    (marked as permanently unavailable, will be skipped from now on)')
        return None
    found = list(VIDEOS_DIR.glob(f'{vid}.*'))
    return found[0] if found else None


def prepare_one(v):
    """Download + transcribe + build SRT. No Gemini quota involved."""
    vid, title = v['id'], v['title']
    srt_path = WORK / f'{vid}.srt'
    if srt_path.exists():
        return 'already_prepared'
    if vid in load_json(SKIP_LIST_PATH, {}):
        return 'permanently_unavailable'

    duration = v.get('duration') or 0
    write_progress(prep_current={'id': vid, 'title': title, 'stage': 'downloading', 'duration': duration})
    audio_path = download_audio(vid, v['url'])
    if not audio_path:
        return 'download_failed'

    json_path = WORK / f'{vid}.json'
    if not json_path.exists():
        write_progress(prep_current={'id': vid, 'title': title, 'stage': 'transcribing', 'duration': duration})
        r = run([PY, str(WORK / 'transcribe_words.py'), str(audio_path), str(json_path)], timeout=7200)
        if r.returncode != 0 or not json_path.exists():
            print(f'  ✗ transcription failed for {vid}: {r.stderr[-500:]}')
            return 'transcribe_failed'

    write_progress(prep_current={'id': vid, 'title': title, 'stage': 'building_srt', 'duration': duration})
    glossary_arg = [str(GLOSSARY_PATH)] if GLOSSARY_PATH.exists() else []
    r = run([PY, str(WORK / 'build_srt.py'), str(json_path), str(srt_path), *glossary_arg], timeout=600)
    if r.returncode != 0 or not srt_path.exists():
        print(f'  ✗ build_srt failed for {vid}: {r.stderr[-500:]}')
        return 'srt_failed'

    print(f'  ✓ prepared {vid}: {title[:40]}')
    return 'prepared'


def correct_one(v):
    """Gemini-correct an already-prepared SRT. Caller is responsible for the daily quota check."""
    vid, title = v['id'], v['title']
    srt_path = WORK / f'{vid}.srt'
    corrected = WORK / f'{vid}_corrected.srt'
    if corrected.exists():
        return 'already_done', 0
    if not srt_path.exists():
        return 'not_prepared', 0

    write_progress(correction_current={'id': vid, 'title': title, 'stage': 'correcting'})
    os.environ['SYSTEM_FILE'] = str(PROMPT_PATH)
    os.environ['CHUNK'] = '50'
    os.environ['CONTEXT'] = '10'
    r = run([PY, str(WORK / 'correct_srt.py'), str(srt_path), str(corrected), 'gemini-3.5-flash-lite'], timeout=900)
    made_requests = 0
    for line in (r.stdout or '').splitlines():
        if "'requests':" in line:
            try:
                made_requests = int(line.split("'requests':")[1].split(',')[0].strip())
            except Exception:
                pass
    if r.returncode != 0 or not corrected.exists():
        print(f'  ✗ correction failed for {vid}: {r.stderr[-500:]}')
        write_progress(correction_current=None)
        return 'correct_failed', made_requests
    print(f'  ✓ corrected {vid}: {title[:40]} ({made_requests} Gemini requests)')
    write_progress(correction_current=None)
    return 'done', made_requests


def pending_candidates(queue):
    """(vid, title, est_requests) for every prepared-but-uncorrected video in the queue."""
    out = []
    for v in queue:
        vid = v['id']
        if (WORK / f'{vid}_corrected.srt').exists() or not (WORK / f'{vid}.srt').exists():
            continue
        est = max(1, math.ceil((v.get('duration') or 0) / EST_SECONDS_PER_REQUEST))
        out.append((vid, v['title'], est))
    return out


def run_phase2_correction(queue):
    """Gemini-correct whatever is ALREADY prepared, up to today's budget. Runs first so a large,
    not-yet-fully-prepared queue never starves this phase of a chance to use today's quota."""
    print(f'=== Phase 2: Gemini correction (budget {DAILY_BUDGET}/day) ===')
    done = fail = 0
    quota_hit = False
    for i, v in enumerate(queue, 1):
        vid = v['id']
        corrected_path = WORK / f'{vid}_corrected.srt'
        if corrected_path.exists():
            done += 1
            continue
        if not (WORK / f'{vid}.srt').exists():
            continue   # not prepared yet - phase 1 will get to it

        est_requests = max(1, math.ceil((v.get('duration') or 0) / EST_SECONDS_PER_REQUEST))
        used = today_usage()['requests']
        if used + est_requests > DAILY_BUDGET:
            remaining = DAILY_BUDGET - used
            # is EVERY remaining candidate too big, or just this one (queue order isn't duration-sorted
            # once some videos are already done)? only the former is a genuine "done for today".
            pending = pending_candidates(queue)
            min_est = min((e for _, _, e in pending), default=None)
            exhausted = min_est is None or min_est > remaining
            print(f'\nDaily Gemini quota reached ({used}/{DAILY_BUDGET}, {remaining} left; '
                  f'shortest remaining candidate needs {min_est}). Stopping for today.')
            write_progress(quota_exhausted_today=exhausted, quota_remaining=remaining, correction_current=None)
            quota_hit = True
            break

        print(f'[correct {i}/{len(queue)}] {vid}: {v["title"][:50]}')
        try:
            result, made_requests = correct_one(v)
        except Exception as e:
            print(f'  ✗ unexpected error on {vid}: {str(e)[:300]}')
            result, made_requests = 'unexpected_error', est_requests
        add_usage(made_requests or est_requests if result != 'already_done' else 0)
        if result in ('done', 'already_done'):
            done += 1
        else:
            fail += 1
        write_progress(done_count=done, quota_exhausted_today=False)
    print(f'Phase 2 done this run: {done} corrected total, {fail} failed. '
          f"Today's Gemini usage: {today_usage()['requests']}/{DAILY_BUDGET}\n")
    return quota_hit


RECHECK_EVERY = 8   # re-check phase 2 after this many freshly-prepared videos, so a long phase 1 run
                     # (which can span many hours, even days, across a huge queue) never sits on a
                     # newly-reset quota for hours without using it


def run_phase1_prepare(queue):
    """Download + transcribe + build SRT for whatever isn't prepared yet. No daily cap, and never
    stops for the Gemini quota - phase 2 (correction) is a separate concern entirely, so a video
    being un-correctable today must never stop phase 1 from preparing the next one. Periodically
    re-invokes phase 2 (ignoring its result) so a quota reset mid-run gets used promptly instead of
    only being checked once at process start."""
    print(f'=== Phase 1: preparing (download + transcribe + build SRT), {len(queue)} queued ===')
    prepared = skipped = 0
    since_recheck = 0
    for i, v in enumerate(queue, 1):
        vid = v['id']
        if (WORK / f'{vid}_corrected.srt').exists() or (WORK / f'{vid}.srt').exists():
            continue
        print(f'[prep {i}/{len(queue)}] {vid}: {v["title"][:50]}')
        try:
            result = prepare_one(v)
        except Exception as e:
            print(f'  ✗ unexpected error on {vid}: {str(e)[:300]}')
            result = 'unexpected_error'
        if result in ('prepared', 'already_prepared'):
            prepared += 1
            since_recheck += 1
        else:
            skipped += 1
        if since_recheck >= RECHECK_EVERY:
            since_recheck = 0
            run_phase2_correction(queue)   # ignore the return value - phase 1 keeps going regardless
            print(f'=== Phase 1: resuming (download + transcribe + build SRT) ===')
    print(f'Phase 1 done: {prepared} newly prepared, {skipped} skipped/failed.\n')
    return prepared, skipped


def ensure_whisper_model():
    """First run on a fresh install downloads the ~1.6GB model; show that in the UI instead of a silent hang."""
    write_progress(prep_current={'id': '', 'title': '語音辨識模型（首次使用需下載約 1.6GB，請耐心等候）',
                                  'stage': 'downloading_model'})
    from faster_whisper.utils import download_model
    download_model('large-v3-turbo')
    write_progress(prep_current=None)


LAST_REFRESH_PATH = WORK / 'last_channel_refresh.json'
REFRESH_EVERY_SECONDS = 20 * 3600   # ~once/day, but never more than every 20h even if the task fires hourly


def maybe_refresh_channel():
    """Throttled call into refresh_channel.py so new livestreams join the queue automatically,
    without hitting YouTube's channel pages on every hourly scheduled-task run."""
    last = load_json(LAST_REFRESH_PATH, {})
    if time.time() - last.get('ts', 0) < REFRESH_EVERY_SECONDS:
        return
    print('=== Checking channel for new videos (refresh_channel.py) ===')
    try:
        subprocess.run([sys.executable, str(WORK / 'refresh_channel.py')],
                        cwd=WORK, timeout=300, check=False)
    except Exception as e:
        print(f'  channel refresh failed: {str(e)[:300]}')
    LAST_REFRESH_PATH.write_text(json.dumps({'ts': time.time()}), encoding='utf-8')


def main():
    maybe_refresh_channel()

    queue = load_json(QUEUE_PATH, [])
    if not queue:
        print('Queue is empty. Build it from the channel picker first.')
        return

    write_progress(total=len(queue), done_count=0, prep_current=None, correction_current=None, stopped_reason=None)

    # phase 2 first, so a large not-yet-fully-prepared queue never starves it of a chance to run;
    # phase 1 always runs afterward regardless of whether phase 2 hit its quota - they are independent
    quota_hit = run_phase2_correction(queue)

    from check_gpu import check_gpu
    gpu_ok, gpu_msg = check_gpu()
    if gpu_ok:
        write_progress(gpu_error=None)
        ensure_whisper_model()
        run_phase1_prepare(queue)
    else:
        print(f'=== Phase 1 skipped: {gpu_msg} ===')
        write_progress(gpu_error=gpu_msg)

    write_progress(prep_current=None, stopped_reason='quota_reached' if quota_hit else 'queue_complete')


if __name__ == '__main__':
    main()
