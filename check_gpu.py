"""NVIDIA GPU / CUDA driver check.

This tool requires an NVIDIA GPU (faster-whisper's CUDA backend) - there is no CPU fallback.
Call check_gpu() early so a missing/broken GPU setup fails with one clear message instead of
a cryptic ctranslate2/CUDA traceback buried deep inside a transcription subprocess.
"""
import subprocess


def check_gpu():
    """Returns (ok: bool, message: str)."""
    try:
        r = subprocess.run(['nvidia-smi'], capture_output=True, text=True, errors='replace', timeout=15)
    except FileNotFoundError:
        return False, (
            '找不到 NVIDIA 顯示卡驅動程式（nvidia-smi 無法執行）。\n'
            '本工具需要 NVIDIA 顯示卡才能進行語音轉錄，請確認：\n'
            '  1. 電腦有安裝 NVIDIA 獨立顯示卡\n'
            '  2. 已安裝最新的 NVIDIA 顯示卡驅動程式（https://www.nvidia.com/download/index.aspx）\n'
            '目前不支援沒有 NVIDIA 顯示卡的電腦。'
        )
    except Exception as e:
        return False, f'檢查顯示卡時發生錯誤：{e}'

    if r.returncode != 0:
        return False, (
            'NVIDIA 顯示卡驅動程式似乎沒有正常運作（nvidia-smi 執行失敗）。\n'
            '請確認顯示卡驅動程式已正確安裝，必要時重新安裝最新版驅動程式後再試一次。\n'
            f'詳細錯誤：{r.stderr.strip()[:300]}'
        )
    return True, r.stdout.strip()


if __name__ == '__main__':
    ok, msg = check_gpu()
    print('✓ 偵測到 NVIDIA 顯示卡，可以開始使用。' if ok else f'✗ {msg}')
    raise SystemExit(0 if ok else 1)
