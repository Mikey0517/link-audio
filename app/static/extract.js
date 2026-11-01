(function () {
  "use strict";

  var L = window.LinkAudio;
  var els = {
    shareText: document.getElementById("shareText"),
    extractCard: document.getElementById("extractCard"),
    extractBtn: document.getElementById("extractBtn"),
    extractLabel: document.querySelector("#extractBtn .btn-label"),
    extractProgress: document.getElementById("extractProgress"),
    progressLabel: document.getElementById("progressLabel"),
    progressElapsed: document.getElementById("progressElapsed"),
    notice: document.getElementById("notice"),
    successActions: document.getElementById("successActions"),
    successLink: document.querySelector("#successActions .success-btn"),
  };

  var state = {
    extracting: false,
    progressTimer: null,
    progressStart: 0,
  };

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
  }

  function formatElapsed(milliseconds) {
    var total = Math.max(0, Math.floor(milliseconds / 1000));
    if (total < 60) {
      return total + "s";
    }
    return Math.floor(total / 60) + "m " + pad2(total % 60) + "s";
  }

  function updateProgressElapsed() {
    if (!state.progressStart) {
      return;
    }
    els.progressElapsed.textContent = formatElapsed(Date.now() - state.progressStart);
  }

  function startProgress() {
    if (state.progressTimer) {
      clearInterval(state.progressTimer);
    }
    state.progressStart = Date.now();
    els.progressLabel.textContent = "正在提取音频…";
    els.progressElapsed.textContent = "0s";
    els.extractProgress.hidden = false;
    els.extractCard.classList.add("is-busy");
    state.progressTimer = setInterval(updateProgressElapsed, 1000);
  }

  function stopProgress() {
    if (state.progressTimer) {
      clearInterval(state.progressTimer);
      state.progressTimer = null;
    }
    state.progressStart = 0;
    els.extractCard.classList.remove("is-busy");
    els.extractProgress.hidden = true;
  }

  function setExtracting(busy) {
    state.extracting = busy;
    els.extractBtn.disabled = busy;
    els.extractBtn.classList.toggle("loading", busy);
    els.extractLabel.textContent = busy ? "正在提取…" : "提取音频";
    if (busy) {
      startProgress();
    } else {
      stopProgress();
    }
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

  function hideSuccess() {
    els.successActions.hidden = true;
  }

  function extractAudio() {
    if (state.extracting) {
      return;
    }
    var text = els.shareText.value.trim();
    if (!text) {
      hideSuccess();
      setNotice("error", "请先粘贴分享文案或链接。");
      els.shareText.focus();
      return;
    }

    hideNotice();
    hideSuccess();
    setExtracting(true);

    L.api("/api/extract", {
      method: "POST",
      body: JSON.stringify({ share_text: text }),
    })
      .then(function (payload) {
        var item = payload && payload.data;
        var name = item && item.title ? "《" + item.title + "》" : "音频";
        setExtracting(false);
        if (payload && payload.duplicate) {
          setNotice("success", name + "已经在音乐库中，可以直接播放。");
        } else {
          setNotice("success", name + "已保存到音乐库。");
        }
        els.shareText.value = "";
        els.successActions.hidden = false;
        if (els.successLink && item && item.id) {
          els.successLink.setAttribute("href", "/library");
        }
      })
      .catch(function (error) {
        setExtracting(false);
        setNotice("error", error.message || "提取失败，请稍后重试。");
      });
  }

  els.extractBtn.addEventListener("click", extractAudio);
  els.shareText.addEventListener("input", function () {
    if (!els.successActions.hidden) {
      hideSuccess();
      hideNotice();
    }
  });
  document.addEventListener("keydown", function (event) {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      extractAudio();
    }
  });
})();
