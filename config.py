"""Per-install customer configuration (config.json): Gemini API key + the customer's own channel URL.

Every script that used to hardcode a single person's channel/API key should import this instead,
so one install of the tool can be handed to a different customer just by running the setup wizard.
"""
import json
from pathlib import Path

WORK = Path(__file__).resolve().parent
CONFIG_PATH = WORK / 'config.json'

DEFAULTS = {
    'gemini_api_key': '',
    'channel_url': '',   # e.g. https://www.youtube.com/@somechannel (streams/videos tabs derived from this)
    'setup_complete': False,
}


def load_config():
    if CONFIG_PATH.exists():
        cfg = {**DEFAULTS, **json.loads(CONFIG_PATH.read_text(encoding='utf-8-sig'))}
        return cfg
    return dict(DEFAULTS)


def save_config(**updates):
    cfg = load_config()
    cfg.update(updates)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding='utf-8')
    return cfg


def channel_urls():
    """(streams_url, videos_url) derived from the configured channel URL's handle/base."""
    base = load_config()['channel_url'].rstrip('/')
    for suffix in ('/videos', '/streams', '/featured'):
        if base.endswith(suffix):
            base = base[:-len(suffix)]
            break
    return f'{base}/streams', f'{base}/videos'
