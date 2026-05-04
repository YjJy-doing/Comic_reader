const state = {
    chapters: [],
    chapterMap: new Map(),
    currentChapterId: null,
    progress: {
        last_chapter_id: null,
        chapters: {},
    },
    libraryConfig: {
        library_path: "",
        library_name: "",
    },
    saveTimer: null,
    pageSyncTimer: null,
    chapterLoadToken: 0,
    currentImageIndex: null,
    speech: {
        supported: typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined",
        enabled: true,
        rate: 1,
        orderMode: "strict",
        token: 0,
        activeKey: null,
        lastSpokenKey: null,
        voice: null,
        dialogCache: new Map(),
        dialogPromises: new Map(),
        prefetchQueued: new Set(),
        prefetchInFlight: false,
    },
};

const SPACE_STEP_RATIO = 0.966; // 页面半屏状态下的“切页标准”可调变量；  页面全屏或全屏模式下调“页面宽度”即可。
const MIN_PAGE_WIDTH = 30;
const DEFAULT_PAGE_WIDTH = 82;
const PAGE_WIDTH_DECIMALS = 2;
const PAGE_WIDTH_STEP = 0.01;
const PAGE_WIDTH_STORAGE_KEY = "manga_reader_page_width";
const THEME_STORAGE_KEY = "manga_reader_theme";

const PAGE_ANCHOR_RATIO = 0.62;
const PAGE_SYNC_DEBOUNCE_MS = 140;
const VOICE_RATE_STORAGE_KEY = "manga_reader_voice_rate";
const VOICE_ENABLED_STORAGE_KEY = "manga_reader_voice_enabled";
const VOICE_ORDER_MODE_STORAGE_KEY = "manga_reader_voice_order_mode";
const VOICE_RATE_OPTIONS = [1, 2, 3];
const VOICE_ORDER_MODE_OPTIONS = ["strict", "balanced"];
const DIALOG_CACHE_LIMIT = 1600;
const PREFETCH_CURRENT_LOOKAHEAD = 4;
const PREFETCH_NEXT_CHAPTER_LIMIT = 12;

const dom = {
    libraryPath: document.getElementById("libraryPath"),
    libraryNameInput: document.getElementById("libraryNameInput"),
    libraryPathInput: document.getElementById("libraryPathInput"),
    applyLibraryBtn: document.getElementById("applyLibraryBtn"),
    libraryHint: document.getElementById("libraryHint"),
    chapterSearch: document.getElementById("chapterSearch"),
    chapterList: document.getElementById("chapterList"),
    chapterCount: document.getElementById("chapterCount"),
    chapterTitle: document.getElementById("chapterTitle"),
    pages: document.getElementById("pages"),
    reader: document.getElementById("reader"),
    statusText: document.getElementById("statusText"),
    progressText: document.getElementById("progressText"),
    prevChapterBtn: document.getElementById("prevChapterBtn"),
    nextChapterBtn: document.getElementById("nextChapterBtn"),
    prevPageBtn: document.getElementById("prevPageBtn"),
    nextPageBtn: document.getElementById("nextPageBtn"),
    widthRange: document.getElementById("widthRange"),
    themeToggleBtn: document.getElementById("themeToggleBtn"),
    fullscreenBtn: document.getElementById("fullscreenBtn"),
    rescanBtn: document.getElementById("rescanBtn"),
    lastReadBtn: document.getElementById("lastReadBtn"),
    voicePanel: document.getElementById("voicePanel"),
    voiceToggleBtn: document.getElementById("voiceToggleBtn"),
    voiceReplayBtn: document.getElementById("voiceReplayBtn"),
    voiceHint: document.getElementById("voiceHint"),
    voiceSpeedButtons: Array.from(document.querySelectorAll(".voice-speed-btn")),
    voiceModeButtons: Array.from(document.querySelectorAll(".voice-mode-btn")),
};

function setStatus(message) {
    if (dom.statusText) {
        dom.statusText.textContent = message;
    }
}

function setVoiceHint(message, isError = false) {
    if (!dom.voiceHint) {
        return;
    }

    dom.voiceHint.textContent = message;
    dom.voiceHint.classList.toggle("error", Boolean(isError));
}

function setLibraryHint(message, isError = false) {
    if (!dom.libraryHint) {
        return;
    }

    dom.libraryHint.textContent = message;
    dom.libraryHint.classList.toggle("error", Boolean(isError));
}

function normalizeLibraryConfig(config) {
    const raw = config && typeof config === "object" ? config : {};
    return {
        library_path: String(raw.library_path || "").trim(),
        library_name: String(raw.library_name || "").trim(),
    };
}

function getSystemTheme() {
    if (typeof window !== "undefined" && window.matchMedia) {
        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    return "light";
}

function loadTheme() {
    try {
        const raw = localStorage.getItem(THEME_STORAGE_KEY);
        if (raw === "dark" || raw === "light") {
            return raw;
        }
    } catch {
        // Ignore storage failures.
    }
    return getSystemTheme();
}

function saveTheme(theme) {
    try {
        localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
        // Ignore storage failures.
    }
}

function updateThemeToggleUi(theme) {
    if (!dom.themeToggleBtn) {
        return;
    }

    dom.themeToggleBtn.textContent = theme === "dark" ? "深色" : "浅色";
    dom.themeToggleBtn.dataset.theme = theme;
}

function applyTheme(theme, options = {}) {
    const { persist = true } = options;
    const normalized = theme === "dark" ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", normalized);
    updateThemeToggleUi(normalized);
    if (persist) {
        saveTheme(normalized);
    }
}

function toggleTheme() {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next, { persist: true });
}

function initTheme() {
    const theme = loadTheme();
    applyTheme(theme, { persist: false });
}

function updateProgressText() {
    if (!dom.reader || !dom.progressText) {
        return;
    }

    const maxScroll = Math.max(1, dom.reader.scrollHeight - dom.reader.clientHeight);
    const ratio = Math.max(0, Math.min(1, dom.reader.scrollTop / maxScroll));
    dom.progressText.textContent = `${Math.round(ratio * 100)}%`;
}

async function requestJson(url, options = {}) {
    const { timeoutMs = 0, ...fetchOptions } = options;
    const controller = timeoutMs > 0 ? new AbortController() : null;
    const timer = controller
        ? setTimeout(() => {
            controller.abort();
        }, timeoutMs)
        : null;

    let response;
    try {
        response = await fetch(url, {
            ...fetchOptions,
            signal: controller ? controller.signal : fetchOptions.signal,
        });
    } catch (error) {
        if (timer) {
            clearTimeout(timer);
        }

        const maybeError = error;
        if (maybeError && maybeError.name === "AbortError") {
            throw new Error(`请求超时（${timeoutMs}ms）`);
        }
        throw error;
    }

    if (timer) {
        clearTimeout(timer);
    }

    if (!response.ok) {
        const message = await response.text();
        throw new Error(`${response.status} ${message}`);
    }
    return response.json();
}

function buildImageUrl(chapterId, imageIndex) {
    const chapterPart = encodeURIComponent(chapterId);
    return `/api/image?chapter=${chapterPart}&index=${imageIndex}`;
}

function getPageKey(chapterId, imageIndex, orderMode = state.speech.orderMode) {
    return `${orderMode}::${chapterId}::${imageIndex}`;
}

function getOrderModeLabel(mode) {
    return mode === "balanced" ? "平衡优先" : "严格优先";
}

function rememberDialogCache(pageKey, payload) {
    state.speech.dialogCache.set(pageKey, payload);

    if (state.speech.dialogCache.size <= DIALOG_CACHE_LIMIT) {
        return;
    }

    const firstKey = state.speech.dialogCache.keys().next().value;
    if (firstKey) {
        state.speech.dialogCache.delete(firstKey);
    }
}

function normalizeDialogPayload(rawPayload) {
    const panelSegments = [];
    const rawPanels = Array.isArray(rawPayload?.panels) ? rawPayload.panels : [];

    for (const panel of rawPanels) {
        const panelBlocks = Array.isArray(panel?.blocks) ? panel.blocks : [];
        for (const block of panelBlocks) {
            const blockSegments = Array.isArray(block?.segments) ? block.segments : [];
            for (const segment of blockSegments) {
                const sentence = String(segment || "").trim();
                if (sentence) {
                    panelSegments.push(sentence);
                }
            }
        }
    }

    const rawSegments = Array.isArray(rawPayload?.segments) ? rawPayload.segments : [];
    const segments = rawSegments
        .map((item) => {
            if (typeof item === "string") {
                return item.trim();
            }
            if (item && typeof item.text === "string") {
                return item.text.trim();
            }
            return "";
        })
        .filter(Boolean);

    const orderedSegments = panelSegments.length > 0 ? panelSegments : segments;
    const text = typeof rawPayload?.text === "string" ? rawPayload.text.trim() : "";
    return {
        text: text || orderedSegments.join(""),
        segments: orderedSegments,
        panelCount: Number(rawPayload?.panel_count || 0),
        blockCount: Number(rawPayload?.block_count || 0),
    };
}

async function fetchPageDialog(chapterId, imageIndex) {
    const pageKey = getPageKey(chapterId, imageIndex);
    const cached = state.speech.dialogCache.get(pageKey);
    if (cached) {
        return cached;
    }

    const inflight = state.speech.dialogPromises.get(pageKey);
    if (inflight) {
        return inflight;
    }

    // 增加超时阈值：strict 模式下较大图片或首次加载模型可能耗时较久
    const timeoutMs = state.speech.orderMode === "strict" ? 90000 : 60000;
    const requestPromise = requestJson(
        `/api/ocr?chapter=${encodeURIComponent(chapterId)}&index=${encodeURIComponent(String(imageIndex))}&mode=${encodeURIComponent(state.speech.orderMode)}`,
        { timeoutMs },
    )
        .then((result) => {
            const normalized = normalizeDialogPayload(result);
            rememberDialogCache(pageKey, normalized);
            return normalized;
        })
        .finally(() => {
            state.speech.dialogPromises.delete(pageKey);
        });

    state.speech.dialogPromises.set(pageKey, requestPromise);
    return requestPromise;
}

function loadVoiceEnabled() {
    try {
        const raw = localStorage.getItem(VOICE_ENABLED_STORAGE_KEY);
        if (raw === null) {
            return true;
        }
        return raw !== "0";
    } catch {
        return true;
    }
}

function saveVoiceEnabled(enabled) {
    try {
        localStorage.setItem(VOICE_ENABLED_STORAGE_KEY, enabled ? "1" : "0");
    } catch {
        // Ignore storage failures and keep runtime state.
    }
}

function loadVoiceRate() {
    try {
        const raw = localStorage.getItem(VOICE_RATE_STORAGE_KEY);
        if (raw === null) {
            return 1;
        }
        const numeric = Number(raw);
        if (!VOICE_RATE_OPTIONS.includes(numeric)) {
            return 1;
        }
        return numeric;
    } catch {
        return 1;
    }
}

function saveVoiceRate(rate) {
    try {
        localStorage.setItem(VOICE_RATE_STORAGE_KEY, String(rate));
    } catch {
        // Ignore storage failures and keep runtime state.
    }
}

function loadVoiceOrderMode() {
    try {
        const raw = localStorage.getItem(VOICE_ORDER_MODE_STORAGE_KEY);
        if (raw === null) {
            return "strict";
        }

        const mode = String(raw).trim().toLowerCase();
        if (!VOICE_ORDER_MODE_OPTIONS.includes(mode)) {
            return "strict";
        }
        return mode;
    } catch {
        return "strict";
    }
}

function saveVoiceOrderMode(mode) {
    try {
        localStorage.setItem(VOICE_ORDER_MODE_STORAGE_KEY, mode);
    } catch {
        // Ignore storage failures and keep runtime state.
    }
}

function pickSpeechVoice() {
    if (!state.speech.supported) {
        return;
    }

    const voices = window.speechSynthesis.getVoices();
    if (!Array.isArray(voices) || voices.length === 0) {
        return;
    }

    const preferred = voices.find((voice) => /^zh/i.test(voice.lang));
    const fallback = voices.find((voice) => /Chinese|Mandarin|中文|国语|普通话/i.test(`${voice.name} ${voice.lang}`));
    state.speech.voice = preferred || fallback || voices[0];
}

function stopSpeechPlayback() {
    state.speech.token += 1;
    state.speech.activeKey = null;

    if (state.speech.supported) {
        window.speechSynthesis.cancel();
    }
}

function speakSegmentsSequentially(segments, token, pageKey, startIndex = 0) {
    if (!state.speech.supported || !state.speech.enabled || token !== state.speech.token) {
        return;
    }

    if (startIndex >= segments.length) {
        if (token === state.speech.token) {
            state.speech.activeKey = null;
        }
        return;
    }

    const text = String(segments[startIndex] || "").trim();
    if (!text) {
        speakSegmentsSequentially(segments, token, pageKey, startIndex + 1);
        return;
    }

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-CN";
    utterance.rate = state.speech.rate;
    utterance.pitch = 1;
    utterance.volume = 1;
    if (state.speech.voice) {
        utterance.voice = state.speech.voice;
    }

    utterance.onend = () => {
        if (token !== state.speech.token) {
            return;
        }
        speakSegmentsSequentially(segments, token, pageKey, startIndex + 1);
    };

    utterance.onerror = () => {
        if (token !== state.speech.token) {
            return;
        }
        speakSegmentsSequentially(segments, token, pageKey, startIndex + 1);
    };

    window.speechSynthesis.speak(utterance);
}

async function playDialogueForPage(chapterId, imageIndex, options = {}) {
    const { force = false } = options;

    if (!state.speech.supported || !state.speech.enabled) {
        return;
    }

    const pageKey = getPageKey(chapterId, imageIndex);
    if (!force && state.speech.lastSpokenKey === pageKey && state.speech.activeKey === pageKey) {
        return;
    }

    stopSpeechPlayback();
    const token = state.speech.token;

    state.speech.activeKey = pageKey;
    state.speech.lastSpokenKey = pageKey;
    setVoiceHint(`正在识别第 ${imageIndex + 1} 页对白...`);

    try {
        const dialog = await fetchPageDialog(chapterId, imageIndex);
        if (token !== state.speech.token) {
            return;
        }

        if (!dialog.segments.length) {
            state.speech.activeKey = null;
            setVoiceHint(`第 ${imageIndex + 1} 页未识别到对白`);
            return;
        }

        const panelInfo = dialog.panelCount > 0 ? `，${dialog.panelCount} 个画面框` : "";
        setVoiceHint(`正在播放第 ${imageIndex + 1} 页对白（${state.speech.rate}x / ${getOrderModeLabel(state.speech.orderMode)}${panelInfo}）`);
        speakSegmentsSequentially(dialog.segments, token, pageKey, 0);

        void queueDialoguePrefetch();
    } catch (error) {
        if (token !== state.speech.token) {
            return;
        }

        state.speech.activeKey = null;
        const message = error instanceof Error ? error.message : String(error);
        setVoiceHint(`对白识别失败: ${message}`, true);
        setStatus(`对白识别失败: ${message}`);
    }
}

function updateVoiceToggleUi() {
    if (!dom.voiceToggleBtn) {
        return;
    }

    dom.voiceToggleBtn.dataset.enabled = state.speech.enabled ? "true" : "false";
    dom.voiceToggleBtn.textContent = state.speech.enabled ? "播放: 开" : "播放: 关";
}

function updateVoiceRateUi() {
    for (const button of dom.voiceSpeedButtons) {
        const numeric = Number(button.dataset.voiceRate || "0");
        button.classList.toggle("active", numeric === state.speech.rate);
    }
}

function updateVoiceModeUi() {
    for (const button of dom.voiceModeButtons) {
        const mode = String(button.dataset.voiceMode || "").toLowerCase();
        button.classList.toggle("active", mode === state.speech.orderMode);
    }
}

function getFilteredChapters() {
    const keyword = dom.chapterSearch.value.trim().toLowerCase();
    if (!keyword) {
        return state.chapters;
    }

    return state.chapters.filter((chapter) => {
        const haystack = `${chapter.title} ${chapter.parent}`.toLowerCase();
        return haystack.includes(keyword);
    });
}

function renderChapterList() {
    const filtered = getFilteredChapters();
    dom.chapterList.innerHTML = "";

    if (filtered.length === 0) {
        const empty = document.createElement("li");
        empty.className = "chapter-sub";
        empty.style.padding = "12px";
        empty.textContent = "没有匹配章节";
        dom.chapterList.appendChild(empty);
        return;
    }

    for (const chapter of filtered) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chapter-item";
        if (chapter.id === state.currentChapterId) {
            button.classList.add("active");
        }

        const title = document.createElement("span");
        title.className = "chapter-title";
        title.textContent = `${String(chapter.index + 1).padStart(3, "0")} ${chapter.title}`;

        const sub = document.createElement("span");
        sub.className = "chapter-sub";
        sub.textContent = `${chapter.parent || "根目录"} | ${chapter.count} 页`;

        button.appendChild(title);
        button.appendChild(sub);
        button.addEventListener("click", () => {
            openChapter(chapter.id, true);
        });

        dom.chapterList.appendChild(button);
    }
}

function getCurrentChapterIndex() {
    return state.chapters.findIndex((chapter) => chapter.id === state.currentChapterId);
}

function getCurrentImageIndex() {
    const images = dom.pages.querySelectorAll("img.page-image");
    if (images.length === 0) {
        return null;
    }

    const anchor = dom.reader.scrollTop + dom.reader.clientHeight * PAGE_ANCHOR_RATIO;
    let currentIndex = 0;

    images.forEach((image, index) => {
        if (image.offsetTop <= anchor) {
            currentIndex = index;
        }
    });

    return currentIndex;
}

function buildPrefetchTasks() {
    if (!state.currentChapterId || state.currentImageIndex === null) {
        return [];
    }

    const tasks = [];
    const currentChapter = state.chapterMap.get(state.currentChapterId);
    if (currentChapter) {
        const currentCount = Number(currentChapter.count) || 0;
        const end = Math.min(currentCount - 1, state.currentImageIndex + PREFETCH_CURRENT_LOOKAHEAD);
        for (let index = state.currentImageIndex; index <= end; index += 1) {
            tasks.push({
                chapterId: state.currentChapterId,
                imageIndex: index,
                mode: state.speech.orderMode,
                priority: index === state.currentImageIndex ? 0 : 8 + (index - state.currentImageIndex),
            });
        }
    }

    const chapterIndex = getCurrentChapterIndex();
    if (chapterIndex >= 0 && chapterIndex + 1 < state.chapters.length) {
        const nextChapter = state.chapters[chapterIndex + 1];
        const nextCount = Number(nextChapter.count) || 0;
        const limit = Math.min(nextCount, PREFETCH_NEXT_CHAPTER_LIMIT);
        for (let index = 0; index < limit; index += 1) {
            tasks.push({
                chapterId: nextChapter.id,
                imageIndex: index,
                mode: state.speech.orderMode,
                priority: 120 + index,
            });
        }
    }

    return tasks;
}

async function queueDialoguePrefetch() {
    if (!state.speech.enabled || !state.currentChapterId || state.currentImageIndex === null || state.speech.prefetchInFlight) {
        return;
    }

    const tasks = buildPrefetchTasks();
    if (!tasks.length) {
        return;
    }

    const pendingTasks = [];
    for (const task of tasks) {
        const pageKey = getPageKey(task.chapterId, task.imageIndex, task.mode);
        if (state.speech.dialogCache.has(pageKey) || state.speech.prefetchQueued.has(pageKey)) {
            continue;
        }
        pendingTasks.push(task);
    }

    if (!pendingTasks.length) {
        return;
    }

    state.speech.prefetchInFlight = true;
    try {
        const result = await requestJson("/api/ocr/prefetch", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({ tasks: pendingTasks }),
            timeoutMs: 15000,
        });
        const accepted = Array.isArray(result?.accepted) ? result.accepted : [];
        for (const task of accepted) {
            const chapterId = String(task?.chapterId || "");
            const imageIndex = Number(task?.imageIndex);
            const mode = String(task?.mode || state.speech.orderMode);
            if (!chapterId || !Number.isFinite(imageIndex)) {
                continue;
            }

            const pageKey = getPageKey(chapterId, imageIndex, mode);
            state.speech.prefetchQueued.add(pageKey);
        }
    } catch {
        // Keep current cache state and retry in next scheduling window.
    } finally {
        state.speech.prefetchInFlight = false;
    }
}

function scheduleCurrentPageSync(options = {}) {
    const { forceVoice = false } = options;

    clearTimeout(state.pageSyncTimer);
    state.pageSyncTimer = setTimeout(() => {
        syncCurrentPage({ forceVoice });
    }, PAGE_SYNC_DEBOUNCE_MS);
}

function syncCurrentPage(options = {}) {
    const { forceVoice = false } = options;

    if (!state.currentChapterId) {
        return;
    }

    const imageIndex = getCurrentImageIndex();
    if (imageIndex === null) {
        return;
    }

    const changed = imageIndex !== state.currentImageIndex;
    state.currentImageIndex = imageIndex;

    if (!changed && !forceVoice) {
        return;
    }

    if (state.speech.enabled) {
        void playDialogueForPage(state.currentChapterId, imageIndex, { force: forceVoice });
        void queueDialoguePrefetch();
    }
}

function applySavedPosition(chapterId, shouldRestore) {
    if (!shouldRestore) {
        dom.reader.scrollTop = 0;
        updateProgressText();
        return;
    }

    const chapterProgress = state.progress.chapters?.[chapterId];
    if (!chapterProgress) {
        dom.reader.scrollTop = 0;
        updateProgressText();
        return;
    }

    if (Number.isInteger(chapterProgress.image_index)) {
        setTimeout(() => {
            const targetImage = dom.pages.querySelector(`img[data-index="${chapterProgress.image_index}"]`);
            if (targetImage) {
                targetImage.scrollIntoView({ block: "start" });
                updateProgressText();
            }
        }, 220);
        return;
    }

    const ratio = typeof chapterProgress.scroll_ratio === "number" ? chapterProgress.scroll_ratio : 0;
    setTimeout(() => {
        const maxScroll = Math.max(1, dom.reader.scrollHeight - dom.reader.clientHeight);
        dom.reader.scrollTop = ratio * maxScroll;
        updateProgressText();
    }, 220);
}

async function openChapter(chapterId, shouldRestore) {
    if (!state.chapterMap.has(chapterId)) {
        return;
    }

    const loadToken = state.chapterLoadToken + 1;
    state.chapterLoadToken = loadToken;
    stopSpeechPlayback();
    state.currentImageIndex = null;

    try {
        setStatus("正在加载章节...");
        const chapter = await requestJson(`/api/chapter?id=${encodeURIComponent(chapterId)}`);
        if (loadToken !== state.chapterLoadToken) {
            return;
        }

        state.currentChapterId = chapterId;
        dom.chapterTitle.textContent = `${chapter.index + 1}/${chapter.total} ${chapter.title}`;
        dom.pages.innerHTML = "";

        if (chapter.image_count <= 0) {
            const empty = document.createElement("div");
            empty.className = "empty";
            empty.textContent = "本章节没有可显示的图片";
            dom.pages.appendChild(empty);
        } else {
            for (let imageIndex = 0; imageIndex < chapter.image_count; imageIndex += 1) {
                const image = document.createElement("img");
                image.className = "page-image";
                image.dataset.index = String(imageIndex);
                image.alt = `${chapter.title} - 第 ${imageIndex + 1} 页`;
                image.src = buildImageUrl(chapterId, imageIndex);
                image.loading = imageIndex < 4 ? "eager" : "lazy";
                image.decoding = "async";
                image.addEventListener("load", () => {
                    if (chapterId === state.currentChapterId) {
                        scheduleCurrentPageSync({ forceVoice: false });
                    }
                });
                dom.pages.appendChild(image);
            }
        }

        renderChapterList();
        applySavedPosition(chapterId, shouldRestore);
        setStatus(`已打开: ${chapter.title}`);

        setTimeout(() => {
            if (chapterId !== state.currentChapterId) {
                return;
            }
            scheduleCurrentPageSync({ forceVoice: true });
        }, 360);
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setStatus(`加载失败: ${message}`);
    }
}

function openRelativeChapter(step) {
    const currentIndex = getCurrentChapterIndex();
    if (currentIndex === -1) {
        return;
    }

    const targetIndex = currentIndex + step;
    if (targetIndex < 0 || targetIndex >= state.chapters.length) {
        return;
    }

    openChapter(state.chapters[targetIndex].id, true);
}

async function saveProgress() {
    if (!state.currentChapterId) {
        return;
    }

    const maxScroll = Math.max(1, dom.reader.scrollHeight - dom.reader.clientHeight);
    const scrollRatio = Math.max(0, Math.min(1, dom.reader.scrollTop / maxScroll));
    const imageIndex = getCurrentImageIndex();

    state.progress.last_chapter_id = state.currentChapterId;
    state.progress.chapters[state.currentChapterId] = {
        scroll_ratio: scrollRatio,
        image_index: imageIndex,
    };

    try {
        await requestJson("/api/progress", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                chapterId: state.currentChapterId,
                scrollRatio,
                imageIndex,
            }),
        });
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setStatus(`进度保存失败: ${message}`);
    }
}

function scheduleSaveProgress() {
    clearTimeout(state.saveTimer);
    updateProgressText();
    state.saveTimer = setTimeout(() => {
        saveProgress();
    }, 500);
}

async function loadProgress() {
    try {
        const progress = await requestJson("/api/progress");
        state.progress = {
            last_chapter_id: progress.last_chapter_id || null,
            chapters: progress.chapters || {},
        };
    } catch {
        state.progress = {
            last_chapter_id: null,
            chapters: {},
        };
    }
}

async function loadLibraryConfig() {
    try {
        const payload = await requestJson("/api/library-config");
        const config = normalizeLibraryConfig(payload.config || {});
        state.libraryConfig = config;

        if (dom.libraryNameInput) {
            dom.libraryNameInput.value = config.library_name;
        }
        if (dom.libraryPathInput) {
            dom.libraryPathInput.value = config.library_path;
        }

        setLibraryHint("优先完整路径；留空则使用文件夹名。", false);
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryHint(`配置读取失败: ${message}`, true);
    }
}

async function loadLibrary() {
    const library = await requestJson("/api/library");
    state.chapters = library.chapters || [];
    state.chapterMap = new Map(state.chapters.map((chapter) => [chapter.id, chapter]));

    dom.libraryPath.textContent = `目录: ${library.library_root}`;
    dom.chapterCount.textContent = `章节数: ${library.chapter_count}`;
}

function showEmptyLibrary(message) {
    dom.pages.innerHTML = '<div class="empty">没有扫描到漫画图片目录</div>';
    dom.chapterTitle.textContent = "没有可阅读章节";
    setStatus(message || "请检查漫画目录是否正确");
}

function getInitialChapterId() {
    const lastChapterId = state.progress.last_chapter_id;
    if (lastChapterId && state.chapterMap.has(lastChapterId)) {
        return lastChapterId;
    }

    if (state.chapters.length > 0) {
        return state.chapters[0].id;
    }

    return null;
}

async function openDefaultChapter() {
    if (state.chapters.length === 0) {
        showEmptyLibrary("请检查漫画目录是否正确");
        return;
    }

    const targetChapterId = getInitialChapterId();
    if (!targetChapterId) {
        showEmptyLibrary("请检查漫画目录是否正确");
        return;
    }

    await openChapter(targetChapterId, true);
}

async function applyLibraryConfig() {
    const libraryPath = dom.libraryPathInput ? dom.libraryPathInput.value.trim() : "";
    const libraryName = dom.libraryNameInput ? dom.libraryNameInput.value.trim() : "";

    if (!libraryPath && !libraryName) {
        setLibraryHint("请填写完整路径或文件夹名", true);
        setStatus("请填写完整路径或文件夹名");
        return;
    }

    setLibraryHint("正在切换漫画库...", false);
    setStatus("正在切换漫画库...");

    try {
        const payload = await requestJson("/api/library-config", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                library_path: libraryPath,
                library_name: libraryName,
            }),
        });

        const config = normalizeLibraryConfig(payload.config || { library_path: libraryPath, library_name: libraryName });
        state.libraryConfig = config;
        if (dom.libraryNameInput) {
            dom.libraryNameInput.value = config.library_name;
        }
        if (dom.libraryPathInput) {
            dom.libraryPathInput.value = config.library_path;
        }

        clearDialogueRuntimeCache();
        state.currentChapterId = null;
        state.currentImageIndex = null;

        await loadLibrary();
        renderChapterList();
        await openDefaultChapter();

        setLibraryHint("漫画库已切换", false);
        setStatus("漫画库已切换");
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setLibraryHint(`切换失败: ${message}`, true);
        setStatus(`切换失败: ${message}`);
    }
}

async function handleRescan() {
    try {
        setStatus("正在重新扫描目录...");
        await requestJson("/api/rescan", { method: "POST" });
        clearDialogueRuntimeCache();

        await loadLibrary();
        renderChapterList();

        if (state.currentChapterId && state.chapterMap.has(state.currentChapterId)) {
            await openChapter(state.currentChapterId, false);
        } else if (state.chapters.length > 0) {
            await openChapter(state.chapters[0].id, false);
        } else {
            dom.pages.innerHTML = '<div class="empty">没有扫描到任何章节</div>';
            dom.chapterTitle.textContent = "没有可阅读章节";
        }

        setStatus("重新扫描完成");
    } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        setStatus(`重新扫描失败: ${message}`);
    }
}

function toggleFullscreen() {
    if (!document.fullscreenElement) {
        document.documentElement.requestFullscreen().catch(() => {});
        return;
    }
    document.exitFullscreen().catch(() => {});
}

function scrollPage(direction) {
    if (!dom.reader) {
        return;
    }

    dom.reader.scrollBy({
        top: direction * dom.reader.clientHeight * SPACE_STEP_RATIO,
        behavior: "smooth",
    });
}

function clampPageWidth(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function roundPageWidth(value) {
    const factor = 10 ** PAGE_WIDTH_DECIMALS;
    return Math.round(value * factor) / factor;
}

function getPageWidthBounds() {
    const min = dom.widthRange ? Number(dom.widthRange.min) || MIN_PAGE_WIDTH : MIN_PAGE_WIDTH;
    const max = dom.widthRange ? Number(dom.widthRange.max) || 100 : 100;
    return { min, max };
}

function applyPageWidth(value, options = {}) {
    const { syncInput = true, formatInput = false } = options;
    const { min, max } = getPageWidthBounds();
    const numeric = Number(value);
    const fallback = dom.widthRange ? Number(dom.widthRange.value) || DEFAULT_PAGE_WIDTH : DEFAULT_PAGE_WIDTH;
    const safeValue = Number.isFinite(numeric) ? numeric : fallback;
    const width = roundPageWidth(clampPageWidth(safeValue, min, max));

    document.documentElement.style.setProperty("--page-width", `${width}%`);

    if (dom.widthRange && syncInput) {
        dom.widthRange.value = formatInput ? width.toFixed(PAGE_WIDTH_DECIMALS) : String(width);
    }

    return width;
}

function savePageWidth(value) {
    try {
        localStorage.setItem(PAGE_WIDTH_STORAGE_KEY, String(value));
    } catch {
        // Ignore storage failures and keep runtime value only.
    }
}

function loadSavedPageWidth() {
    try {
        const raw = localStorage.getItem(PAGE_WIDTH_STORAGE_KEY);
        if (raw === null) {
            return null;
        }

        const numeric = Number(raw);
        if (!Number.isFinite(numeric)) {
            return null;
        }

        const { min, max } = getPageWidthBounds();
        return roundPageWidth(clampPageWidth(numeric, min, max));
    } catch {
        return null;
    }
}

function handlePageWidthInput(options = {}) {
    const { commit = false } = options;

    if (!dom.widthRange) {
        return;
    }

    const raw = dom.widthRange.value.trim();
    if (raw === "") {
        if (!commit) {
            return;
        }

        const savedWidth = loadSavedPageWidth();
        const fallbackWidth = savedWidth ?? DEFAULT_PAGE_WIDTH;
        const restoredWidth = applyPageWidth(fallbackWidth, { syncInput: true, formatInput: true });
        savePageWidth(restoredWidth);
        return;
    }

    const width = applyPageWidth(raw, { syncInput: commit, formatInput: commit });
    savePageWidth(width);
}

function createToolbarButton(id, text) {
    const button = document.createElement("button");
    button.id = id;
    button.type = "button";
    button.textContent = text;
    return button;
}

function ensureToolbarButtons() {
    const actions = document.querySelector(".toolbar-actions");
    if (!actions) {
        return;
    }

    const anchor = actions.querySelector('label[for="widthRange"]') || dom.fullscreenBtn || null;

    if (!dom.prevPageBtn) {
        dom.prevPageBtn = createToolbarButton("prevPageBtn", "上一页 (V)");
        actions.insertBefore(dom.prevPageBtn, anchor);
    }

    if (!dom.nextPageBtn) {
        dom.nextPageBtn = createToolbarButton("nextPageBtn", "下一页 (空格)");
        actions.insertBefore(dom.nextPageBtn, anchor);
    }
}

function syncShortcutLabels() {
    if (dom.prevChapterBtn) {
        dom.prevChapterBtn.textContent = "上一话 (B)";
    }
    if (dom.nextChapterBtn) {
        dom.nextChapterBtn.textContent = "下一话 (N)";
    }
    if (dom.prevPageBtn) {
        dom.prevPageBtn.textContent = "上一页 (V)";
    }
    if (dom.nextPageBtn) {
        dom.nextPageBtn.textContent = "下一页 (空格)";
    }
}

function clearDialogueRuntimeCache() {
    state.speech.dialogCache.clear();
    state.speech.dialogPromises.clear();
    state.speech.prefetchQueued.clear();
    state.speech.prefetchInFlight = false;
    state.speech.activeKey = null;
    state.speech.lastSpokenKey = null;
}

function setVoiceOrderMode(mode, options = {}) {
    const { persist = true, replay = true } = options;
    const normalized = String(mode || "").trim().toLowerCase();
    if (!VOICE_ORDER_MODE_OPTIONS.includes(normalized)) {
        return;
    }

    const changed = normalized !== state.speech.orderMode;
    state.speech.orderMode = normalized;
    if (persist) {
        saveVoiceOrderMode(normalized);
    }

    updateVoiceModeUi();
    if (!changed) {
        return;
    }

    stopSpeechPlayback();
    clearDialogueRuntimeCache();

    if (!state.speech.enabled) {
        setVoiceHint(`识别策略已切换为${getOrderModeLabel(normalized)}（当前播放关闭）`);
        return;
    }

    setVoiceHint(`识别策略已切换为${getOrderModeLabel(normalized)}，将按新策略重新识别。`);
    if (!replay || !state.currentChapterId) {
        return;
    }

    const imageIndex = state.currentImageIndex ?? getCurrentImageIndex();
    if (imageIndex === null) {
        return;
    }

    void playDialogueForPage(state.currentChapterId, imageIndex, { force: true });
    void queueDialoguePrefetch();
}

function setVoiceEnabled(enabled, options = {}) {
    const { persist = true } = options;
    state.speech.enabled = Boolean(enabled) && state.speech.supported;

    if (persist) {
        saveVoiceEnabled(state.speech.enabled);
    }

    updateVoiceToggleUi();

    if (!state.speech.supported) {
        setVoiceHint("当前浏览器不支持语音播放", true);
        if (dom.voiceReplayBtn) {
            dom.voiceReplayBtn.disabled = true;
        }
        return;
    }

    if (dom.voiceReplayBtn) {
        dom.voiceReplayBtn.disabled = !state.speech.enabled;
    }

    if (state.speech.enabled) {
        setVoiceHint(`切页后自动播放当前页对白（${getOrderModeLabel(state.speech.orderMode)}），并后台预识别下一话。`);
        scheduleCurrentPageSync({ forceVoice: true });
    } else {
        stopSpeechPlayback();
        setVoiceHint("对白播放已关闭");
    }
}

function setVoiceRate(rate, options = {}) {
    const { persist = true, replay = false } = options;
    const numeric = Number(rate);
    if (!VOICE_RATE_OPTIONS.includes(numeric)) {
        return;
    }

    state.speech.rate = numeric;
    if (persist) {
        saveVoiceRate(numeric);
    }

    updateVoiceRateUi();

    if (!state.speech.enabled || !state.currentChapterId || state.currentImageIndex === null) {
        return;
    }

    setVoiceHint(`播放速度已切换到 ${numeric}x`);
    if (replay) {
        void playDialogueForPage(state.currentChapterId, state.currentImageIndex, { force: true });
    }
}

function initVoiceControls() {
    if (!state.speech.supported) {
        setVoiceEnabled(false, { persist: false });
        return;
    }

    const enabled = loadVoiceEnabled();
    const rate = loadVoiceRate();
    const orderMode = loadVoiceOrderMode();

    state.speech.rate = rate;
    state.speech.orderMode = orderMode;
    updateVoiceRateUi();
    updateVoiceModeUi();

    pickSpeechVoice();
    if (typeof window.speechSynthesis.addEventListener === "function") {
        window.speechSynthesis.addEventListener("voiceschanged", pickSpeechVoice);
    } else {
        window.speechSynthesis.onvoiceschanged = pickSpeechVoice;
    }

    setVoiceEnabled(enabled, { persist: false });
}

function bindEvents() {
    if (dom.chapterSearch) {
        dom.chapterSearch.addEventListener("input", renderChapterList);
    }

    if (dom.prevChapterBtn) {
        dom.prevChapterBtn.addEventListener("click", () => openRelativeChapter(-1));
    }
    if (dom.nextChapterBtn) {
        dom.nextChapterBtn.addEventListener("click", () => openRelativeChapter(1));
    }
    if (dom.prevPageBtn) {
        dom.prevPageBtn.addEventListener("click", () => scrollPage(-1));
    }
    if (dom.nextPageBtn) {
        dom.nextPageBtn.addEventListener("click", () => scrollPage(1));
    }
    if (dom.fullscreenBtn) {
        dom.fullscreenBtn.addEventListener("click", toggleFullscreen);
    }
    if (dom.themeToggleBtn) {
        dom.themeToggleBtn.addEventListener("click", toggleTheme);
    }
    if (dom.rescanBtn) {
        dom.rescanBtn.addEventListener("click", handleRescan);
    }
    if (dom.applyLibraryBtn) {
        dom.applyLibraryBtn.addEventListener("click", () => {
            void applyLibraryConfig();
        });
    }

    if (dom.lastReadBtn) {
        dom.lastReadBtn.addEventListener("click", () => {
            const chapterId = state.progress.last_chapter_id;
            if (chapterId && state.chapterMap.has(chapterId)) {
                openChapter(chapterId, true);
                return;
            }
            setStatus("没有可恢复的阅读记录");
        });
    }

    if (dom.widthRange) {
        dom.widthRange.addEventListener("input", () => {
            handlePageWidthInput({ commit: false });
        });
        dom.widthRange.addEventListener("change", () => {
            handlePageWidthInput({ commit: true });
        });
        dom.widthRange.addEventListener("blur", () => {
            handlePageWidthInput({ commit: true });
        });
    }

    if (dom.reader) {
        dom.reader.addEventListener("scroll", () => {
            scheduleSaveProgress();
            scheduleCurrentPageSync({ forceVoice: false });
        });
    }

    if (dom.voiceToggleBtn) {
        dom.voiceToggleBtn.addEventListener("click", () => {
            setVoiceEnabled(!state.speech.enabled, { persist: true });
        });
    }

    if (dom.voiceReplayBtn) {
        dom.voiceReplayBtn.addEventListener("click", () => {
            if (!state.currentChapterId) {
                setVoiceHint("当前没有可重播页面", true);
                return;
            }

            const imageIndex = state.currentImageIndex ?? getCurrentImageIndex();
            if (imageIndex === null) {
                setVoiceHint("当前没有可重播页面", true);
                return;
            }

            void playDialogueForPage(state.currentChapterId, imageIndex, { force: true });
        });
    }

    for (const button of dom.voiceSpeedButtons) {
        button.addEventListener("click", () => {
            const rate = Number(button.dataset.voiceRate || "1");
            setVoiceRate(rate, { persist: true, replay: true });
        });
    }

    for (const button of dom.voiceModeButtons) {
        button.addEventListener("click", () => {
            const mode = String(button.dataset.voiceMode || "").toLowerCase();
            setVoiceOrderMode(mode, { persist: true, replay: true });
        });
    }

    document.addEventListener("keydown", (event) => {
        if (event.ctrlKey || event.altKey || event.metaKey) {
            return;
        }

        const tag = event.target?.tagName?.toLowerCase() || "";
        if (tag === "input" || tag === "textarea") {
            return;
        }

        const key = event.key.toLowerCase();

        if (event.code === "Space") {
            event.preventDefault();
            const direction = event.shiftKey ? -1 : 1;
            scrollPage(direction);
            return;
        }

        if (key === "b") {
            event.preventDefault();
            openRelativeChapter(-1);
        }
        if (key === "n") {
            event.preventDefault();
            openRelativeChapter(1);
        }
        if (key === "v") {
            event.preventDefault();
            scrollPage(-1);
        }
        if (key === "f") {
            event.preventDefault();
            toggleFullscreen();
        }
    });
}

async function init() {
    initTheme();
    ensureToolbarButtons();
    syncShortcutLabels();
    bindEvents();
    initVoiceControls();

    if (dom.widthRange) {
        dom.widthRange.min = String(MIN_PAGE_WIDTH);
        dom.widthRange.step = String(PAGE_WIDTH_STEP);
    }

    const savedWidth = loadSavedPageWidth();
    const initialWidth = savedWidth ?? (dom.widthRange ? dom.widthRange.value : DEFAULT_PAGE_WIDTH);
    const appliedWidth = applyPageWidth(initialWidth, { syncInput: true, formatInput: true });
    savePageWidth(appliedWidth);

    await loadLibraryConfig();
    await loadProgress();
    await loadLibrary();
    renderChapterList();
    await openDefaultChapter();
}

init();
