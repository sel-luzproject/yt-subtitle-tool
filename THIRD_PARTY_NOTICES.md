# 第三方元件授權

本專案原始碼以 MIT 授權（見 `LICENSE`）。**原始碼倉庫本身不含任何下列元件**；它們只在你自行安裝相依套件，或使用 Releases 提供的安裝程式時才會出現。授權資訊取自各套件發佈的中繼資料（版本為建置安裝程式時所用）。

## Python 套件

| 套件 | 版本 | 授權 |
|---|---|---|
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| av (PyAV) | 18.1.0 | BSD-3-Clause（輪檔內含 FFmpeg 函式庫，其授權請參考 PyAV 專案說明） |
| certifi | 2026.7.22 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| colorama | 0.4.6 | BSD |
| ctranslate2 | 4.8.2 | MIT |
| fastapi | 0.141.1 | MIT |
| faster-whisper | 1.2.1 | MIT |
| filelock | 4.0.1 | MIT |
| flatbuffers | 25.12.19 | Apache-2.0 |
| fsspec | 2026.9.0 | BSD-3-Clause |
| h11 | 0.16.0 | MIT |
| hf-xet | 1.6.0 | Apache-2.0 |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD |
| huggingface-hub | 1.32.0 | Apache-2.0 |
| idna | 3.20 | BSD-3-Clause |
| numpy | 2.5.3 | BSD-3-Clause 及其他寬鬆授權 |
| onnxruntime | 1.30.0 | MIT |
| opencc-python-reimplemented | 0.1.7 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 或 BSD-2-Clause |
| protobuf | 7.36.2 | BSD-3-Clause |
| pydantic / pydantic-core | 2.13.5 / 2.46.5 | MIT |
| pypinyin | 0.55.0 | MIT |
| pyyaml | 6.0.3 | MIT |
| starlette | 1.6.0 | BSD-3-Clause |
| tokenizers | 0.23.2 | Apache-2.0 |
| tqdm | 4.70.1 | MPL-2.0 與 MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.4 | MIT |
| tzdata | 2026.4 | Apache-2.0 |
| uvicorn | 0.53.0 | BSD-3-Clause |
| yt-dlp | 2026.8.19 | Unlicense |

安裝程式另外內附 Python 3.13 執行環境（PSF 授權）。

## NVIDIA CUDA 執行函式庫（僅存在於安裝程式內）

`nvidia-cublas-cu12`、`nvidia-cuda-nvrtc-cu12`、`nvidia-cudnn-cu12` 為 NVIDIA 專有授權，其再散布須遵守
[NVIDIA 軟體授權條款](https://docs.nvidia.com/cuda/eula/index.html)。這些檔案不在本原始碼倉庫中。

## 語音辨識模型（首次使用時下載，不內附）

`large-v3-turbo`（經 faster-whisper 轉換版本，來源 `mobiuslabsgmbh/faster-whisper-large-v3-turbo`），
基於 OpenAI Whisper（MIT）。請以模型頁面標示的授權為準。
