"""LLM ASR-error correction for an SRT (diff-only output), Ollama or Gemini backend.

Usage: python work/correct_srt.py <in.srt> <out.srt> <model> [first_cue] [last_cue]
Env:   BACKEND=ollama|gemini (default: gemini if model starts with 'gemini', else ollama)
       CHUNK=<cues per request, default 25>   SYSTEM_FILE=<prompt file>   THINK=1 (ollama only)
Writes <out>.changes.tsv listing every proposed edit and whether the guard accepted it.
The Gemini key is read from GEMINI_API_KEY (process env, else the user's registry env) and never printed.
"""
import json, os, re, sys, time, urllib.error, urllib.request

src, dst, MODEL = sys.argv[1], sys.argv[2], sys.argv[3]
FIRST = int(sys.argv[4]) if len(sys.argv) > 4 else 1
LAST = int(sys.argv[5]) if len(sys.argv) > 5 else 10**9
BACKEND = os.environ.get('BACKEND') or ('gemini' if MODEL.startswith('gemini') else 'ollama')
CHUNK, CONTEXT = int(os.environ.get('CHUNK', 25)), int(os.environ.get('CONTEXT', 6))
THINK = os.environ.get('THINK') == '1'
PHONETIC = os.environ.get('PHONETIC', '1') == '1'          # reject edits whose new chars do not sound like the old
PHONETIC_AVG = float(os.environ.get('PHONETIC_AVG', 0.5))  # 0.5 balanced (default), 0.75 strict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from phonetic_guard import phonetic_ok

SYSTEM = open(os.environ.get('SYSTEM_FILE', 'work/prompt_b.txt'), encoding='utf8').read()

EDITS_SCHEMA = {"type": "object", "properties": {"edits": {"type": "array", "items": {
    "type": "object", "properties": {"id": {"type": "integer"}, "text": {"type": "string"}},
    "required": ["id", "text"]}}}, "required": ["edits"]}
GEMINI_SCHEMA = {"type": "OBJECT", "properties": {"edits": {"type": "ARRAY", "items": {
    "type": "OBJECT", "properties": {"id": {"type": "INTEGER"}, "text": {"type": "STRING"}},
    "required": ["id", "text"]}}}, "required": ["edits"]}

stats = {'requests': 0, 'retries': 0, 'prompt': 0, 'out': 0, 'thought': 0}


def gemini_key():
    k = os.environ.get('GEMINI_API_KEY')
    if not k:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as h:
                k = winreg.QueryValueEx(h, 'GEMINI_API_KEY')[0]
        except OSError:
            pass
    if not k:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from config import load_config
        k = load_config()['gemini_api_key']
    if not k:
        raise RuntimeError('No Gemini API key configured - run the setup wizard first.')
    return k


def ask_ollama(prompt):
    body = json.dumps({'model': MODEL, 'stream': False, 'think': THINK, 'format': EDITS_SCHEMA,
                       'messages': [{'role': 'system', 'content': SYSTEM}, {'role': 'user', 'content': prompt}],
                       'options': {'temperature': 0, 'num_ctx': 8192}}).encode('utf8')
    req = urllib.request.Request('http://127.0.0.1:11434/api/chat', body, {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as r:
        res = json.loads(r.read())
    stats['prompt'] += res.get('prompt_eval_count', 0); stats['out'] += res.get('eval_count', 0)
    return res['message']['content']


def ask_gemini(prompt):
    url = f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent'
    body = json.dumps({'systemInstruction': {'parts': [{'text': SYSTEM}]},
                       'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
                       'generationConfig': {'temperature': 0, 'responseMimeType': 'application/json',
                                            'responseSchema': GEMINI_SCHEMA}}).encode('utf8')
    for attempt in range(6):
        req = urllib.request.Request(url, body, {'Content-Type': 'application/json', 'x-goog-api-key': gemini_key()})
        try:
            stats['requests'] += 1
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read())
            u = res.get('usageMetadata', {})
            stats['prompt'] += u.get('promptTokenCount', 0); stats['out'] += u.get('candidatesTokenCount', 0)
            stats['thought'] += u.get('thoughtsTokenCount', 0)
            return res['candidates'][0]['content']['parts'][0]['text']
        except urllib.error.HTTPError as e:
            msg = e.read().decode('utf8', 'replace')[:300]
            if e.code in (429, 500, 503) and attempt < 5:
                stats['retries'] += 1
                wait = 20 * (attempt + 1)
                print(f'  HTTP {e.code}, retry in {wait}s: {msg[:160]}', flush=True)
                time.sleep(wait)
                continue
            raise RuntimeError(f'HTTP {e.code}: {msg}')


ask = ask_gemini if BACKEND == 'gemini' else ask_ollama


def parse_srt(path):
    cues = []
    for b in open(path, encoding='utf-8').read().strip().split('\n\n'):
        l = b.split('\n')
        cues.append({'id': int(l[0]), 'time': l[1], 'text': '\n'.join(l[2:])})
    return cues


def cjk_len(t):
    return len(re.sub(r'\s', '', t))


def guard(old, new):
    """Accept only small, local edits."""
    if new == old or not new.strip():
        return False, 'same/empty'
    lo, ln = cjk_len(old), cjk_len(new)
    if abs(ln - lo) > max(3, 0.3 * lo):
        return False, 'length change'
    if lo >= 6 and len(old) == len(new) and sum(1 for a, b in zip(old, new) if a != b) > 0.5 * lo:
        return False, 'too many chars changed'
    if re.search(r'[a-zA-Z]{3,}', new) and not re.search(r'[a-zA-Z]{3,}', old):
        return False, 'introduced latin'
    if PHONETIC:
        ok, why = phonetic_ok(old, new, min_avg=PHONETIC_AVG)
        if not ok:
            return False, why
    return True, ''


def main():
    cues = parse_srt(src)
    by_id = {c['id']: c for c in cues}
    todo = [c for c in cues if FIRST <= c['id'] <= LAST]
    rows, t0 = [], time.time()
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        ctx = [by_id[j] for j in range(chunk[0]['id'] - CONTEXT, chunk[0]['id']) if j in by_id]
        lines = [f"{c['id']}|{c['text']} (上下文)" for c in ctx] + [f"{c['id']}|{c['text']}" for c in chunk]
        try:
            edits = json.loads(ask('\n'.join(lines))).get('edits', [])
        except Exception as e:
            print('chunk', chunk[0]['id'], 'failed:', str(e)[:300], flush=True)
            continue
        ids = {c['id'] for c in chunk}
        for e in edits:
            cid, new = e.get('id'), (e.get('text') or '').strip()
            if cid not in ids:
                continue
            old = by_id[cid]['text']
            ok, why = guard(old, new)
            rows.append((cid, by_id[cid]['time'][:8], old, new, ok, why))
            if ok:
                by_id[cid]['text'] = new
        print(f'chunk {chunk[0]["id"]}-{chunk[-1]["id"]}: {len(edits)} edits | {time.time()-t0:.0f}s', flush=True)

    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        for c in cues:
            f.write(f"{c['id']}\n{c['time']}\n{c['text']}\n\n")
    with open(dst + '.changes.tsv', 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write('cue\ttime\told\tnew\taccepted\treason\n')
        for r in rows:
            f.write('\t'.join(map(str, r)) + '\n')
    acc = sum(1 for r in rows if r[4])
    echoes = sum(1 for r in rows if r[5] == 'same/empty')
    print(f'DONE {MODEL}: proposed={len(rows)} accepted={acc} echoes={echoes} rejected_other={len(rows)-acc-echoes} | '
          f'{time.time()-t0:.0f}s | {stats}')


if __name__ == '__main__':
    main()
