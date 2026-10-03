# YT 字幕機

自動下載 YouTube 頻道影片音訊 → 本機 GPU 語音辨識（faster-whisper）→ Gemini 校正同音錯字 → 網頁介面校對 → 匯出 SRT。針對繁體中文直播／影片調校。

> 一般使用者請看 [使用說明](packaging/使用說明.html)，並從 Releases 下載安裝程式。本頁給想自己從原始碼執行或打包的人。

## 系統需求
- Windows 10/11 64 位元
- **NVIDIA 顯示卡**（需 CUDA；不支援 CPU 模式）
- 自己的 [Gemini API Key](https://aistudio.google.com/apikey)

## 從原始碼執行
```bash
pip install -r packaging/requirements-product.txt
python app/server.py        # 開啟 http://127.0.0.1:8420，依設定精靈輸入 API Key 與頻道網址
```
可用環境變數：`YT_PORT`（預設 8420）、`YT_NO_BROWSER=1`（不自動開瀏覽器）。

## 打包成安裝程式
需要先裝好上述相依套件，以及 [Inno Setup 6](https://jrsoftware.org/isinfo.php)。
```bash
python packaging/build_dist.py     # 在 %LOCALAPPDATA%\YTSubtitleBuild 組出可搬移資料夾（含獨立 Python）
ISCC.exe "/DBuildDir=%LOCALAPPDATA%\YTSubtitleBuild\YT字幕機" packaging/installer.iss
```
建置資料夾刻意放在 OneDrive 之外，避免同步程式鎖檔。

## 架構
| 檔案 | 作用 |
|---|---|
| `app/server.py` + `app/static/` | 本機 FastAPI 網頁介面（設定精靈、頻道清單、校對） |
| `batch_process.py` | 批次流程：轉錄準備（無次數限制）＋ Gemini 校正（依每日額度） |
| `watchdog_and_run.py` | 偵測並重啟卡住的批次程序 |
| `transcribe_words.py` / `build_srt.py` | 語音辨識與字幕產生 |
| `correct_srt.py` / `phonetic_guard.py` | Gemini 校正，並以讀音相近檢查擋掉亂改 |
| `refresh_channel.py` | 增量更新頻道影片清單 |

## 注意事項
- 本工具會下載並轉錄你指定頻道的影片音訊，請確認你有權處理這些內容，並自行遵守 YouTube 服務條款與 Google Gemini API 使用條款。
- 免費版 Gemini API 的內容可能被 Google 用於改善產品。
- 安裝程式內含 NVIDIA CUDA 執行函式庫（cuBLAS、cuDNN），其再散布須遵守 NVIDIA 的授權條款。

## 授權
原始碼以 [MIT](LICENSE) 授權。第三方元件的授權見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
