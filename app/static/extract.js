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
    progressPercent: document.getElementById("progressPercent"),
    progressElapsed: document.getElementById("progressElapsed"),
    progressFill: document.getElementById("progressFill"),
    notice: document.getElementById("notice"),
    successActions: document.getElementById("successActions"),
    successHeading: document.getElementById("successHeading"),
    successTitle: document.getElementById("successTitle"),
    successLink: document.querySelector("#successActions .success-btn"),
  };

  var state = {
    extracting: false,
    jobId: null,
    progressTimer: null,
    progressStart: 0,
    progressDone: false,
    polling: false,
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

  function newJobId() {
    try {
      if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
      }
    } catch (error) {
      /* fall through */
    }
    return (
      "job-" +
      Date.now().toString(36) +
      "-" +
      Math.random().toString(36).slice(2, 12)
    );
  }

  function setProgress(percent, stage) {
    var value = parseInt(percent, 10);
    if (!isFinite(value)) {
      value = 0;
    }
    value = Math.max(0, Math.min(100, value));
    els.progressFill.style.width = value + "%";
    els.progressPercent.textContent = value + "%";
    els.extractProgress.setAttribute("aria-valuenow", String(value));
    if (stage) {
      els.progressLabel.textContent = stage;
      els.extractProgress.setAttribute("aria-valuetext", value + "% " + stage);
    }
  }

  function updateElapsed() {
    if (!state.progressStart) {
      return;
    }
    els.progressElapsed.textContent = formatElapsed(Date.now() - state.progressStart);
  }

  function pollProgress() {
    if (!state.jobId || state.progressDone || state.polling) {
      return;
    }
    state.polling = true;
    L.api("/api/extract/progress/" + encodeURIComponent(state.jobId))
      .then(function (data) {
        if (!data || state.progressDone) {
          return;
        }
        setProgress(data.percent, data.stage);
        if (data.status === "completed") {
          setProgress(100, "完成");
        }
      })
      .catch(function () {
        /* the job may not be registered yet; keep the elapsed time running */
      })
      .then(function () {
        state.polling = false;
      });
  }

  function tickProgress() {
    updateElapsed();
    pollProgress();
  }

  function stopProgressTimer() {
    if (state.progressTimer) {
      clearInterval(state.progressTimer);
      state.progressTimer = null;
    }
  }

  function startProgress() {
    stopProgressTimer();
    state.progressDone = false;
    state.progressStart = Date.now();
    setProgress(0, "正在排队");
    els.progressElapsed.textContent = "0s";
    els.extractProgress.hidden = false;
    els.extractCard.classList.add("is-busy");
    state.progressTimer = setInterval(tickProgress, 700);
    pollProgress();
  }

  function stopProgress() {
    stopProgressTimer();
    state.progressDone = true;
    state.polling = false;
    state.progressStart = 0;
    state.jobId = null;
    els.extractCard.classList.remove("is-busy");
    els.extractProgress.hidden = true;
  }

  function finishProgress() {
    stopProgressTimer();
    state.progressDone = true;
    setProgress(100, "完成");
    els.extractBtn.classList.remove("loading");
    els.extractLabel.textContent = "完成";
    setTimeout(function () {
      setExtracting(false);
    }, 500);
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
    state.jobId = newJobId();
    setExtracting(true);

    L.api("/api/extract", {
      method: "POST",
      body: JSON.stringify({ share_text: text, job_id: state.jobId }),
    })
      .then(function (payload) {
        var item = payload && payload.data;
        var name = item && item.title ? "《" + item.title + "》" : "音频";
        finishProgress();
        els.successHeading.textContent = payload && payload.duplicate ? "已在音乐库中" : "已保存到音乐库";
        els.successTitle.textContent = name;
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
