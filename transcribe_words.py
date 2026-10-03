"""Word-level transcription -> JSON for the SRT builder.

Usage: python work/transcribe_words.py <audio.wav> <out.json> [cuda|cpu]
Env:   VAD_JSON='{"min_silence_duration_ms":400}'  overrides silero VAD parameters (default: library defaults)
       PROMPT='...'                                 overrides the initial prompt
"""
import os, glob, json, time, site, sys

audio, out = sys.argv[1], sys.argv[2]
device = sys.argv[3] if len(sys.argv) > 3 else 'cuda'

for base in site.getsitepackages():
    for b in glob.glob(os.path.join(base, 'nvidia', '*', 'bin')):
        os.add_dll_directory(b)
        os.environ['PATH'] = b + os.pathsep + os.environ['PATH']

from faster_whisper import WhisperModel

VAD = json.loads(os.environ.get('VAD_JSON', '{}')) or None
PROMPT = os.environ.get('PROMPT', '以下是繁體中文的直播雜談。')

t0 = time.time()
model = WhisperModel('large-v3-turbo', device=device, compute_type='float16' if device == 'cuda' else 'int8')
print('model loaded', round(time.time() - t0, 1), 's', flush=True)

segs, info = model.transcribe(
    audio,
    language='zh',
    vad_filter=True,
    vad_parameters=VAD,
    word_timestamps=True,
    initial_prompt=PROMPT,
    beam_size=5,
    condition_on_previous_text=False,
)
result = []
for s in segs:
    result.append({
        'start': round(s.start, 2), 'end': round(s.end, 2), 'text': s.text.strip(),
        'avg_logprob': round(s.avg_logprob, 3), 'no_speech_prob': round(s.no_speech_prob, 3),
        'compression_ratio': round(s.compression_ratio, 2),
        'words': [{'w': w.word, 's': round(w.start, 2), 'e': round(w.end, 2), 'p': round(w.probability, 3)}
                  for w in (s.words or [])],
    })
    if len(result) % 100 == 0:
        print(len(result), 'segments,', round(s.end), 's audio done,', round(time.time() - t0), 's elapsed', flush=True)
with open(out, 'w', encoding='utf8') as f:
    json.dump({'duration': info.duration, 'segments': result}, f, ensure_ascii=False)
print('DONE', len(result), 'segments', round(time.time() - t0), 's total', flush=True)
