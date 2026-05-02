from __future__ import annotations

import argparse
from concurrent.futures import Future
import json
import os
import queue
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Flask, abort, jsonify, render_template, request, send_from_directory

try:
    import numpy as np
    from PIL import Image, ImageOps
except ImportError:
    np = None
    Image = None
    ImageOps = None

try:
    from waitress import serve as waitress_serve
except ImportError:
    waitress_serve = None

APP_DIR = Path(__file__).resolve().parent
DEFAULT_LIBRARY_DIR = (APP_DIR.parent / "一人之下_漫画").resolve()
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
OCR_MIN_LONGEST = 1400
OCR_MAX_LONGEST = 2200
OCR_PREFETCH_MAX_TASKS = 48
WATERMARK_PHRASES = [ //在此处追加新水印即可
    "腾讯动漫",
]
WATERMARK_LATIN_TOKENS = {
    "acqqcom",
    "qqcom",
}
WATERMARK_FUZZY_MAX_DISTANCE = 1


def natural_key(value: str) -> list[Any]:
    parts = re.split(r"(\d+)", value)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


@dataclass(frozen=True)
class Chapter:
    chapter_id: str
    title: str
    parent: str
    rel_dir: str
    images: list[str]


class MangaIndex:
    def __init__(self, library_root: Path) -> None:
        self.library_root = library_root
        self._lock = threading.Lock()
        self._chapters: list[Chapter] = []
        self._chapter_map: dict[str, Chapter] = {}
        self._chapter_order: dict[str, int] = {}
        self.reload()

    def _discover_chapters(self) -> list[Chapter]:
        if not self.library_root.exists():
            return []

        discovered: list[Chapter] = []

        for current_root, dirs, files in os.walk(self.library_root):
            dirs.sort(key=natural_key)
            images = [
                file_name
                for file_name in files
                if Path(file_name).suffix.lower() in IMAGE_EXTENSIONS
            ]
            if not images:
                continue

            images.sort(key=natural_key)
            current_path = Path(current_root)
            rel_dir = current_path.relative_to(self.library_root).as_posix()
            parent = Path(rel_dir).parent.as_posix()
            if parent == ".":
                parent = ""

            discovered.append(
                Chapter(
                    chapter_id=rel_dir,
                    title=current_path.name,
                    parent=parent,
                    rel_dir=rel_dir,
                    images=images,
                )
            )

        discovered.sort(key=lambda chapter: natural_key(chapter.rel_dir))
        return discovered

    def reload(self) -> None:
        chapters = self._discover_chapters()
        chapter_map = {chapter.chapter_id: chapter for chapter in chapters}
        chapter_order = {
            chapter.chapter_id: chapter_index for chapter_index, chapter in enumerate(chapters)
        }

        with self._lock:
            self._chapters = chapters
            self._chapter_map = chapter_map
            self._chapter_order = chapter_order

    def library_summary(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                {
                    "id": chapter.chapter_id,
                    "title": chapter.title,
                    "parent": chapter.parent,
                    "count": len(chapter.images),
                    "index": index,
                }
                for index, chapter in enumerate(self._chapters)
            ]

    def chapter_count(self) -> int:
        with self._lock:
            return len(self._chapters)

    def get_chapter(self, chapter_id: str) -> Chapter | None:
        with self._lock:
            return self._chapter_map.get(chapter_id)

    def chapter_index(self, chapter_id: str) -> int:
        with self._lock:
            return self._chapter_order.get(chapter_id, -1)


class ProgressStore:
    def __init__(self, progress_path: Path) -> None:
        self.progress_path = progress_path
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._data = self._load_data()

    @staticmethod
    def _default_data() -> dict[str, Any]:
        return {
            "last_chapter_id": None,
            "chapters": {},
            "updated_at": None,
        }

    def _load_data(self) -> dict[str, Any]:
        if not self.progress_path.exists():
            return self._default_data()

        try:
            raw_data = json.loads(self.progress_path.read_text(encoding="utf-8"))
            if not isinstance(raw_data, dict):
                return self._default_data()
            if "chapters" not in raw_data or not isinstance(raw_data["chapters"], dict):
                raw_data["chapters"] = {}
            return raw_data
        except (json.JSONDecodeError, OSError):
            return self._default_data()

    def read(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data, ensure_ascii=False))

    def update(self, chapter_id: str, scroll_ratio: float, image_index: int | None) -> None:
        ratio = max(0.0, min(1.0, float(scroll_ratio)))
        now = datetime.now().isoformat(timespec="seconds")

        with self._lock:
            chapter_progress = self._data.setdefault("chapters", {})
            chapter_progress[chapter_id] = {
                "scroll_ratio": ratio,
                "image_index": image_index,
                "updated_at": now,
            }
            self._data["last_chapter_id"] = chapter_id
            self._data["updated_at"] = now
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.progress_path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.progress_path)


@dataclass(frozen=True)
class OCRTextLine:
    text: str
    left: float
    top: float
    right: float
    bottom: float
    score: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class OCRTextBlock:
    lines: list[OCRTextLine]
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass
class OCRTextPanel:
    blocks: list[OCRTextBlock]
    left: float
    top: float
    right: float
    bottom: float

    @property
    def width(self) -> float:
        return max(0.0, self.right - self.left)

    @property
    def height(self) -> float:
        return max(0.0, self.bottom - self.top)

    @property
    def center_x(self) -> float:
        return (self.left + self.right) / 2.0

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


class DisjointSet:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            self.parent[left_root] = right_root
            return
        if self.rank[left_root] > self.rank[right_root]:
            self.parent[right_root] = left_root
            return
        self.parent[right_root] = left_root
        self.rank[left_root] += 1


def interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def interval_gap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    if end_a < start_b:
        return start_b - end_a
    if end_b < start_a:
        return start_a - end_b
    return 0.0


class OCRService:
    def __init__(self, manga_index: MangaIndex, cache_limit: int = 4096) -> None:
        self.manga_index = manga_index
        self.cache_limit = cache_limit
        self._lock = threading.Lock()
        self._engine_lock = threading.Lock()
        self._cache: dict[str, dict[str, Any]] = {}
        self._cache_order: list[str] = []
        self._inflight: dict[str, Future[dict[str, Any]]] = {}
        self._queued: set[str] = set()
        self._queue: queue.PriorityQueue[tuple[int, int, str, int]] = queue.PriorityQueue()
        self._sequence = 0
        self._engine: Any | None = None
        self._engine_error: str | None = None
        self._worker = threading.Thread(target=self._prefetch_worker, name="ocr-prefetch", daemon=True)
        self._worker.start()

    @staticmethod
    def _cache_key(chapter_id: str, image_index: int) -> str:
        return f"{chapter_id}::{image_index}"

    @staticmethod
    def _clone_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(payload, ensure_ascii=False))

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cache_order.clear()
            self._inflight.clear()
            self._queued.clear()

        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                break

    def _get_engine(self) -> Any:
        with self._engine_lock:
            if self._engine is not None:
                return self._engine

            if self._engine_error is not None:
                raise RuntimeError(self._engine_error)

            try:
                from rapidocr_onnxruntime import RapidOCR
            except ImportError as exc:
                self._engine_error = (
                    "OCR 引擎未安装，请先执行: pip install -r requirements.txt"
                )
                raise RuntimeError(self._engine_error) from exc

            self._engine = RapidOCR()
            return self._engine

    def _resolve_image_path(self, chapter_id: str, image_index: int) -> Path:
        chapter = self.manga_index.get_chapter(chapter_id)
        if chapter is None:
            raise ValueError("章节不存在")

        if image_index < 0 or image_index >= len(chapter.images):
            raise ValueError("图片索引超出范围")

        chapter_dir = self.manga_index.library_root / Path(chapter.rel_dir)
        return chapter_dir / chapter.images[image_index]

    @staticmethod
    def _normalize_text(raw_text: str) -> str:
        text = re.sub(r"\s+", "", raw_text or "")
        text = text.replace("\u3000", "")
        return text.strip()

    @staticmethod
    def _levenshtein_distance(left: str, right: str, max_distance: int) -> int:
        """计算编辑距离；当距离已超过阈值时尽早返回。"""
        if left == right:
            return 0

        left_len = len(left)
        right_len = len(right)
        if abs(left_len - right_len) > max_distance:
            return max_distance + 1

        previous = list(range(right_len + 1))
        for left_index, left_char in enumerate(left, start=1):
            current = [left_index]
            row_min = current[0]

            for right_index, right_char in enumerate(right, start=1):
                cost = 0 if left_char == right_char else 1
                value = min(
                    previous[right_index] + 1,
                    current[right_index - 1] + 1,
                    previous[right_index - 1] + cost,
                )
                current.append(value)
                if value < row_min:
                    row_min = value

            if row_min > max_distance:
                return max_distance + 1

            previous = current

        return previous[-1]

    @staticmethod
    def _contains_fuzzy_phrase(normalized_text: str, phrase: str, max_distance: int) -> bool:
        if not phrase:
            return False

        if phrase in normalized_text:
            return True

        phrase_len = len(phrase)
        min_len = max(1, phrase_len - max_distance)
        max_len = phrase_len + max_distance
        text_len = len(normalized_text)

        for start in range(text_len):
            for candidate_len in range(min_len, max_len + 1):
                end = start + candidate_len
                if end > text_len:
                    continue

                candidate = normalized_text[start:end]
                distance = OCRService._levenshtein_distance(candidate, phrase, max_distance)
                if distance <= max_distance:
                    return True

        return False

    @staticmethod
    def _looks_like_watermark(text: str) -> bool:
        normalized = re.sub(r"[^\w\u4e00-\u9fff]", "", text or "").lower()
        if not normalized:
            return False

        for latin_token in WATERMARK_LATIN_TOKENS:
            if latin_token in normalized:
                return True

        # 通过编辑距离匹配 OCR 近似词：例如「腾讯运漫」「腾机动漫」等。
        for phrase in WATERMARK_PHRASES:
            if OCRService._contains_fuzzy_phrase(
                normalized,
                phrase,
                WATERMARK_FUZZY_MAX_DISTANCE,
            ):
                return True

        if "腾讯" in normalized and ("动漫" in normalized or "漫画" in normalized):
            return True

        # 含「腾讯」且字数很少（1-4 字）的孤立文本框大概率是水印
        if "腾讯" in normalized and len(normalized) <= 4:
            return True
        return False

    @staticmethod
    def _is_watermark_line(
        text: str,
        left: float,
        top: float,
        right: float,
        bottom: float,
        image_width: float,
        image_height: float,
    ) -> bool:
        if not OCRService._looks_like_watermark(text):
            return False

        center_x = (left + right) / 2.0
        center_y = (top + bottom) / 2.0
        box_area = max(1.0, (right - left) * (bottom - top))
        page_area = max(1.0, image_width * image_height)

        bottom_zone = center_y >= image_height * 0.65
        right_zone = center_x >= image_width * 0.52
        small_box = box_area <= page_area * 0.08
        corner_zone = bottom_zone and right_zone

        return corner_zone or (bottom_zone and small_box)

    @staticmethod
    def _is_valid_text(text: str) -> bool:
        if not text:
            return False
        if len(text) == 1 and text in {"-", "_", "|", "~", ".", ",", "。", "，"}:
            return False
        return True

    def _prepare_image(self, image_path: Path) -> Any:
        if np is None or Image is None or ImageOps is None:
            raise RuntimeError("缺少 OCR 依赖，请先执行: pip install -r requirements.txt")

        with Image.open(image_path) as image:
            rgb = image.convert("RGB")
            width, height = rgb.size
            longest = max(width, height)

            if longest > OCR_MAX_LONGEST:
                scale = OCR_MAX_LONGEST / max(1, longest)
                target_width = max(1, int(width * scale))
                target_height = max(1, int(height * scale))
                rgb = rgb.resize((target_width, target_height), Image.Resampling.LANCZOS)
            elif longest < OCR_MIN_LONGEST:
                scale = OCR_MIN_LONGEST / max(1, longest)
                target_width = max(1, int(width * scale))
                target_height = max(1, int(height * scale))
                rgb = rgb.resize((target_width, target_height), Image.Resampling.LANCZOS)

            gray = ImageOps.grayscale(rgb)
            enhanced = ImageOps.autocontrast(gray, cutoff=2)
            return np.asarray(enhanced)

    def _parse_lines(self, ocr_raw: Any, image_width: float, image_height: float) -> list[OCRTextLine]:
        if not isinstance(ocr_raw, list):
            return []

        parsed: list[OCRTextLine] = []
        for item in ocr_raw:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue

            box = item[0]
            raw_text = str(item[1])
            raw_score = item[2] if len(item) > 2 else 1.0

            if not isinstance(box, (list, tuple)) or len(box) < 4:
                continue

            points: list[tuple[float, float]] = []
            for point in box:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    x = float(point[0])
                    y = float(point[1])
                except (TypeError, ValueError):
                    continue
                points.append((x, y))

            if len(points) < 4:
                continue

            try:
                score = float(raw_score)
            except (TypeError, ValueError):
                score = 1.0

            text = self._normalize_text(raw_text)
            if score < 0.25 or not self._is_valid_text(text):
                continue

            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            left = min(xs)
            right = max(xs)
            top = min(ys)
            bottom = max(ys)

            if right - left < 2 or bottom - top < 2:
                continue

            if self._is_watermark_line(text, left, top, right, bottom, image_width, image_height):
                continue

            parsed.append(
                OCRTextLine(
                    text=text,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                    score=score,
                )
            )

        return parsed

    @staticmethod
    def _segment_by_flag(flags: list[bool]) -> list[tuple[bool, int, int]]:
        if not flags:
            return []

        segments: list[tuple[bool, int, int]] = []
        current = flags[0]
        start = 0
        for index in range(1, len(flags)):
            if flags[index] == current:
                continue
            segments.append((current, start, index - 1))
            current = flags[index]
            start = index
        segments.append((current, start, len(flags) - 1))
        return segments

    def _detect_panel_rects(self, image_input: Any) -> list[tuple[float, float, float, float]]:
        if image_input is None or not hasattr(image_input, "shape"):
            return []

        if len(image_input.shape) < 2:
            return []

        gray = image_input
        if len(image_input.shape) == 3:
            gray = image_input[:, :, 0]

        height = int(gray.shape[0])
        width = int(gray.shape[1])
        if height < 200 or width < 200:
            return []

        dark_mask = gray < 45
        row_ratio = dark_mask.mean(axis=1)
        row_border = row_ratio >= 0.68
        row_segments = self._segment_by_flag(row_border.tolist())

        min_band_height = max(60, int(height * 0.06))
        band_segments = [
            (start, end)
            for is_border, start, end in row_segments
            if not is_border and (end - start + 1) >= min_band_height
        ]

        if not band_segments:
            return []

        min_col_width = max(60, int(width * 0.08))
        panel_rects: list[tuple[float, float, float, float]] = []

        for band_top, band_bottom in band_segments:
            band = dark_mask[band_top:band_bottom + 1, :]
            col_ratio = band.mean(axis=0)
            col_border = col_ratio >= 0.68
            col_segments = self._segment_by_flag(col_border.tolist())

            max_border_width = max(6, int(width * 0.008))

            content_cols = [
                (start, end)
                for is_border, start, end in col_segments
                if (not is_border or (end - start + 1) > max_border_width)
                and (end - start + 1) >= min_col_width
            ]

            if not content_cols:
                panel_rects.append((0.0, float(band_top), float(width), float(band_bottom)))
                continue

            for col_left, col_right in content_cols:
                panel_rects.append(
                    (float(col_left), float(band_top), float(col_right), float(band_bottom))
                )

        if len(panel_rects) > 24:
            return []

        return panel_rects

    @staticmethod
    def _should_merge_lines(left_line: OCRTextLine, right_line: OCRTextLine) -> bool:
        """判断两条 OCR 文本行是否属于同一个对白框/文本块，需要合并。

        核心原则：只合并明显属于同一气泡内的多行文本，以及 OCR 碎片化的同
        一行文本。绝不跨气泡合并——两个独立气泡即使水平对齐、间距很小，
        也必须保持独立。
        """
        overlap_x = interval_overlap(left_line.left, left_line.right, right_line.left, right_line.right)
        overlap_y = interval_overlap(left_line.top, left_line.bottom, right_line.top, right_line.bottom)
        gap_x = interval_gap(left_line.left, left_line.right, right_line.left, right_line.right)
        gap_y = interval_gap(left_line.top, left_line.bottom, right_line.top, right_line.bottom)

        min_width = max(1.0, min(left_line.width, right_line.width))
        min_height = max(1.0, min(left_line.height, right_line.height))
        avg_height = (left_line.height + right_line.height) / 2.0

        overlap_x_ratio = overlap_x / min_width
        overlap_y_ratio = overlap_y / min_height
        center_delta = abs(left_line.center_x - right_line.center_x)
        max_width = max(left_line.width, right_line.width)

        # 条件 A：同一气泡内的多行文本。
        # x 方向高度重叠（≥62%），y 方向间距有限（≤2.8 倍平均行高）。
        # 这是最常见的情况：气泡里换行的两行文字。
        gap_y_limit = max(12.0, min(avg_height * 1.2, 90.0))
        center_limit = max(10.0, max_width * 0.4)
        if overlap_x_ratio >= 0.75 and gap_y <= gap_y_limit and center_delta <= center_limit:
            return True

        # 条件 B：OCR 把同一行文字切成了两个碎片。
        # 此时 y 方向几乎完全重叠，x 方向间距极小（≤3 像素或 0.15 倍行高）。
        # 严格限制 gap_x 防止跨气泡合并。
        horizontal_gap_limit = max(3.0, avg_height * 0.15)
        if overlap_y_ratio >= 0.88 and gap_x <= horizontal_gap_limit and gap_y <= max(4.0, avg_height * 0.35):
            return True

        # 条件 C：两个框在 x 和 y 方向都高度重叠（≥78% / 62%），
        # 几乎完全重合，视为同一区域的重复识别碎片。
        if overlap_x_ratio >= 0.78 and overlap_y_ratio >= 0.62:
            return True

        return False

    @staticmethod
    def _should_merge_blocks(
        left_block: OCRTextBlock,
        right_block: OCRTextBlock,
        median_width: float,
        median_height: float,
    ) -> bool:
        """判断两个对白框（block）是否属于同一画面框。"""
        overlap_x = interval_overlap(left_block.left, left_block.right, right_block.left, right_block.right)
        overlap_y = interval_overlap(left_block.top, left_block.bottom, right_block.top, right_block.bottom)
        gap_x = interval_gap(left_block.left, left_block.right, right_block.left, right_block.right)
        gap_y = interval_gap(left_block.top, left_block.bottom, right_block.top, right_block.bottom)

        min_width = max(1.0, min(left_block.width, right_block.width))
        min_height = max(1.0, min(left_block.height, right_block.height))
        overlap_x_ratio = overlap_x / min_width
        overlap_y_ratio = overlap_y / min_height

        # 高重叠视为同一画面内的重复/相邻对白。
        if overlap_x_ratio >= 0.78 and overlap_y_ratio >= 0.7:
            return True

        vertical_gap_limit = max(14.0, median_height * 0.65)
        horizontal_gap_limit = max(14.0, median_width * 0.55)

        # 上下堆叠的对白（同一画面内多气泡）。
        if overlap_x_ratio >= 0.55 and gap_y <= vertical_gap_limit:
            return True

        # 左右并列的对白（同一画面内多气泡）。
        if overlap_y_ratio >= 0.55 and gap_x <= horizontal_gap_limit:
            return True

        return False

    @staticmethod
    def _order_lines(lines: list[OCRTextLine]) -> list[OCRTextLine]:
        """对同一文本块内的多行文字进行排序。

        漫画中绝大多数对白框是横排文字，采用「上→下、左→右」的顺序。
        仅当检测到明确的竖排特征时才使用竖排逻辑。
        """
        if len(lines) <= 1:
            return lines

        if OCRService._is_vertical_layout(lines):
            return OCRService._order_lines_vertical(lines)

        return OCRService._order_lines_horizontal(lines)

    @staticmethod
    def _is_vertical_layout(lines: list[OCRTextLine]) -> bool:
        """检测文本行集合是否呈竖排排版。

        竖排特征：每行宽度较窄（宽≤高×1.35），整体呈多列分布，
        且以竖直方向上的重叠对为主。
        """
        if len(lines) <= 2:
            return False

        long_text_count = sum(1 for line in lines if len(line.text) >= 4)
        if long_text_count >= max(1, len(lines) // 2):
            return False

        widths = sorted(line.width for line in lines)
        heights = sorted(line.height for line in lines)
        median_width = max(1.0, widths[len(widths) // 2])
        median_height = max(1.0, heights[len(heights) // 2])

        long_line_count = sum(1 for line in lines if line.width > line.height * 1.9)
        if long_line_count >= max(1, len(lines) // 3):
            return False

        compact_count = sum(1 for line in lines if line.width <= line.height * 1.35)
        compact_ratio = compact_count / len(lines)
        if compact_ratio < 0.65:
            return False

        left = min(line.left for line in lines)
        right = max(line.right for line in lines)
        top = min(line.top for line in lines)
        bottom = max(line.bottom for line in lines)
        x_span = max(1.0, right - left)
        y_span = max(1.0, bottom - top)

        column_threshold = max(8.0, median_width * 0.72)
        sorted_centers = sorted((line.center_x for line in lines), reverse=True)
        columns: list[dict[str, float]] = []
        for center_x in sorted_centers:
            matched: dict[str, float] | None = None
            for column in columns:
                if abs(center_x - column["anchor_x"]) <= column_threshold:
                    matched = column
                    break

            if matched is None:
                columns.append({"anchor_x": center_x, "count": 1.0})
                continue

            count = matched["count"] + 1.0
            matched["anchor_x"] = (matched["anchor_x"] * matched["count"] + center_x) / count
            matched["count"] = count

        if len(columns) < 2:
            return False

        if x_span < median_width * 1.35:
            return False

        vertical_pair_count = 0
        horizontal_pair_count = 0

        for left_index in range(len(lines)):
            for right_index in range(left_index + 1, len(lines)):
                left_line = lines[left_index]
                right_line = lines[right_index]
                overlap_x = interval_overlap(left_line.left, left_line.right, right_line.left, right_line.right)
                overlap_y = interval_overlap(left_line.top, left_line.bottom, right_line.top, right_line.bottom)
                min_width = max(1.0, min(left_line.width, right_line.width))
                min_height = max(1.0, min(left_line.height, right_line.height))
                overlap_x_ratio = overlap_x / min_width
                gap_y = interval_gap(left_line.top, left_line.bottom, right_line.top, right_line.bottom)
                gap_x = interval_gap(left_line.left, left_line.right, right_line.left, right_line.right)

                if overlap_x_ratio >= 0.45 and gap_y <= max(20.0, median_height * 2.8):
                    vertical_pair_count += 1
                if overlap_y >= 0 and gap_x <= max(14.0, median_height * 1.4):
                    horizontal_pair_count += 1

        relation_bias = (
            vertical_pair_count >= 2
            and vertical_pair_count > horizontal_pair_count * 1.25
        )

        return relation_bias and (median_height > median_width * 1.22 or y_span > x_span * 1.15)

    @staticmethod
    def _order_lines_vertical(lines: list[OCRTextLine]) -> list[OCRTextLine]:
        widths = sorted(line.width for line in lines)
        median_width = max(1.0, widths[len(widths) // 2])
        column_threshold = max(7.0, median_width * 0.64)

        sorted_by_column = sorted(lines, key=lambda line: (-line.center_x, line.top))
        columns: list[dict[str, Any]] = []

        for line in sorted_by_column:
            matched: dict[str, Any] | None = None
            for column in columns:
                if abs(line.center_x - column["anchor_x"]) <= column_threshold:
                    matched = column
                    break

            if matched is None:
                columns.append({"anchor_x": line.center_x, "lines": [line]})
                continue

            matched["lines"].append(line)
            count = len(matched["lines"])
            matched["anchor_x"] = (matched["anchor_x"] * (count - 1) + line.center_x) / count

        ordered: list[OCRTextLine] = []
        for column in sorted(columns, key=lambda value: value["anchor_x"], reverse=True):
            column_lines = sorted(column["lines"], key=lambda line: line.top)
            ordered.extend(column_lines)
        return ordered

    @staticmethod
    def _order_lines_horizontal(lines: list[OCRTextLine]) -> list[OCRTextLine]:
        """横排文字排序：同一行内左→右，行间上→下。

        关键修正：同属一个文本块的多行文字（如旁白框内换行）必须保持
        自上而下的顺序，不会被错误地按 x 坐标重排。
        """
        heights = sorted(line.height for line in lines)
        median_height = heights[len(heights) // 2]
        row_threshold = max(7.0, median_height * 0.52)
        sorted_by_top = sorted(lines, key=lambda line: (line.top, line.left, line.center_y))
        rows: list[dict[str, Any]] = []

        for line in sorted_by_top:
            matched: dict[str, Any] | None = None
            for row in rows:
                if abs(line.top - row["top"]) <= row_threshold:
                    matched = row
                    break

            if matched is None:
                rows.append({
                    "top": line.top,
                    "lines": [line],
                })
                continue

            matched["lines"].append(line)
            matched["top"] = min(matched["top"], line.top)

        ordered: list[OCRTextLine] = []
        # 按行的 top（靠上）优先，再按 center_y 作为次要排序，保证视觉上的从上到下读取顺序
        for row in sorted(rows, key=lambda value: value["top"]):
            # 同一行内优先保留上→下，再按左→右，避免多行对白被 x 坐标打乱
            row_lines = sorted(row["lines"], key=lambda line: (line.top, line.left, line.center_y))
            ordered.extend(row_lines)
        return ordered

    def _build_blocks(self, lines: list[OCRTextLine]) -> list[OCRTextBlock]:
        if not lines:
            return []

        disjoint_set = DisjointSet(len(lines))
        for left_index in range(len(lines)):
            for right_index in range(left_index + 1, len(lines)):
                if self._should_merge_lines(lines[left_index], lines[right_index]):
                    disjoint_set.union(left_index, right_index)

        grouped: dict[int, list[OCRTextLine]] = {}
        for index, line in enumerate(lines):
            root = disjoint_set.find(index)
            grouped.setdefault(root, []).append(line)

        blocks: list[OCRTextBlock] = []
        for group_lines in grouped.values():
            ordered_lines = self._order_lines(group_lines)
            left = min(line.left for line in ordered_lines)
            top = min(line.top for line in ordered_lines)
            right = max(line.right for line in ordered_lines)
            bottom = max(line.bottom for line in ordered_lines)
            blocks.append(
                OCRTextBlock(
                    lines=ordered_lines,
                    left=left,
                    top=top,
                    right=right,
                    bottom=bottom,
                )
            )

        return self._order_blocks(blocks)

    @staticmethod
    def _make_panel(blocks: list[OCRTextBlock]) -> OCRTextPanel:
        left = min(block.left for block in blocks)
        top = min(block.top for block in blocks)
        right = max(block.right for block in blocks)
        bottom = max(block.bottom for block in blocks)
        return OCRTextPanel(blocks=blocks, left=left, top=top, right=right, bottom=bottom)

    @staticmethod
    def _merge_panel(target: OCRTextPanel, block: OCRTextBlock) -> OCRTextPanel:
        merged_blocks = [*target.blocks, block]
        return OCRService._make_panel(merged_blocks)

    def _build_panels(self, blocks: list[OCRTextBlock]) -> list[OCRTextPanel]:
        if not blocks:
            return []

        return self._build_panels_with_rects(blocks, None)

    def _build_panels_with_rects(
        self,
        blocks: list[OCRTextBlock],
        panel_rects: list[tuple[float, float, float, float]] | None,
    ) -> list[OCRTextPanel]:
        if not blocks:
            return []

        if panel_rects:
            panel_blocks: list[list[OCRTextBlock]] = [list() for _ in panel_rects]
            for block in blocks:
                best_index: int | None = None
                best_overlap = 0.0
                for index, rect in enumerate(panel_rects):
                    left, top, right, bottom = rect
                    overlap_x = interval_overlap(left, right, block.left, block.right)
                    overlap_y = interval_overlap(top, bottom, block.top, block.bottom)
                    overlap_area = overlap_x * overlap_y
                    if overlap_area > best_overlap:
                        best_overlap = overlap_area
                        best_index = index

                if best_index is None or best_overlap == 0.0:
                    for index, rect in enumerate(panel_rects):
                        left, top, right, bottom = rect
                        if left <= block.center_x <= right and top <= block.center_y <= bottom:
                            best_index = index
                            break

                if best_index is None:
                    best_index = 0

                panel_blocks[best_index].append(block)

            panels: list[OCRTextPanel] = []
            for index, rect in enumerate(panel_rects):
                if not panel_blocks[index]:
                    continue
                ordered_blocks = self._order_blocks(panel_blocks[index])
                left, top, right, bottom = rect
                panels.append(
                    OCRTextPanel(
                        blocks=ordered_blocks,
                        left=left,
                        top=top,
                        right=right,
                        bottom=bottom,
                    )
                )

            if panels:
                return self._order_panels(panels)

        heights = sorted(block.height for block in blocks)
        widths = sorted(block.width for block in blocks)
        median_height = max(1.0, heights[len(heights) // 2])
        median_width = max(1.0, widths[len(widths) // 2])

        row_threshold = max(22.0, median_height * 0.95)
        split_gap_threshold = max(52.0, median_width * 0.86)
        vertical_merge_gap = max(28.0, median_height * 1.4)

        sorted_by_top = sorted(blocks, key=lambda block: (block.center_y, block.left))
        rows: list[dict[str, Any]] = []

        for block in sorted_by_top:
            matched: dict[str, Any] | None = None
            for row in rows:
                if abs(block.center_y - row["anchor_center_y"]) <= row_threshold:
                    matched = row
                    break

            if matched is None:
                rows.append({
                    "anchor_center_y": block.center_y,
                    "top": block.top,
                    "blocks": [block],
                })
                continue

            matched["blocks"].append(block)
            count = len(matched["blocks"])
            matched["anchor_center_y"] = (
                matched["anchor_center_y"] * (count - 1) + block.center_y
            ) / count
            matched["top"] = min(matched["top"], block.top)

        panel_candidates: list[OCRTextPanel] = []
        for row in sorted(rows, key=lambda item: item["top"]):
            row_blocks = sorted(row["blocks"], key=lambda block: block.left)
            if not row_blocks:
                continue

            current_group: list[OCRTextBlock] = [row_blocks[0]]
            for block in row_blocks[1:]:
                prev = current_group[-1]
                gap_x = interval_gap(prev.left, prev.right, block.left, block.right)
                if gap_x >= split_gap_threshold:
                    panel_candidates.append(self._make_panel(current_group))
                    current_group = [block]
                else:
                    current_group.append(block)

            panel_candidates.append(self._make_panel(current_group))

        merged_panels: list[OCRTextPanel] = []
        for candidate in sorted(panel_candidates, key=lambda panel: (panel.top, panel.left)):
            merged = False
            for index, panel in enumerate(merged_panels):
                overlap_x = interval_overlap(panel.left, panel.right, candidate.left, candidate.right)
                min_width = max(1.0, min(panel.width, candidate.width))
                overlap_x_ratio = overlap_x / min_width
                gap_y = interval_gap(panel.top, panel.bottom, candidate.top, candidate.bottom)

                if overlap_x_ratio >= 0.62 and gap_y <= vertical_merge_gap:
                    combined = self._make_panel([*panel.blocks, *candidate.blocks])
                    merged_panels[index] = combined
                    merged = True
                    break

            if not merged:
                merged_panels.append(candidate)

        return self._order_panels(merged_panels)

    @staticmethod
    def _panel_anchor_y(panel: OCRTextPanel) -> float:
        if not panel.blocks:
            return panel.center_y

        centers = sorted(block.center_y for block in panel.blocks)
        return centers[len(centers) // 2]

    @staticmethod
    def _order_panels(panels: list[OCRTextPanel]) -> list[OCRTextPanel]:
        """对画面框（panel）进行阅读顺序排序：上→下、左→右，并处理跨行长框。"""
        if len(panels) <= 1:
            return panels

        heights = sorted(panel.height for panel in panels)
        widths = sorted(panel.width for panel in panels)
        median_height = max(1.0, heights[len(heights) // 2])
        median_width = max(1.0, widths[len(widths) // 2])

        vertical_gap_threshold = max(18.0, median_height * 0.45)
        horizontal_gap_threshold = max(18.0, median_width * 0.35)
        min_overlap_ratio = 0.2

        count = len(panels)
        edges: list[set[int]] = [set() for _ in range(count)]
        indegrees = [0] * count

        def add_edge(source: int, target: int) -> None:
            if target in edges[source]:
                return
            edges[source].add(target)
            indegrees[target] += 1

        for left_index in range(count):
            for right_index in range(left_index + 1, count):
                left_panel = panels[left_index]
                right_panel = panels[right_index]
                overlap_x = interval_overlap(
                    left_panel.left,
                    left_panel.right,
                    right_panel.left,
                    right_panel.right,
                )
                overlap_y = interval_overlap(
                    left_panel.top,
                    left_panel.bottom,
                    right_panel.top,
                    right_panel.bottom,
                )
                gap_x = interval_gap(
                    left_panel.left,
                    left_panel.right,
                    right_panel.left,
                    right_panel.right,
                )
                gap_y = interval_gap(
                    left_panel.top,
                    left_panel.bottom,
                    right_panel.top,
                    right_panel.bottom,
                )

                min_width = max(1.0, min(left_panel.width, right_panel.width))
                min_height = max(1.0, min(left_panel.height, right_panel.height))
                overlap_x_ratio = overlap_x / min_width
                overlap_y_ratio = overlap_y / min_height

                if left_panel.bottom <= right_panel.top - vertical_gap_threshold:
                    add_edge(left_index, right_index)
                    continue
                if right_panel.bottom <= left_panel.top - vertical_gap_threshold:
                    add_edge(right_index, left_index)
                    continue

                if overlap_y_ratio >= min_overlap_ratio:
                    if left_panel.center_x + horizontal_gap_threshold <= right_panel.center_x:
                        add_edge(left_index, right_index)
                        continue
                    if right_panel.center_x + horizontal_gap_threshold <= left_panel.center_x:
                        add_edge(right_index, left_index)
                        continue
                    if gap_x > horizontal_gap_threshold:
                        if left_panel.center_x < right_panel.center_x:
                            add_edge(left_index, right_index)
                            continue
                        if right_panel.center_x < left_panel.center_x:
                            add_edge(right_index, left_index)
                            continue

                if overlap_x_ratio >= min_overlap_ratio:
                    if left_panel.center_y + vertical_gap_threshold <= right_panel.center_y:
                        add_edge(left_index, right_index)
                        continue
                    if right_panel.center_y + vertical_gap_threshold <= left_panel.center_y:
                        add_edge(right_index, left_index)
                        continue

        pending = [index for index in range(count) if indegrees[index] == 0]
        pending.sort(key=lambda idx: (panels[idx].top, panels[idx].left))
        ordered_indices: list[int] = []

        while pending:
            current = pending.pop(0)
            ordered_indices.append(current)
            for neighbor in sorted(edges[current], key=lambda idx: (panels[idx].top, panels[idx].left)):
                indegrees[neighbor] -= 1
                if indegrees[neighbor] == 0:
                    pending.append(neighbor)
            pending.sort(key=lambda idx: (panels[idx].top, panels[idx].left))

        if len(ordered_indices) < count:
            remaining = [index for index in range(count) if index not in ordered_indices]
            remaining.sort(key=lambda idx: (panels[idx].top, panels[idx].left))
            ordered_indices.extend(remaining)

        return [panels[index] for index in ordered_indices]

    @staticmethod
    def _order_blocks(blocks: list[OCRTextBlock]) -> list[OCRTextBlock]:
        """对白框（block）排序：上→下，同一行左→右。"""
        if len(blocks) <= 1:
            return blocks

        heights = sorted(block.height for block in blocks)
        median_height = heights[len(heights) // 2]
        # 放宽行阈值，避免同一分镜内不同高度的块因 top 差异被错误分行
        row_threshold = max(20.0, median_height * 0.72)

        # 使用 block.top 保持严格的上→下顺序，避免中心点偏移导致的同排判断错误
        sorted_by_top = sorted(blocks, key=lambda block: (block.top, block.left))
        rows: list[dict[str, Any]] = []

        for block in sorted_by_top:
            # 改为用 block.top 判断是否属于同一排，避免 center_y 偏差导致的分行错误
            best_row: dict[str, Any] | None = None
            for row in rows:
                same_row = abs(block.top - row["top"]) <= row_threshold
                if same_row:
                    best_row = row
                    break

            if best_row is None:
                rows.append({
                    "top": block.top,
                    "blocks": [block],
                })
                continue

            best_row["blocks"].append(block)
            best_row["top"] = min(best_row["top"], block.top)

        ordered: list[OCRTextBlock] = []
        for row in sorted(rows, key=lambda value: value["top"]):
            row_blocks = sorted(row["blocks"], key=lambda block: block.left)
            ordered.extend(row_blocks)
        return ordered

    @staticmethod
    def _join_text_fragments(fragments: list[str]) -> str:
        if not fragments:
            return ""

        merged = ""
        for fragment in fragments:
            current = fragment.strip()
            if not current:
                continue
            if not merged:
                merged = current
                continue
            merged += current
        return merged

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        if not text:
            return []

        strong_parts = re.split(r"(?<=[。！？!?；;…])", text)
        sentences: list[str] = []

        for part in strong_parts:
            current = part.strip()
            if not current:
                continue

            if len(current) >= 30 and "，" in current:
                weak_parts = re.split(r"(?<=，)", current)
                for weak in weak_parts:
                    sentence = weak.strip()
                    if sentence:
                        sentences.append(sentence)
                continue

            sentences.append(current)

        return sentences

    def _make_page_payload(
        self,
        chapter_id: str,
        image_index: int,
        lines: list[OCRTextLine],
        panel_rects: list[tuple[float, float, float, float]] | None = None,
    ) -> dict[str, Any]:
        blocks = self._build_blocks(lines)
        panels = self._build_panels_with_rects(blocks, panel_rects)
        segments: list[str] = []
        block_payload: list[dict[str, Any]] = []
        panel_payload: list[dict[str, Any]] = []
        running_block_index = 1

        for panel_index, panel in enumerate(panels, start=1):
            ordered_panel_blocks = self._order_blocks(panel.blocks)
            panel_segments: list[str] = []
            panel_blocks_payload: list[dict[str, Any]] = []

            for block in ordered_panel_blocks:
                block_text = self._join_text_fragments([line.text for line in block.lines])
                block_segments = self._split_sentences(block_text)
                if not block_segments and block_text:
                    block_segments = [block_text]

                segments.extend(block_segments)
                panel_segments.extend(block_segments)

                block_item = {
                    "index": running_block_index,
                    "text": block_text,
                    "segments": block_segments,
                    "bbox": {
                        "left": round(block.left, 2),
                        "top": round(block.top, 2),
                        "right": round(block.right, 2),
                        "bottom": round(block.bottom, 2),
                    },
                }
                running_block_index += 1

                block_payload.append(block_item)
                panel_blocks_payload.append(block_item)

            panel_payload.append(
                {
                    "index": panel_index,
                    "segments": panel_segments,
                    "bbox": {
                        "left": round(panel.left, 2),
                        "top": round(panel.top, 2),
                        "right": round(panel.right, 2),
                        "bottom": round(panel.bottom, 2),
                    },
                    "blocks": panel_blocks_payload,
                }
            )

        full_text = "".join(segments)
        return {
            "chapter_id": chapter_id,
            "image_index": image_index,
            "line_count": len(lines),
            "panel_count": len(panels),
            "block_count": len(block_payload),
            "text": full_text,
            "segments": [{"index": index + 1, "text": segment} for index, segment in enumerate(segments)],
            "panels": panel_payload,
            "blocks": block_payload,
        }

    def _compute_page_dialog(self, chapter_id: str, image_index: int) -> dict[str, Any]:
        image_path = self._resolve_image_path(chapter_id, image_index)
        engine = self._get_engine()
        image_input = self._prepare_image(image_path)

        image_height = float(image_input.shape[0]) if hasattr(image_input, "shape") else 0.0
        image_width = float(image_input.shape[1]) if hasattr(image_input, "shape") else 0.0

        raw_output = engine(image_input)
        ocr_raw = raw_output[0] if isinstance(raw_output, tuple) else raw_output
        lines = self._parse_lines(ocr_raw, image_width, image_height)
        panel_rects = self._detect_panel_rects(image_input)
        return self._make_page_payload(chapter_id, image_index, lines, panel_rects)

    def get_page_dialog(
        self,
        chapter_id: str,
        image_index: int,
    ) -> dict[str, Any]:
        key = self._cache_key(chapter_id, image_index)

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                payload = self._clone_payload(cached)
                payload["cached"] = True
                return payload

            future = self._inflight.get(key)
            if future is None:
                future = Future()
                self._inflight[key] = future
                owner = True
            else:
                owner = False

        if owner:
            try:
                payload = self._compute_page_dialog(chapter_id, image_index)

                with self._lock:
                    self._cache[key] = payload
                    self._cache_order.append(key)
                    self._queued.discard(key)

                    while len(self._cache_order) > self.cache_limit:
                        oldest = self._cache_order.pop(0)
                        self._cache.pop(oldest, None)

                future.set_result(payload)
            except Exception as exc:
                future.set_exception(exc)
                raise
            finally:
                with self._lock:
                    self._inflight.pop(key, None)

            result = self._clone_payload(payload)
            result["cached"] = False
            return result

        payload = future.result()
        result = self._clone_payload(payload)
        result["cached"] = True
        return result

    def enqueue_prefetch(self, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        queued_count = 0
        accepted_tasks: list[dict[str, Any]] = []
        for task in tasks[:OCR_PREFETCH_MAX_TASKS]:
            if not isinstance(task, dict):
                continue

            chapter_id = str(task.get("chapterId", "")).strip()
            if not chapter_id:
                continue

            try:
                image_index = int(task.get("imageIndex"))
            except (TypeError, ValueError):
                continue

            priority = task.get("priority", 80)
            try:
                priority_value = int(priority)
            except (TypeError, ValueError):
                priority_value = 80
            priority_value = max(0, min(999, priority_value))

            chapter = self.manga_index.get_chapter(chapter_id)
            if chapter is None or image_index < 0 or image_index >= len(chapter.images):
                continue

            key = self._cache_key(chapter_id, image_index)
            with self._lock:
                if key in self._cache or key in self._queued:
                    continue

                self._sequence += 1
                sequence = self._sequence
                self._queued.add(key)

            self._queue.put((priority_value, sequence, chapter_id, image_index))
            queued_count += 1
            accepted_tasks.append(
                {
                    "chapterId": chapter_id,
                    "imageIndex": image_index,
                }
            )

        return {
            "queued": queued_count,
            "accepted": accepted_tasks,
        }

    def _prefetch_worker(self) -> None:
        while True:
            try:
                _, _, chapter_id, image_index = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue

            key = self._cache_key(chapter_id, image_index)
            try:
                self.get_page_dialog(chapter_id, image_index)
            except Exception:
                with self._lock:
                    self._queued.discard(key)
            finally:
                self._queue.task_done()



def create_app(library_root: Path) -> Flask:
    app = Flask(__name__)
    app.config["TEMPLATES_AUTO_RELOAD"] = True
    app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
    app.jinja_env.auto_reload = True
    manga_index = MangaIndex(library_root)
    progress_store = ProgressStore(APP_DIR / "data" / "progress.json")
    ocr_service = OCRService(manga_index)

    @app.get("/")
    def home() -> str:
        return render_template("index.html")

    @app.get("/api/library")
    def api_library() -> Any:
        return jsonify(
            {
                "library_root": str(manga_index.library_root),
                "chapter_count": manga_index.chapter_count(),
                "chapters": manga_index.library_summary(),
            }
        )

    @app.get("/api/chapter")
    def api_chapter() -> Any:
        chapter_id = request.args.get("id", "").strip()
        if not chapter_id:
            abort(400, description="缺少章节 id 参数")

        chapter = manga_index.get_chapter(chapter_id)
        if chapter is None:
            abort(404, description="章节不存在")

        chapter_index = manga_index.chapter_index(chapter_id)
        return jsonify(
            {
                "id": chapter.chapter_id,
                "title": chapter.title,
                "parent": chapter.parent,
                "index": chapter_index,
                "total": manga_index.chapter_count(),
                "image_count": len(chapter.images),
            }
        )

    @app.get("/api/image")
    def api_image() -> Any:
        chapter_id = request.args.get("chapter", "").strip()
        raw_image_index = request.args.get("index", "").strip()

        if not chapter_id:
            abort(400, description="缺少 chapter 参数")

        chapter = manga_index.get_chapter(chapter_id)
        if chapter is None:
            abort(404, description="章节不存在")

        try:
            image_index = int(raw_image_index)
        except ValueError:
            abort(400, description="index 必须是整数")

        if image_index < 0 or image_index >= len(chapter.images):
            abort(404, description="图片索引超出范围")

        chapter_dir = manga_index.library_root / Path(chapter.rel_dir)
        image_name = chapter.images[image_index]
        return send_from_directory(str(chapter_dir), image_name, conditional=True)

    @app.get("/api/progress")
    def api_progress() -> Any:
        return jsonify(progress_store.read())

    @app.post("/api/progress")
    def api_save_progress() -> Any:
        payload = request.get_json(silent=True) or {}
        chapter_id = str(payload.get("chapterId", "")).strip()
        if not chapter_id:
            abort(400, description="chapterId 不能为空")

        chapter = manga_index.get_chapter(chapter_id)
        if chapter is None:
            abort(404, description="章节不存在")

        try:
            scroll_ratio = float(payload.get("scrollRatio", 0))
        except (TypeError, ValueError):
            abort(400, description="scrollRatio 必须是数字")

        image_index: int | None = None
        if payload.get("imageIndex") is not None:
            try:
                image_index = int(payload.get("imageIndex"))
            except (TypeError, ValueError):
                image_index = None

        progress_store.update(chapter_id=chapter_id, scroll_ratio=scroll_ratio, image_index=image_index)
        return jsonify({"ok": True})

    @app.post("/api/rescan")
    def api_rescan() -> Any:
        manga_index.reload()
        ocr_service.clear_cache()
        return jsonify(
            {
                "ok": True,
                "chapter_count": manga_index.chapter_count(),
            }
        )

    @app.get("/api/ocr")
    def api_ocr() -> Any:
        chapter_id = request.args.get("chapter", "").strip()
        raw_image_index = request.args.get("index", "").strip()

        if not chapter_id:
            abort(400, description="缺少 chapter 参数")

        try:
            image_index = int(raw_image_index)
        except ValueError:
            abort(400, description="index 必须是整数")

        try:
            payload = ocr_service.get_page_dialog(chapter_id, image_index)
        except ValueError as exc:
            abort(404, description=str(exc))
        except RuntimeError as exc:
            abort(503, description=str(exc))

        return jsonify(payload)

    @app.post("/api/ocr/prefetch")
    def api_ocr_prefetch() -> Any:
        payload = request.get_json(silent=True) or {}
        tasks = payload.get("tasks")
        if not isinstance(tasks, list):
            abort(400, description="tasks 必须是数组")

        enqueue_result = ocr_service.enqueue_prefetch(tasks)
        return jsonify(
            {
                "ok": True,
                "queued": enqueue_result.get("queued", 0),
                "accepted": enqueue_result.get("accepted", []),
            }
        )

    return app



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Windows 自用本地漫画阅读器")
    parser.add_argument(
        "--library",
        default=str(DEFAULT_LIBRARY_DIR),
        help="漫画根目录，默认会尝试使用脚本同级的 一人之下_漫画 目录",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=7878, help="监听端口，默认 7878")
    parser.add_argument(
        "--engine",
        choices=("auto", "waitress", "flask"),
        default="auto",
        help="服务引擎：auto(默认) 优先 waitress，否则 flask",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    library_root = Path(args.library).expanduser().resolve()

    if not library_root.exists() or not library_root.is_dir():
        raise SystemExit(f"漫画目录不存在: {library_root}")

    app = create_app(library_root)
    print(f"漫画目录: {library_root}")
    print(f"打开地址: http://{args.host}:{args.port}")

    engine = args.engine
    if engine == "auto":
        engine = "waitress" if waitress_serve is not None else "flask"

    if engine == "waitress":
        if waitress_serve is None:
            raise SystemExit("未安装 waitress，请先执行: pip install -r requirements.txt")
        print("服务引擎: waitress")
        waitress_serve(app, host=args.host, port=args.port, threads=8)
    else:
        print("服务引擎: flask 开发服务器")
        app.run(host=args.host, port=args.port, debug=False, use_reloader=False)