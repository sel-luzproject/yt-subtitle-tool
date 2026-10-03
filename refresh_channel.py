"""Incrementally refresh channel_all.json with any new videos published since the last fetch.

Usage: python refresh_channel.py
Only ADDS new videos - never touches 'order' or skip status of ones already known, and rebuilds
queue.json afterward so new videos join the processing queue automatically.
"""
import json
import subprocess
import sys
from pathlib import Path

from config import channel_urls

WORK = Path(__file__).resolve().parent


def fetch_flat(url):
    import yt_dlp
    ydl_opts = {'quiet': True, 'no_warnings': True, 'extract_flat': True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    return [{'id': e['id'], 'title': e.get('title'), 'duration': e.get('duration'),
              'url': e.get('url') or f"https://www.youtube.com/watch?v={e['id']}"}
             for e in info.get('entries', []) if e]


def main():
    from config import load_config
    if not load_config()['channel_url']:
        print('channel_url not configured yet - run the setup wizard first.')
        return

    all_path = WORK / 'channel_all.json'
    existing = json.loads(all_path.read_text(encoding='utf-8-sig')) if all_path.exists() else []
    known_ids = {v['id'] for v in existing}
    by_id = {v['id']: v for v in existing}

    streams_url, videos_url = channel_urls()
    print('fetching current streams list...', flush=True)
    streams = fetch_flat(streams_url)
    print('fetching current videos list...', flush=True)
    videos = fetch_flat(videos_url)

    min_order_streams = min((v.get('order', 0) for v in existing if v.get('source') == 'streams'), default=0)
    min_order_videos = min((v.get('order', 0) for v in existing if v.get('source') == 'videos'), default=0)

    added = []
    # scan the WHOLE list rather than stopping at the first known id - a pinned/scheduled entry
    # (e.g. an upcoming-premiere announcement) can sit at the top out of chronological order and
    # would otherwise make an early-break miss genuinely new videos right behind it
    for i, e in enumerate(streams):
        if e['id'] in known_ids:
            continue
        new_order = min_order_streams - (len(streams) - i)   # keeps them sorted newest-first ahead of old min
        entry = {**e, 'source': 'streams', 'order': new_order}
        by_id[e['id']] = entry
        added.append(entry)
    for i, e in enumerate(videos):
        if e['id'] in known_ids:
            continue
        new_order = min_order_videos - (len(videos) - i)
        entry = {**e, 'source': 'videos', 'order': new_order}
        by_id[e['id']] = entry
        added.append(entry)

    if not added:
        print('no new videos found - channel list is already up to date.')
        return

    merged = list(by_id.values())
    all_path.write_text(json.dumps(merged, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'added {len(added)} new video(s):')
    for e in added:
        dur = f"{(e['duration'] or 0)//60}分" if e.get('duration') else '?'
        print(f"  {e['id']}  {dur}  {e['title'][:50]}")

    rebuild_queue()


def rebuild_queue():
    """Mirrors server.py's /api/queue/build logic, done locally so this doesn't depend on the
    review server being up."""
    status_path = WORK / 'channel_status.json'
    status = json.loads(status_path.read_text(encoding='utf-8-sig')) if status_path.exists() else {}
    videos = json.loads((WORK / 'channel_all.json').read_text(encoding='utf-8-sig'))
    done_ids = {p.stem.removesuffix('_corrected') for p in WORK.glob('*_corrected.srt')}
    todo = [v for v in videos
            if not status.get(v['id'], {}).get('skip')
            and v['id'] not in done_ids
            and (v.get('duration') or 0) >= 300]
    todo.sort(key=lambda v: v.get('duration') or 0)
    (WORK / 'queue.json').write_text(json.dumps(todo, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'queue rebuilt: {len(todo)} videos queued')


if __name__ == '__main__':
    main()
