"""Self-healing wrapper around batch_process.py.

Called by the hourly scheduled task instead of batch_process.py directly. If a previous run is
still alive but hasn't updated batch_progress.json in a while (e.g. its GPU subprocess got zombied
by a sleep/resume cycle), this kills every related process and starts a clean run - so the pipeline
recovers within an hour without anyone needing to notice or intervene.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

WORK = Path(__file__).resolve().parent
PROGRESS_PATH = WORK / 'batch_progress.json'
FAIL_STREAK_PATH = WORK / 'watchdog_fail_streak.json'
STALE_SECONDS = 25 * 60   # no single normal step should take this long; if it has, assume it's stuck
MAX_SKIP_STREAK = 3   # after this many consecutive hourly cycles that couldn't confirm a clean kill,
                       # start a fresh instance anyway - a truly unkillable zombie (GPU-driver-wedged
                       # after sleep/resume) once blocked the whole pipeline for 3 days straight

# transcription of a long livestream can legitimately take longer than STALE_SECONDS on this GPU -
# a 9483s (2.6h) stream once got killed and restarted every hour for 14h straight because the
# watchdog kept misjudging genuinely-slow-but-healthy transcription as stuck. Scale the allowance
# for that stage by the video's own duration instead of using a flat cutover.
TRANSCRIBE_SECONDS_PER_AUDIO_SECOND = 0.5   # generous: assume transcription could take up to 0.5x realtime

MATCH_PATTERNS = ['batch_process.py', 'correct_srt.py', 'transcribe_words.py', 'build_srt.py']


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding='utf-8-sig'))
        except Exception:
            return default
    return default


def save_json(path, data):
    path.write_text(json.dumps(data), encoding='utf-8')


def stale_threshold(progress):
    prep = (progress or {}).get('prep_current') or {}
    if prep.get('stage') == 'downloading_model':
        return 3 * 3600   # one-time ~1.6GB download, may be slow on a weak connection
    if prep.get('stage') == 'transcribing' and prep.get('duration'):
        return max(STALE_SECONDS, int(prep['duration'] * TRANSCRIBE_SECONDS_PER_AUDIO_SECOND))
    return STALE_SECONDS


def find_related_pids():
    # filter by Name='python.exe' server-side first - much faster than pulling every process and
    # filtering client-side, and avoids the WMI query itself hanging past its own timeout (this
    # crashed the watchdog with an uncaught TimeoutExpired once, silently skipping that hour)
    filt = ' -or '.join(f'$_.CommandLine -match "{p}"' for p in MATCH_PATTERNS)
    ps = (f"Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          f"Where-Object {{ {filt} }} | Select-Object -ExpandProperty ProcessId")
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', ps],
                            capture_output=True, text=True, timeout=45)
        return [int(x) for x in r.stdout.split() if x.strip().isdigit()]
    except Exception as e:
        print(f'{time.strftime("%Y-%m-%d %H:%M:%S")}: find_related_pids failed ({str(e)[:200]}) - '
              f'treating as none found for this check.')
        return []


def kill_pids(pids):
    if not pids:
        return
    # taskkill /T kills the whole process tree in one native call - more reliable than
    # Stop-Process against a GPU-driver-zombied process (sleep/resume can leave a process
    # stuck in an uninterruptible wait that Stop-Process alone repeatedly fails to clear)
    for pid in pids:
        try:
            subprocess.run(['taskkill', '/F', '/T', '/PID', str(pid)],
                            capture_output=True, text=True, timeout=30)
        except Exception:
            pass
    try:
        ids = ','.join(str(p) for p in pids)
        subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                         f'Stop-Process -Id {ids} -Force -ErrorAction SilentlyContinue'],
                        capture_output=True, text=True, timeout=30)
    except Exception:
        pass


def main():
    pids = find_related_pids()
    if pids:
        stale = True
        threshold = STALE_SECONDS
        if PROGRESS_PATH.exists():
            try:
                progress = json.loads(PROGRESS_PATH.read_text(encoding='utf-8-sig'))
                threshold = stale_threshold(progress)
                age = time.time() - progress.get('updated_at', 0)
                stale = age > threshold
            except Exception:
                pass
        if not stale:
            print(f'{time.strftime("%Y-%m-%d %H:%M:%S")}: previous run still active and healthy, skipping this trigger.')
            return
        print(f'{time.strftime("%Y-%m-%d %H:%M:%S")}: previous run looks stuck (no progress update within '
              f'{threshold//60} min) - killing {len(pids)} related process(es) and starting fresh.')
        # verify the kill actually worked before launching a new instance - a fire-and-forget kill
        # that silently fails would otherwise leave two instances running the same queue at once.
        # CUDA context teardown can take a while, so retry with more patience than a quick 15s burst.
        for attempt in range(10):
            kill_pids(pids)
            time.sleep(6)
            pids = find_related_pids()
            if not pids:
                break
        if pids:
            streak = load_json(FAIL_STREAK_PATH, {}).get('count', 0) + 1
            save_json(FAIL_STREAK_PATH, {'count': streak})
            if streak < MAX_SKIP_STREAK:
                print(f'{time.strftime("%Y-%m-%d %H:%M:%S")}: could not clear {len(pids)} process(es) after 10 '
                      f'attempts ({pids}) - skipping this trigger (fail streak {streak}/{MAX_SKIP_STREAK}).')
                return
            print(f'{time.strftime("%Y-%m-%d %H:%M:%S")}: could not clear {len(pids)} process(es) '
                  f'{streak} triggers in a row - starting a fresh instance anyway rather than staying '
                  f'blocked indefinitely (accepting the residual risk of a stray old process).')

    save_json(FAIL_STREAK_PATH, {'count': 0})
    print(f'{time.strftime("%Y-%m-%d %H:%M:%S")}: starting batch_process.py')
    subprocess.run([sys.executable, str(WORK / 'batch_process.py')], cwd=str(WORK))


if __name__ == '__main__':
    main()
