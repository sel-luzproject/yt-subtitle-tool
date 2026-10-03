"""Build a YouTube-importable SRT from word-level ASR JSON.

Usage: python work/build_srt.py <words.json> <out.srt> [glossary.txt]
Also writes <out>.review.tsv listing low-confidence cues for spot checks.
"""
import json, re, sys
from opencc import OpenCC

MAX_CHARS = 18          # per cue (single line); Chinese subtitle readability
MIN_CHARS = 4
MAX_DUR = 6.5
MIN_DUR = 0.8
GAP_BREAK = 0.6         # pause (s) that may end a cue once it holds >= 6 chars
LONG_GAP = 1.0          # pause (s) that always ends a cue, however short
END_PAD = 0.15

STRONG = set('。！？!?…')
WEAK = set('，、；,;：')

cc = OpenCC('s2twp')    # simplified -> Taiwan traditional with phrase conversion


def load_glossary(path):
    rules = []
    if path:
        for line in open(path, encoding='utf8'):
            line = line.strip()
            if line and not line.startswith('#') and '=>' in line:
                a, b = line.split('=>', 1)
                rules.append((a.strip(), b.strip()))
    return rules


def srt_time(t):
    ms = int(round(t * 1000)); h, r = divmod(ms, 3600000); m, r = divmod(r, 60000); s, ms = divmod(r, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def keep(seg):
    if seg['no_speech_prob'] > 0.6 and seg['avg_logprob'] < -1.0:
        return False
    if seg['compression_ratio'] > 2.4:   # repetition loop / hallucination
        return False
    return bool(seg['words'])


def split_segment(seg):
    """Split one ASR segment into cue dicts using word timing and punctuation."""
    cues, cur = [], []

    def flush():
        if cur:
            cues.append({'toks': list(cur), 'lp': seg['avg_logprob']})
            cur.clear()

    def clen(toks):
        return sum(len(re.sub(r'[\s，。、；：！？,.;:!?…]', '', t['w'])) for t in toks)

    prev_end = None
    for w in seg['words']:
        text = w['w']
        if cur and prev_end is not None:
            gap = w['s'] - prev_end
            if gap >= LONG_GAP or (gap > GAP_BREAK and clen(cur) >= 6):
                carry = None
                # word alignment often pins the word before a long silence to the wrong side of it;
                # a 1-2 char tail followed by a long pause really belongs to what comes next
                if gap >= 2.0 and len(cur) >= 2 and len(cur[-1]['w'].strip()) <= 2 \
                        and cur[-1]['w'].strip()[-1:] not in STRONG | WEAK:
                    carry = dict(cur.pop())
                    carry['s'], carry['e'] = w['s'] - 0.35, w['s']
                flush()
                if carry:
                    cur.append(carry)
        cur.append(w)
        prev_end = w['e']
        n = clen(cur)
        last = text.strip()[-1:] if text.strip() else ''
        dur = cur[-1]['e'] - cur[0]['s']
        if last in STRONG and n >= 5:
            flush()
        elif last in WEAK and n >= 9:
            flush()
        elif n >= MAX_CHARS or dur >= MAX_DUR:
            flush()
    flush()
    return cues


CJK = r'[㐀-鿿]'


def clean(text):
    text = text.strip()
    text = text.replace('?', '？').replace('!', '！')
    # ASR tokens carry stray spaces between Chinese characters; only keep spaces next to Latin/digits
    text = re.sub(rf'(?<={CJK})\s+(?={CJK})', '', text)
    text = re.sub(r'[。．.]+$', '', text)
    text = re.sub(r'^[，、；：,;:\s]+|[，、；：,;:\s]+$', '', text)
    text = re.sub(r'[，、；：,;:]', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def main():
    src, dst = sys.argv[1], sys.argv[2]
    rules = load_glossary(sys.argv[3] if len(sys.argv) > 3 else None)
    data = json.load(open(src, encoding='utf8'))

    cues = []
    dropped = 0
    for seg in data['segments']:
        if not keep(seg):
            dropped += 1
            continue
        for c in split_segment(seg):
            toks = c['toks']
            text = clean(''.join(t['w'] for t in toks))
            if not text:
                continue
            minp = min(t['p'] for t in toks)
            meanp = sum(t['p'] for t in toks) / len(toks)
            cues.append({'s': toks[0]['s'], 'e': toks[-1]['e'] + END_PAD, 'text': text,
                         'lp': c['lp'], 'minp': minp, 'meanp': meanp})

    # merge cues that are too short into a neighbour when it still reads well
    merged = []
    for c in cues:
        if merged:
            p = merged[-1]
            if (len(p['text']) < MIN_CHARS or len(c['text']) < MIN_CHARS) and \
               c['s'] - p['e'] < 0.5 and len(p['text']) + len(c['text']) + 1 <= MAX_CHARS + 4:
                p['text'] = p['text'] + ' ' + c['text']
                p['e'] = c['e']; p['minp'] = min(p['minp'], c['minp']); p['meanp'] = (p['meanp'] + c['meanp']) / 2
                p['lp'] = min(p['lp'], c['lp'])
                continue
        merged.append(dict(c))
    cues = merged

    # text conversion + glossary, then timing rules
    for c in cues:
        c['text'] = cc.convert(c['text'])
        for a, b in rules:
            c['text'] = c['text'].replace(a, b)
    for i, c in enumerate(cues):
        nxt = cues[i + 1]['s'] if i + 1 < len(cues) else c['e'] + 1
        c['e'] = min(c['e'], nxt - 0.02)
        if c['e'] - c['s'] < MIN_DUR:
            c['e'] = min(c['s'] + MIN_DUR, nxt - 0.02)
        if c['e'] <= c['s']:
            c['e'] = c['s'] + 0.3

    with open(dst, 'w', encoding='utf-8', newline='\n') as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{srt_time(c['s'])} --> {srt_time(c['e'])}\n{c['text']}\n\n")

    # NOTE: confidence only catches unsure lines; confident homophone errors (e.g. 不可名創) slip through
    flagged = [c for c in cues if c['meanp'] < 0.7 or c['lp'] < -0.8]
    with open(dst + '.review.tsv', 'w', encoding='utf-8-sig', newline='\n') as f:
        f.write('time\ttext\tmin_word_prob\tsegment_logprob\n')
        for c in flagged:
            t = int(c['s'])
            f.write(f"{t//3600}:{t%3600//60:02d}:{t%60:02d}\t{c['text']}\t{c['minp']:.2f}\t{c['lp']:.2f}\n")

    lens = [len(c['text']) for c in cues]
    print(f'cues={len(cues)} dropped_segments={dropped} flagged={len(flagged)} '
          f'chars/cue avg={sum(lens)/len(lens):.1f} max={max(lens)}')


if __name__ == '__main__':
    main()
