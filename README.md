# Local Comic Reader

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?logo=flask)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue?logo=windows)](https://www.microsoft.com/windows)

A lightweight, locally hosted comic reader designed for folder-based comic image collections. Supports common image formats including jpg / png / webp / gif / bmp / avif, with built-in OCR dialogue recognition and automatic speech playback.

---

## ✨ Features

-   **Automatic chapter scanning** - Recursively scans the comic root directory and auto-discovers all chapter folders containing images.
-   **Chapter search** - Real-time filtering by title or path keywords.
-   **Reading progress** - Automatically saves per-chapter scroll ratio and current page index, then restores on restart.
-   **Keyboard navigation** - B/N for chapter switching, Space/V for page turning, F for fullscreen.
-   **Adjustable page width** - Percentage input with precision up to two decimal places.
-   **Hot rescan** - One-click rescan after adding new chapters, no service restart required.
-   **OCR dialogue extraction** - Uses RapidOCR to detect dialogue text on comic pages.
-   **Smart ordering** - Groups and sorts text blocks/panels by reading order to reduce cross-bubble misreads.
-   **Speech playback** - Auto-reads current page dialogue on page turn, with 1x / 2x / 3x speed controls and replay.
-   **Background pre-recognition** - Preloads OCR results for context and next chapter to reduce page-switch waiting time.

---

## 🔧 Tech Stack

| Layer        | Tech                                                  |
| ------------ | ----------------------------------------------------- |
| Backend      | Python 3.10+ / Flask 3.x / Waitress                  |
| OCR          | RapidOCR (ONNX Runtime) + Pillow + NumPy             |
| Frontend     | Vanilla HTML5 / CSS3 / JavaScript (ES2020+)          |
| Speech       | Web Speech API                                        |
| Data Storage | Local JSON file (`data/progress.json`, runtime-generated) |

---

## 📦 Project Structure

```
comic-reader/
├── app.py               # Backend entry (Flask API + OCR engine)
├── requirements.txt     # Python dependencies
├── start_reader.bat     # One-click startup script (Windows)
├── stop_reader.bat      # One-click stop script (Windows)
├── README.md
├── data/                # Runtime data (auto-generated, gitignored)
│   ├── progress.json    # Reading progress
│   └── server.pid       # Background process id
├── static/
│   ├── app.js           # Frontend main logic
│   └── style.css        # Frontend styles
└── templates/
    └── index.html       # Frontend page template
```

---

## 📤 Publishing to GitHub

-   Runtime files are generated on first run and should not be committed. The included `.gitignore` already ignores `data/`, `__pycache__/`, `*.pyc`, `*.pid`, `*.token`, `.venv/`.
-   Recommended repo contents: `app.py`, `requirements.txt`, `start_reader.bat`, `stop_reader.bat`, `static/`, `templates/`, `README.md`, `README.zh.md`, `.gitignore`.

## 🚀 Quick Start

### Prerequisites

-   **Python 3.10+** (3.11 or 3.12 recommended)
-   Windows 10 / 11 (the bat scripts are Windows-specific; Linux/macOS users can run via CLI)

### Install

```bash
# Clone repository
git clone <repo-url>
cd comic-reader

# Install dependencies
pip install -r requirements.txt
```

### Run

**Option 1: Command line**

```bash
python app.py --library "..\一人之下_漫画"
```

**Option 2: Double-click bat script**

Double-click `start_reader.bat`. The script will automatically check dependencies, start the service in the background, and open the browser.

To stop the background service, double-click `stop_reader.bat`.

### Access

Open `http://127.0.0.1:7878` in your browser.

---

## ⌨️ Shortcuts

| Key           | Action                          |
| ------------- | ------------------------------- |
| `B`           | Previous chapter                |
| `N`           | Next chapter                    |
| `Space`       | Next page (hold to slow pan)    |
| `Shift+Space` | Previous page                   |
| `V`           | Previous page (hold to slow pan) |
| `F`           | Toggle fullscreen               |

---

## ⚙️ CLI Arguments

| Argument    | Default                             | Description                                  |
| ----------- | ----------------------------------- | -------------------------------------------- |
| `--library` | `../一人之下_漫画`                  | Comic root directory path                     |
| `--host`    | `127.0.0.1`                         | Service bind address                          |
| `--port`    | `7878`                              | Service bind port                             |
| `--engine`  | `auto`                              | Server engine (`auto`/`waitress`/`flask`)    |

Example:

```bash
python app.py --library "D:\Manga" --port 9000
```

---

## 🗂️ Supported Directory Layout

Any directory containing image files is treated as a chapter. Image filenames are displayed with natural sort order (numbers first, case-insensitive).

```
一人之下_漫画/
├── 1-200话/
│   ├── 001/
│   │   ├── 001.jpg
│   │   ├── 002.jpg
│   │   └── 003.jpg
│   └── 002/
│       ├── 001.png
│       └── 002.png
├── 201-300话/
│   └── 201/
│       ├── 01.webp
│       └── 02.webp
└── 番外/
    └── 番外01/
        ├── page01.jpg
        └── page02.jpg
```

---

## 🗣️ Dialogue Playback

-   **Playback toggle**: The left sidebar `Playback: On/Off` button controls auto-play on page turns.
-   **Replay current page**: Re-recognize and replay dialogue on the current page.
-   **Playback speed**: 1x / 2x / 3x. Switching speed immediately replays the current page.
-   **Sorting modes**:
    -   **Strict Priority** - Stricter merge thresholds to reduce accidental merges.
    -   **Balanced Priority** - Default mode; more aggressive at merging fragmented text.
-   The app pre-recognizes nearby context and the next chapter in the background for smoother continuous reading.

---

## 📄 API Endpoints

| Method | Path                 | Description                           |
| ------ | -------------------- | ------------------------------------- |
| `GET`  | `/api/library`       | Get comic library summary             |
| `GET`  | `/api/chapter?id=`   | Get details of a specific chapter     |
| `GET`  | `/api/image`         | Get chapter images                    |
| `GET`  | `/api/progress`      | Read saved reading progress           |
| `POST` | `/api/progress`      | Save reading progress                 |
| `POST` | `/api/rescan`        | Trigger directory rescan              |
| `GET`  | `/api/ocr`           | Get OCR dialogue result for one page  |
| `POST` | `/api/ocr/prefetch`  | Submit background OCR prefetch tasks  |

---

## 🐛 FAQ

<details>
<summary><b>Startup error: "Comic directory does not exist"</b></summary>

Make sure the path provided by `--library` exists and is a directory. The default is `一人之下_漫画` at the same level as the project directory.

</details>

<details>
<summary><b>OCR is unavailable / engine not installed</b></summary>

Make sure `rapidocr-onnxruntime` is installed:

```bash
pip install rapidocr-onnxruntime
```

The OCR engine may take a few seconds to initialize on first startup.

</details>

<details>
<summary><b>No sound during speech playback</b></summary>

-   Make sure your browser supports Web Speech API (Chrome / Edge supported)
-   Check whether your system audio output device is working
-   Some Windows 10 systems require additional Chinese voice packs

</details>

<details>
<summary><b>Port 7878 is already in use</b></summary>

Use the `--port` argument to specify another port:

```bash
python app.py --port 9000
```

</details>

---

## 📝 License

MIT
