(function () {
  "use strict";

  var els = {
    shareText: document.getElementById("shareText"),
    extractBtn: document.getElementById("extractBtn"),
    extractLabel: document.querySelector("#extractBtn .btn-label"),
    notice: document.getElementById("notice"),
    playerCard: document.getElementById("playerCard"),
    playToggle: document.getElementById("playToggle"),
    playIcon: document.getElementById("playIcon"),
    nowPlayingTitle: document.getElementById("nowPlayingTitle"),
    nowPlayingMeta: document.getElementById("nowPlayingMeta"),
    sourceBadge: document.getElementById("sourceBadge"),
    nextBtn: document.getElementById("nextBtn"),
    audio: document.getElementById("audio"),
    autoNext: document.getElementById("autoNext"),
    audioList: document.getElementById("audioList"),
    audioCount: document.getElementById("audioCount"),
    emptyState: document.getElementById("emptyState"),
    loadingList: document.getElementById("loadingList"),
    refreshBtn: document.getElementById("refreshBtn"),
    toast: document.getElementById("toast"),
    confirmModal: document.getElementById("confirmModal"),
    confirmText: document.getElementById("confirmText"),
    cancelDelete: document.getElementById("cancelDelete"),
    confirmDelete: document.getElementById("confirmDelete"),
    itemTemplate: document.getElementById("audioItemTemplate"),
  };

  var state = {
    items: [],
    nodes: new Map(),
    currentId: null,
    extracting: false,
    loadingList: false,
    pendingDelete: null,
    lastFocus: null,
    toastTimer: null,
  };

  var platformLabels = {
    douyin: "抖音",
  };

  var dateFormatter = null;
  try {
    dateFormatter = new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch (error) {
    dateFormatter = null;
  }

  function api(path, options) {
    var opts = options || {};
    var headers = Object.assign({ Accept: "application/json" }, opts.headers || {});
    if (opts.body) {
      headers["Content-Type"] = "application/json";
    }

    return fetch(path, Object.assign({}, opts, { headers: headers, cache: "no-store" }))
      .catch(function () {
        throw new Error("无法连接服务器，请检查网络或服务是否在运行");
      })
      .then(function (response) {
        var contentType = response.headers.get("content-type") || "";
        var payloadPromise = contentType.indexOf("application/json") >= 0
          ? response.json().catch(function () { return null; })
          : Promise.resolve(null);

        return payloadPromise.then(function (payload) {
          if (!response.ok) {
            var detail = payload && payload.detail;
            var message = "";
            if (typeof detail === "string") {
              message = detail;
            } else if (detail && detail.message) {
              message = detail.message;
            } else if (payload && payload.message) {
              message = payload.message;
            } else if (response.status === 429) {
              message = "服务器正忙，请稍后重试";
            } else {
              message = "请求失败（HTTP " + response.status + "）";
            }
            var error = new Error(message);
            error.status = response.status;
            throw error;
          }
          return payload;
        });
      });
  }

  function formatDuration(seconds) {
    if (typeof seconds !== "number" || !isFinite(seconds) || seconds <= 0) {
      return "时长未知";
    }
    var total = Math.floor(seconds + 0.5);
    var hours = Math.floor(total / 3600);
    var minutes = Math.floor((total % 3600) / 60);
    var secs = total % 60;
    if (hours > 0) {
      return hours + ":" + pad2(minutes) + ":" + pad2(secs);
    }
    return minutes + ":" + pad2(secs);
  }

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function formatDate(iso) {
    if (!iso) {
      return "时间未知";
    }
    var date = new Date(iso);
    if (isNaN(date.getTime())) {
      return iso;
    }
    try {
      return dateFormatter ? dateFormatter.format(date) : date.toLocaleString();
    } catch (error) {
      return date.toLocaleString();
    }
  }

  function platformName(platform) {
    if (!platform) {
      return "未知平台";
    }
    return platformLabels[platform] || platform;
  }

  function sourceLabel(item) {
    var source;
    if (item.source_kind === "video_ffmpeg") {
      source = "视频完整音轨";
    } else if (item.source_kind === "music_transcoded") {
      source = item.media_type === "image" ? "图文原声（转码）" : "原声（转码）";
    } else {
      source = item.media_type === "image" ? "图文原声" : "作品原声";
    }
    return platformName(item.platform) + " · " + source;
  }

  function findItem(id) {
    for (var i = 0; i < state.items.length; i += 1) {
      if (String(state.items[i].id) === String(id)) {
        return state.items[i];
      }
    }
    return null;
  }

  function setNotice(type, message) {
    els.notice.hidden = false;
    els.notice.className = "notice " + type;
    els.notice.textContent = message;
  }

  function hideNotice() {
    els.notice.hidden = true;
    els.notice.textContent = "";
  }

  function showToast(message, type, duration) {
    if (state.toastTimer) {
      clearTimeout(state.toastTimer);
    }
    els.toast.hidden = false;
    els.toast.className = "toast " + (type || "info");
    els.toast.textContent = message;
    state.toastTimer = setTimeout(function () {
      els.toast.hidden = true;
    }, duration || 4200);
  }

  function setExtracting(busy) {
    state.extracting = busy;
    els.extractBtn.disabled = busy;
    els.extractBtn.classList.toggle("loading", busy);
    els.extractLabel.textContent = busy ? "正在提取…" : "提取音频";
  }

  function setListLoading(loading) {
    state.loadingList = loading;
    els.loadingList.hidden = !loading;
    els.refreshBtn.disabled = loading;
  }

  function renderList() {
    state.nodes.clear();
    els.audioList.textContent = "";

    state.items.forEach(function (item) {
      var fragment = els.itemTemplate.content.cloneNode(true);
      var node = fragment.querySelector(".audio-item");
      var main = fragment.querySelector(".item-main");
      var title = fragment.querySelector(".item-title");
      var platform = fragment.querySelector(".item-platform");
      var author = fragment.querySelector(".item-author");
      var date = fragment.querySelector(".item-date");
      var duration = fragment.querySelector(".item-duration");
      var deleteBtn = fragment.querySelector(".delete-btn");

      node.dataset.id = String(item.id);
      title.textContent = item.title || "未命名音频";
      platform.textContent = platformName(item.platform);
      author.textContent = item.author || "未知作者";
      date.textContent = formatDate(item.created_at);
      duration.textContent = formatDuration(item.duration_seconds);

      main.setAttribute("aria-label", "播放 " + (item.title || "未命名音频"));
      main.addEventListener("click", function () {
        onItemClick(item);
      });

      deleteBtn.setAttribute("aria-label", "删除 " + (item.title || "未命名音频"));
      deleteBtn.addEventListener("click", function () {
        requestDelete(item);
      });

      state.nodes.set(String(item.id), node);
      els.audioList.appendChild(fragment);
    });

    els.audioCount.textContent = String(state.items.length);
    els.emptyState.hidden = state.loadingList || state.items.length > 0;
    els.nextBtn.disabled = state.items.length < 2;
    updateItemStates();
  }

  function updateItemStates() {
    var playing = !els.audio.paused && !els.audio.ended && state.currentId !== null;
    state.nodes.forEach(function (node, id) {
      var isCurrent = String(state.currentId) === String(id);
      node.classList.toggle("is-current", isCurrent);
      node.classList.toggle("is-playing", isCurrent && playing);
      var icon = node.querySelector(".item-play");
      var main = node.querySelector(".item-main");
      if (icon) {
        icon.textContent = isCurrent && playing ? "❚❚" : "▶";
      }
      if (main) {
        main.setAttribute("aria-pressed", isCurrent ? "true" : "false");
      }
    });

    els.playToggle.classList.toggle("is-playing", playing);
    els.playIcon.textContent = playing ? "❚❚" : "▶";
    els.playToggle.setAttribute("aria-label", playing ? "暂停" : "播放");
    updateMediaSessionState(playing);
  }

  function updateCurrentDisplay(item) {
    els.nowPlayingTitle.textContent = item.title || "未命名音频";
    els.nowPlayingMeta.textContent = (item.author || "未知作者") + " · " + sourceLabel(item);
    els.sourceBadge.textContent = sourceLabel(item);
    els.sourceBadge.title = "音频保存在服务器：" + (item.file_size
      ? formatBytes(item.file_size)
      : "大小未知");
    updateMediaMetadata(item);
  }

  function formatBytes(bytes) {
    if (typeof bytes !== "number" || bytes <= 0) {
      return "0 B";
    }
    if (bytes < 1024 * 1024) {
      return Math.round(bytes / 1024) + " KB";
    }
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function selectItem(item, options) {
    var opts = options || {};
    var changed = String(state.currentId) !== String(item.id);
    state.currentId = item.id;
    els.playerCard.hidden = false;
    updateCurrentDisplay(item);

    if (changed || els.audio.dataset.audioId !== String(item.id)) {
      els.audio.pause();
      els.audio.dataset.audioId = String(item.id);
      els.audio.src = item.audio_url;
      els.audio.load();
    }

    updateItemStates();

    if (opts.scroll) {
      try {
        els.playerCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (error) {
        els.playerCard.scrollIntoView();
      }
    }

    if (opts.autoplay) {
      playCurrent();
    }
  }

  function playCurrent() {
    if (state.currentId === null) {
      if (state.items.length > 0) {
        selectItem(state.items[0], { autoplay: true });
      } else {
        showToast("音频库还是空的，请先提取音频", "info");
      }
      return Promise.resolve();
    }

    return els.audio.play().catch(function () {
      showToast("浏览器阻止了播放，请再点一次播放按钮", "info");
      updateItemStates();
    });
  }

  function togglePlayback() {
    if (state.currentId === null) {
      playCurrent();
      return;
    }
    if (els.audio.paused || els.audio.ended) {
      if (els.audio.ended) {
        els.audio.currentTime = 0;
      }
      playCurrent();
    } else {
      els.audio.pause();
    }
  }

  function onItemClick(item) {
    if (String(state.currentId) === String(item.id)) {
      togglePlayback();
      return;
    }
    selectItem(item, { autoplay: true, scroll: true });
  }

  function playNext(wrap, autoplay) {
    if (state.items.length === 0) {
      return false;
    }
    var index = -1;
    for (var i = 0; i < state.items.length; i += 1) {
      if (String(state.items[i].id) === String(state.currentId)) {
        index = i;
        break;
      }
    }

    var nextIndex = index + 1;
    if (nextIndex >= state.items.length) {
      if (!wrap) {
        return false;
      }
      nextIndex = 0;
    }
    if (nextIndex === index) {
      els.audio.currentTime = 0;
      if (autoplay) {
        playCurrent();
      }
      return true;
    }
    selectItem(state.items[nextIndex], { autoplay: autoplay !== false, scroll: true });
    return true;
  }

  function playPrevious() {
    if (state.items.length === 0) {
      return false;
    }
    var index = -1;
    for (var i = 0; i < state.items.length; i += 1) {
      if (String(state.items[i].id) === String(state.currentId)) {
        index = i;
        break;
      }
    }
    var previousIndex = index - 1;
    if (previousIndex < 0) {
      previousIndex = state.items.length - 1;
    }
    selectItem(state.items[previousIndex], { autoplay: true, scroll: true });
    return true;
  }

  function clearPlayer() {
    state.currentId = null;
    els.audio.pause();
    els.audio.removeAttribute("src");
    delete els.audio.dataset.audioId;
    els.audio.load();
    els.playerCard.hidden = true;
    els.nowPlayingTitle.textContent = "选择一首音频";
    els.nowPlayingMeta.textContent = "";
    els.sourceBadge.textContent = "本地音频";
    updateItemStates();
    clearMediaMetadata();
  }

  function loadAudios(focusId) {
    if (state.loadingList) {
      return Promise.resolve();
    }
    setListLoading(true);
    // Keep the previous list visible while refreshing; only the initial call
    // has an empty list.
    els.emptyState.hidden = true;

    return api("/api/audios")
      .then(function (payload) {
        var items = payload && Array.isArray(payload.items) ? payload.items : [];
        state.items = items;
        renderList();

        if (focusId !== undefined && focusId !== null) {
          var focusItem = findItem(focusId);
          if (focusItem) {
            selectItem(focusItem, { autoplay: false, scroll: true });
            return;
          }
        }

        if (state.currentId !== null) {
          var current = findItem(state.currentId);
          if (current) {
            updateCurrentDisplay(current);
          } else {
            clearPlayer();
          }
        }
      })
      .catch(function (error) {
        showToast(error.message || "读取音频列表失败", "error", 6000);
        if (state.items.length === 0) {
          els.emptyState.hidden = false;
        }
      })
      .then(function () {
        setListLoading(false);
        els.emptyState.hidden = state.items.length > 0;
      });
  }

  function extractAudio() {
    if (state.extracting) {
      return;
    }
    var text = els.shareText.value.trim();
    if (!text) {
      setNotice("error", "请先粘贴分享文案或链接。");
      els.shareText.focus();
      return;
    }

    setExtracting(true);
    hideNotice();
    setNotice(
      "info",
      "正在解析分享链接并获取音频，请稍候。视频作品可能需要 FFmpeg 转码，耗时会更长。"
    );

    api("/api/extract", {
      method: "POST",
      body: JSON.stringify({ share_text: text }),
    })
      .then(function (payload) {
        var item = payload && payload.data;
        if (payload && payload.duplicate) {
          setNotice("success", "这首音频已经在音频库中，已为你定位到列表。");
        } else {
          setNotice("success", "提取完成，音频已保存到服务器，可以直接播放。");
        }
        els.shareText.value = "";
        if (item) {
          return loadAudios(item.id);
        }
        return loadAudios();
      })
      .catch(function (error) {
        setNotice("error", error.message || "提取失败，请稍后重试。");
      })
      .then(function () {
        setExtracting(false);
      });
  }

  function requestDelete(item) {
    state.pendingDelete = item;
    state.lastFocus = document.activeElement;
    els.confirmText.textContent = "将同时删除服务器上的音频文件：“" + (item.title || "未命名音频") + "”。";
    els.confirmModal.hidden = false;
    setTimeout(function () {
      els.cancelDelete.focus();
    }, 20);
  }

  function closeDeleteModal() {
    state.pendingDelete = null;
    els.confirmModal.hidden = true;
    if (state.lastFocus && typeof state.lastFocus.focus === "function") {
      try {
        state.lastFocus.focus();
      } catch (error) {
        /* ignore */
      }
    }
    state.lastFocus = null;
  }

  function deleteItem(item) {
    return api("/api/audios/" + encodeURIComponent(item.id), { method: "DELETE" })
      .then(function () {
        var wasCurrent = String(state.currentId) === String(item.id);
        state.items = state.items.filter(function (entry) {
          return String(entry.id) !== String(item.id);
        });
        if (wasCurrent) {
          clearPlayer();
        }
        renderList();
        showToast("已删除音频及服务器文件", "success");
      })
      .catch(function (error) {
        showToast(error.message || "删除失败", "error", 6000);
      });
  }

  function setupMediaSession() {
    if (!("mediaSession" in navigator) || !navigator.mediaSession) {
      return;
    }
    try {
      navigator.mediaSession.setActionHandler("play", function () {
        playCurrent();
      });
      navigator.mediaSession.setActionHandler("pause", function () {
        els.audio.pause();
      });
      navigator.mediaSession.setActionHandler("nexttrack", function () {
        playNext(true, true);
      });
      navigator.mediaSession.setActionHandler("previoustrack", function () {
        playPrevious();
      });
    } catch (error) {
      /* some car browsers expose a partial Media Session API */
    }
  }

  function updateMediaMetadata(item) {
    if (!("mediaSession" in navigator) || !navigator.mediaSession) {
      return;
    }
    if (typeof window.MediaMetadata !== "function") {
      return;
    }
    try {
      navigator.mediaSession.metadata = new window.MediaMetadata({
        title: item.title || "音频",
        artist: item.author || "未知作者",
        album: "链接音频库",
      });
    } catch (error) {
      /* metadata is optional */
    }
  }

  function updateMediaSessionState(playing) {
    if (!("mediaSession" in navigator) || !navigator.mediaSession) {
      return;
    }
    try {
      navigator.mediaSession.playbackState = playing ? "playing" : "paused";
    } catch (error) {
      /* ignore */
    }
  }

  function clearMediaMetadata() {
    if (!("mediaSession" in navigator) || !navigator.mediaSession) {
      return;
    }
    try {
      navigator.mediaSession.metadata = null;
      navigator.mediaSession.playbackState = "none";
    } catch (error) {
      /* ignore */
    }
  }

  function loadAutoNextPreference() {
    try {
      var stored = window.localStorage.getItem("link-audio-auto-next");
      if (stored === null) {
        // Migrate the preference from the original Douyin-only name.
        stored = window.localStorage.getItem("douyin-audio-auto-next");
      }
      if (stored !== null) {
        els.autoNext.checked = stored === "1";
      }
    } catch (error) {
      /* private browsing may block localStorage */
    }
  }

  function saveAutoNextPreference() {
    try {
      window.localStorage.setItem("link-audio-auto-next", els.autoNext.checked ? "1" : "0");
    } catch (error) {
      /* ignore */
    }
  }

  els.extractBtn.addEventListener("click", extractAudio);
  els.refreshBtn.addEventListener("click", function () {
    loadAudios();
  });
  els.playToggle.addEventListener("click", togglePlayback);
  els.nextBtn.addEventListener("click", function () {
    if (!playNext(true, true)) {
      showToast("没有下一首了", "info");
    }
  });
  els.autoNext.addEventListener("change", saveAutoNextPreference);

  els.audio.addEventListener("play", updateItemStates);
  els.audio.addEventListener("pause", updateItemStates);
  els.audio.addEventListener("ended", function () {
    updateItemStates();
    if (els.autoNext.checked && !playNext(false, true)) {
      updateItemStates();
    }
  });
  els.audio.addEventListener("error", function () {
    if (state.currentId !== null) {
      showToast("音频加载失败，文件可能已被删除或格式不受支持", "error", 6000);
    }
  });
  els.audio.addEventListener("loadedmetadata", function () {
    if (state.currentId === null) {
      return;
    }
    var current = findItem(state.currentId);
    if (!current || current.duration_seconds) {
      return;
    }
    var duration = els.audio.duration;
    if (isFinite(duration) && duration > 0) {
      current.duration_seconds = duration;
      var node = state.nodes.get(String(current.id));
      if (node) {
        node.querySelector(".item-duration").textContent = formatDuration(duration);
      }
    }
  });

  els.cancelDelete.addEventListener("click", function () {
    closeDeleteModal();
  });
  els.confirmDelete.addEventListener("click", function () {
    var item = state.pendingDelete;
    closeDeleteModal();
    if (item) {
      deleteItem(item);
    }
  });
  els.confirmModal.addEventListener("click", function (event) {
    if (event.target === els.confirmModal) {
      closeDeleteModal();
    }
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && !els.confirmModal.hidden) {
      closeDeleteModal();
    }
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      extractAudio();
    }
  });

  setupMediaSession();
  loadAutoNextPreference();
  loadAudios();
})();
