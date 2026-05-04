# 本地漫画阅读器

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?logo=flask)](https://flask.palletsprojects.com/)
[![License](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%2F11-blue?logo=windows)](https://www.microsoft.com/windows)

一个轻量、本地可运行的漫画阅读工具，专为图片目录结构的漫画资源设计。支持 jpg / png / webp / gif / bmp / avif 等常见图片格式，内置 OCR 对白识别与自动语音播放。

---

## ✨ 功能特性

-   **自动章节扫描** — 递归扫描漫画根目录，自动发现所有包含图片的章节目录
-   **章节检索** — 按标题或路径关键词实时过滤章节列表
-   **阅读进度** — 自动保存每话的滚动比例与当前页索引，重启后恢复上次位置
-   **快捷键导航** — B/N 切话、Space/V 翻页、F 全屏
-   **可调页面宽度** — 百分比数值输入，精确到小数点后两位
-   **主题切换** — 支持浅色 / 深色主题一键切换
-   **漫画库切换** — 侧边栏直接切换漫画库，无需重启
-   **热重扫** — 新增章节后一键重新扫描，无需重启服务
-   **OCR 对白识别** — 基于 RapidOCR 自动提取漫画页面上的文字对白
-   **智能排序** — 对白块/画面框按阅读顺序聚合排列，避免跨气泡串读
-   **语音播放** — 切页自动播报当前页对白，支持 1x / 2x / 3x 语速调节与重播
-   **后台预识别** — 自动预取上下文与下一话的 OCR 结果，减少切页等待

---

## 🔧 技术栈

| 层级     | 技术                                                  |
| -------- | ----------------------------------------------------- |
| 后端     | Python 3.10+ / Flask 3.x / Waitress                   |
| OCR      | RapidOCR (ONNX Runtime) + Pillow + NumPy              |
| 前端     | Vanilla HTML5 / CSS3 / JavaScript (ES2020+)           |
| 语音     | Web Speech API                                        |
| 数据存储 | 本地 JSON 文件（`data/progress.json`，运行时自动生成） |

---

## 📦 项目结构

```
漫画阅读器/
├── app.py               # 后端主程序（Flask API + OCR 引擎）
├── reader.config.json   # 漫画库配置（可选）
├── requirements.txt     # Python 依赖
├── start_reader.bat     # Windows 一键启动脚本
├── stop_reader.bat      # Windows 一键停止脚本
├── README.md
├── data/                # 运行时数据（自动生成，已加入 .gitignore）
│   ├── progress.json    # 阅读进度
│   └── server.pid       # 后台进程 ID
├── static/
│   ├── app.js           # 前端主逻辑
│   └── style.css        # 前端样式
└── templates/
    └── index.html       # 前端页面模板
```

---

## 📤 发布到 GitHub

-   运行时文件会在首次启动时自动生成，不应提交到仓库。项目已提供 `.gitignore`，会忽略 `data/`、`__pycache__/`、`*.pyc`、`*.pid`、`*.token`、`.venv/`。
-   建议发布的内容：`app.py`、`requirements.txt`、`start_reader.bat`、`stop_reader.bat`、`static/`、`templates/`、`README.md`、`README.zh.md`、`.gitignore`。

## 🚀 快速开始

### 前置依赖

-   **Python 3.10+**（推荐 3.11 或 3.12）
-   Windows 10 / 11（bat 脚本为 Windows 专用，Linux/macOS 可直接用命令行）

### 安装

```bash
# 克隆仓库
git clone <repo-url>
cd 漫画阅读器

# 安装依赖
pip install -r requirements.txt
```

### 启动

**方式一：命令行**

```bash
python app.py --library "..\一人之下_漫画"
```

**方式二：双击 bat 脚本**

直接双击 `start_reader.bat`，脚本会自动检测依赖、后台启动服务并打开浏览器。

停止后台服务请双击 `stop_reader.bat`。

### 访问

浏览器打开 `http://127.0.0.1:7878`

---

## 🧭 使用指南

### 切换漫画库（页面内）

在左侧「漫画库切换」面板填写其一：

-   **文件夹名**（相对项目上级目录，例如 `一人之下`）
-   **完整路径**（绝对或相对项目目录，例如 `D:\Comics\一人之下`）

点击 **切换漫画库** 后会立刻重载章节列表。

### 主题切换

点击 **全屏 (F)** 旁边的 **主题** 按钮，在浅色 / 深色之间切换。

### 配置文件

可在 `reader.config.json` 中设置默认漫画库：

```json
{
    "library_path": "D:\\Comics\\一人之下",
    "library_name": ""
}
```

优先级：`--library` 参数 > `library_path` > `library_name` > 默认目录。

---

## ⌨️ 快捷键

| 按键          | 功能             |
| ------------- | ---------------- |
| `B`           | 上一话           |
| `N`           | 下一话           |
| `Space`       | 下一页（长按慢划）|
| `Shift+Space` | 上一页           |
| `V`           | 上一页（长按慢划）|
| `F`           | 全屏切换         |

---

## ⚙️ 命令行参数

| 参数        | 默认值                              | 说明                         |
| ----------- | ----------------------------------- | ---------------------------- |
| `--library` | 配置或 `../一人之下_漫画`           | 漫画根目录路径               |
| `--host`    | `127.0.0.1`                         | 服务监听地址                 |
| `--port`    | `7878`                              | 服务监听端口                 |
| `--engine`  | `auto`                              | 服务引擎 (`auto`/`waitress`/`flask`) |

示例：

```bash
python app.py --library "D:\Manga" --port 9000
```

---

## 🗂️ 适配的目录结构

任一包含图片文件的目录即被视为一个章节。图片文件名会按自然排序（数字优先，不区分大小写）展示。

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

## 🗣️ 对白播放

-   **播放开关**：左侧栏「播放: 开/关」按钮控制切页自动播放
-   **重播当前页**：重新识别并播放当前页面上的对白
-   **播放速度**：1x / 2x / 3x 三档可调，切换后立即重播当前页
-   **排序策略**：已移除手动切换（使用内置最佳策略）。
    -   优化点：现在以文本框/画面框的顶边（`top`）为主要排序依据，优先保持逐框识别顺序，减少跨框串读与行内乱序的情况；旁白类多行文本在同一文本块内会严格保持自上而下的顺序。
-   后台会预识别当前上下文与下一话的对白，保证连续阅读时的即时响应

---

## 📄 API 接口

| 方法   | 路径                 | 说明                       |
| ------ | -------------------- | -------------------------- |
| `GET`  | `/api/library`       | 获取漫画库摘要              |
| `GET`  | `/api/library-config`| 获取漫画库配置              |
| `POST` | `/api/library-config`| 更新漫画库配置              |
| `GET`  | `/api/chapter?id=`   | 获取指定章节信息            |
| `GET`  | `/api/image`         | 获取章节图片                |
| `GET`  | `/api/progress`      | 读取阅读进度                |
| `POST` | `/api/progress`      | 保存阅读进度                |
| `POST` | `/api/rescan`        | 重新扫描目录                |
| `GET`  | `/api/ocr`           | 获取单页 OCR 对白结果       |
| `POST` | `/api/ocr/prefetch`  | 提交后台预识别任务队列       |

---

## 🐛 常见问题

<details>
<summary><b>启动时报错「漫画目录不存在」</b></summary>

请确认 `--library` 参数指向的路径存在且为目录。默认路径为项目目录同级下的 `一人之下_漫画`。

</details>

<details>
<summary><b>OCR 功能不可用 / 提示引擎未安装</b></summary>

确保已安装 `rapidocr-onnxruntime`：

```bash
pip install rapidocr-onnxruntime
```

首次启动时 OCR 引擎需要初始化，可能需要几秒钟。

</details>

<details>
<summary><b>语音播放无声音</b></summary>

-   确认浏览器支持 Web Speech API（Chrome / Edge 均支持）
-   检查系统音频输出设备是否正常
-   部分 Win10 系统需要安装中文语音包

</details>

<details>
<summary><b>端口 7878 被占用</b></summary>

使用 `--port` 参数指定其他端口：

```bash
python app.py --port 9000
```

</details>

---

## 📝 License

MIT