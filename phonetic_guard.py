"""Phonetic guard for LLM ASR corrections.

ASR mistakes are (near-)homophones, so a proposed edit is only plausible if every replaced character
sounds like the character it replaces. This rejects invented words, pasted neighbour text and edits
to in-game jargon whose sound is unrelated to the 'fix'.
"""
import difflib, re
from pypinyin import Style, pinyin

# merge initials that ASR (and Taiwan Mandarin) commonly confuse: retroflex/flat, aspirated/unaspirated, n/l/r, f/h
_INIT_MAP = {'zh': 'z', 'ch': 'z', 'c': 'z', 'sh': 's', 'q': 'j', 'p': 'b', 't': 'd', 'k': 'g',
             'l': 'n', 'r': 'n', 'f': 'h'}
_FIN_MAP = {'ang': 'an', 'eng': 'en', 'ing': 'in', 'ong': 'un', 'iang': 'ian', 'uang': 'uan', 'iong': 'iun',
            'ueng': 'un', 'un': 'un', 'uen': 'un'}


def _sound(ch):
    ini = pinyin(ch, style=Style.INITIALS, strict=False, errors=lambda x: [''])[0][0]
    fin = pinyin(ch, style=Style.FINALS, strict=False, errors=lambda x: [''])[0][0]
    return _INIT_MAP.get(ini, ini), _FIN_MAP.get(fin, fin)


def char_score(a, b):
    """1.0 same sound, 0.5 shares initial or final, 0 unrelated."""
    if a == b:
        return 1.0
    if not ('㐀' <= a <= '鿿' and '㐀' <= b <= '鿿'):
        return 1.0 if a.lower() == b.lower() else 0.0
    (i1, f1), (i2, f2) = _sound(a), _sound(b)
    hits = (i1 == i2) + (f1 == f2)
    return {2: 1.0, 1: 0.5, 0: 0.0}[hits]


def phonetic_ok(old, new, max_changed=6, min_avg=0.75):
    """True if new differs from old only by same-length, similar-sounding character swaps."""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    changed = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            continue
        if tag != 'replace' or (i2 - i1) != (j2 - j1):
            # allow pure trailing/leading plural-like ASCII growth, e.g. window -> windows
            if tag == 'insert' and re.fullmatch(r'[sS]', new[j1:j2]) and i1 > 0 and old[i1 - 1].isascii():
                continue
            return False, f'non-substitution ({tag})'
        changed += i2 - i1
        scores = [char_score(a, b) for a, b in zip(old[i1:i2], new[j1:j2])]
        if min(scores) == 0.0 or sum(scores) / len(scores) < min_avg:
            return False, f'sounds unlike: {old[i1:i2]}->{new[j1:j2]}'
    if changed > max_changed:
        return False, 'too many changes'
    return True, ''


if __name__ == '__main__':
    tests = [('測診團隊', '策展團隊'), ('雞腿', '體力'), ('佛霞的更新', '碧藍航線的更新'), ('記憶體', '記憶卡'),
             ('不可名創', '不可名狀'), ('酥泡', '舒跑'), ('聖卡', '想開'), ('冰冰的時候', '聽歌的時候'),
             ('魂牽夢影', '魂牽夢縈'), ('灌二手店', '逛二手店'), ('萬劫不符沒有回頭路的', '會不會有些事情是')]
    for o, n in tests:
        print(f'{o} -> {n}: {phonetic_ok(o, n)}')
