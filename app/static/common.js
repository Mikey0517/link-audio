(function () {
  "use strict";

  var platformLabels = {
    douyin: "抖音",
    bilibili: "哔哩哔哩",
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

  function pad2(value) {
    return value < 10 ? "0" + value : String(value);
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

  function formatBytes(bytes) {
    if (typeof bytes !== "number" || bytes <= 0) {
      return "0 B";
    }
    if (bytes < 1024 * 1024) {
      return Math.round(bytes / 1024) + " KB";
    }
    return (bytes / (1024 * 1024)).toFixed(1) + " MB";
  }

  function platformName(platform) {
    if (!platform) {
      return "未知平台";
    }
    return platformLabels[platform] || platform;
  }

  function sourceLabel(item) {
    var source;
    if (item.platform === "bilibili") {
      source = item.source_kind === "video_ffmpeg" ? "视频音轨" : "视频音轨（转码）";
    } else if (item.source_kind === "video_ffmpeg") {
      source = "视频完整音轨";
    } else if (item.source_kind === "music_transcoded") {
      source = item.media_type === "image" ? "图文原声（转码）" : "原声（转码）";
    } else {
      source = item.media_type === "image" ? "图文原声" : "作品原声";
    }
    return platformName(item.platform) + " · " + source;
  }

  function showToast(message, type, duration) {
    var toast = document.getElementById("toast");
    if (!toast) {
      return;
    }
    if (toast._timer) {
      clearTimeout(toast._timer);
    }
    toast.hidden = false;
    toast.className = "toast " + (type || "info");
    toast.textContent = message;
    toast._timer = setTimeout(function () {
      toast.hidden = true;
    }, duration || 4200);
  }

  window.LinkAudio = {
    api: api,
    formatBytes: formatBytes,
    formatDate: formatDate,
    formatDuration: formatDuration,
    platformName: platformName,
    showToast: showToast,
    sourceLabel: sourceLabel,
  };
})();
