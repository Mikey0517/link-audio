(function () {
  "use strict";

  var L = window.LinkAudio;
  var els = {
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
    playAllBtn: document.getElementById("playAllBtn"),
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
    loadingList: false,
    pendingDelete: null,
    lastFocus: null,
    deleting: {},
  };

  function findItem(id) {
    for (var i = 0; i < state.items.length; i += 1) {
      if (String(state.items[i].id) === String(id)) {
        return state.items[i];
      }
    }
    return null;
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
      platform.textContent = L.platformName(item.platform);
      author.textContent = item.author || "未知作者";
      date.textContent = L.formatDate(item.created_at);
      duration.textContent = L.formatDuration(item.duration_seconds);

      main.setAttribute("aria-label", "播放 " + (item.title || "未命名音频"));
      main.addEventListener("click", function () {
        onItemClick(item);
      });

      deleteBtn.setAttribute("aria-label", "删除 " + (item.title || "未命名音频"));
      deleteBtn.addEventListener("click", function () {
        requestDelete(item);
      });

      if (state.deleting[String(item.id)]) {
        node.classList.add("is-deleting");
        deleteBtn.disabled = true;
        deleteBtn.textContent = "删除中";
        deleteBtn.setAttribute("aria-busy", "true");
      }

      state.nodes.set(String(item.id), node);
      els.audioList.appendChild(fragment);
    });

    els.audioCount.textContent = String(state.items.length);
    els.emptyState.hidden = state.loadingList || state.items.length > 0;
    els.playAllBtn.disabled = state.items.length === 0;
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
    els.playToggle.disabled = state.items.length === 0;
    els.playerCard.classList.toggle("is-playing", playing);
    updateMediaSessionState(playing);
  }

  function updateCurrentDisplay(item) {
    els.nowPlayingTitle.textContent = item.title || "未命名音频";
    els.nowPlayingMeta.textContent =
      (item.author || "未知作者") + " · " + L.platformName(item.platform);
    els.sourceBadge.textContent = L.sourceLabel(item);
    els.sourceBadge.title = "音频保存在服务器：" + (item.file_size
      ? L.formatBytes(item.file_size)
      : "大小未知");
    updateMediaMetadata(item);
  }

  function selectItem(item, options) {
    var opts = options || {};
    var changed = String(state.currentId) !== String(item.id);
    state.currentId = item.id;
    els.playerCard.classList.remove("is-empty");
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
        L.showToast("音乐库还是空的，请先解析一条音频", "info");
      }
      return Promise.resolve();
    }

    return els.audio.play().catch(function () {
      L.showToast("浏览器阻止了播放，请再点一次播放按钮", "info");
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
    if (state.deleting[String(item.id)]) {
      return;
    }
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
    els.playerCard.classList.add("is-empty");
    els.playerCard.classList.remove("is-playing");
    els.nowPlayingTitle.textContent = "选择一首音频";
    els.nowPlayingMeta.textContent = "播放列表中的音频会显示在这里";
    els.sourceBadge.textContent = "本地音频";
    els.sourceBadge.title = "";
    updateItemStates();
    clearMediaMetadata();
  }

  function loadAudios(focusId) {
    if (state.loadingList) {
      return Promise.resolve();
    }
    setListLoading(true);
    els.emptyState.hidden = true;

    return L.api("/api/audios")
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

        if (state.currentId === null && state.items.length > 0) {
          selectItem(state.items[0], { autoplay: false, scroll: false });
        } else if (state.currentId === null) {
          clearPlayer();
        }
      })
      .catch(function (error) {
        L.showToast(error.message || "读取音频列表失败", "error", 6000);
        if (state.items.length === 0) {
          els.emptyState.hidden = false;
        }
      })
      .then(function () {
        setListLoading(false);
        els.emptyState.hidden = state.items.length > 0;
      });
  }

  function setDeleteLoading(item, loading) {
    var node = state.nodes.get(String(item.id));
    if (!node) {
      return;
    }
    node.classList.toggle("is-deleting", loading);
    var button = node.querySelector(".delete-btn");
    if (button) {
      button.disabled = loading;
      button.textContent = loading ? "删除中" : "删除";
      button.setAttribute("aria-busy", loading ? "true" : "false");
    }
    if (loading) {
      var main = node.querySelector(".item-main");
      if (main) {
        main.disabled = true;
      }
    } else {
      var itemMain = node.querySelector(".item-main");
      if (itemMain) {
        itemMain.disabled = false;
      }
    }
  }

  function requestDelete(item) {
    if (state.deleting[String(item.id)]) {
      return;
    }
    state.pendingDelete = item;
    state.lastFocus = document.activeElement;
    els.confirmText.textContent =
      "将同时删除服务器上的音频文件：“" + (item.title || "未命名音频") + "”。";
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
    var key = String(item.id);
    if (state.deleting[key]) {
      return Promise.resolve();
    }
    state.deleting[key] = true;
    setDeleteLoading(item, true);
    return L.api("/api/audios/" + encodeURIComponent(item.id), { method: "DELETE" })
      .then(function () {
        var wasCurrent = String(state.currentId) === String(item.id);
        state.items = state.items.filter(function (entry) {
          return String(entry.id) !== String(item.id);
        });
        delete state.deleting[key];
        if (wasCurrent) {
          clearPlayer();
        }
        renderList();
        if (state.currentId === null && state.items.length > 0) {
          selectItem(state.items[0], { autoplay: false, scroll: false });
        }
        L.showToast("已删除音频及服务器文件", "success");
      })
      .catch(function (error) {
        delete state.deleting[key];
        setDeleteLoading(item, false);
        L.showToast(error.message || "删除失败", "error", 6000);
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

  els.refreshBtn.addEventListener("click", function () {
    loadAudios();
  });
  els.playAllBtn.addEventListener("click", function () {
    if (state.items.length === 0) {
      L.showToast("音乐库还是空的，请先解析一条音频", "info");
      return;
    }
    selectItem(state.items[0], { autoplay: true, scroll: true });
  });
  els.playToggle.addEventListener("click", togglePlayback);
  els.nextBtn.addEventListener("click", function () {
    if (!playNext(true, true)) {
      L.showToast("没有下一首了", "info");
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
      L.showToast("音频加载失败，文件可能已被删除或格式不受支持", "error", 6000);
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
        node.querySelector(".item-duration").textContent = L.formatDuration(duration);
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
  });

  setupMediaSession();
  loadAutoNextPreference();
  loadAudios();
})();
