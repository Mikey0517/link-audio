# 链接音频库（Link Audio）

把分享文案/短链接中的音频转成 MP3，保存在自己的服务器磁盘和 SQLite 中，并提供一个适合手机、车机浏览器触摸操作的单页播放库。

项目采用 **provider（来源平台）架构**：抖音是当前实现的第一个 provider，后续可以增加小红书、B 站、YouTube 等来源，而不用改动存储、Range 播放、前端播放器和删除逻辑。

> 仅供个人学习与自用。不实现批量爬取、不绕过登录/付费/风控，不提供商业分发能力。请遵守各平台的服务条款和所在地法律，只处理你有权保存的内容。

---

## 功能

- 粘贴**整段分享文案**即可提取，由 provider 注册表识别来源并提取链接。
- 当前内置抖音 provider：只允许 `douyin.com`、`v.douyin.com`、`iesdouyin.com` 及其子域名，手动跟随跳转并逐跳校验，降低 SSRF 风险。
- 使用移动端 User-Agent 请求抖音分享页，解析 `window._ROUTER_DATA`、`RENDER_DATA`、`__INITIAL_STATE__` 等内嵌 JSON，并提供公开接口/备用分享页兜底。
- 平台无关的存储、下载、FFmpeg、Range 流式播放、前端播放器逻辑已经独立于抖音；数据库记录 `platform` 字段。
- 自动区分**图文作品**和**视频作品**：
  - 图文：优先直接下载 `music.play_url.url_list` 的 MP3，正常路径不调用 FFmpeg。
  - 视频：优先尝试音乐直链；若失败、不是有效音频，或时长比较发现它不是完整混音（过短/过长），自动回退到视频抽轨。
  - 视频抽轨优先让 FFmpeg 直读视频直链；失败后下载视频到临时目录再本地转码。
- 所有结果统一保存成真实 `.mp3`，数据库记录标题、作者、时长、大小、来源类型和状态。
- `/audio/{id}` 完整支持 HTTP Range：
  - `206 Partial Content`
  - `Content-Range: bytes start-end/total`
  - `Accept-Ranges: bytes`
  - `HEAD` 与 `GET` 行为一致（含 Range 时 HEAD 也返回 206 头）
  - 64 KB 分块流式读取，不把整首歌读进内存。
- 原生 HTML/CSS/JS 前端，无前端框架：
  - 桌面左右布局：左侧提取，右侧播放器 + 播放列表；窄屏自动堆叠；
  - 右侧播放器包含封面、当前曲目、播放/暂停、下一首、播放全部和播放列表；
  - 提取期间显示不确定进度条和实时耗时（后端是单次请求，不是伪造百分比）；
  - 大按钮、大字体、适合触控；
  - 原生 `<audio controls>`，车机拖动进度条依赖服务端 Range；
  - 列表点击切换、播放/暂停、删除、自动下一首、Media Session 车机媒体键支持。
- SQLite + 磁盘持久化，支持 Docker Compose；同一条音频按 `(platform, media_id)` 去重。
- 下载大小上限、并发提取限制、队列超时、所有外部请求超时、结构化日志。

---

## 目录结构

```text
link-audio/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 应用、API、Range 流式响应
│   ├── config.py            # 环境变量配置
│   ├── db.py                # SQLite 初始化、迁移与 CRUD
│   ├── net.py               # 通用 URL 校验、DNS 公网地址检查
│   ├── providers/
│   │   ├── base.py          # BaseProvider / ParsedMedia 契约
│   │   ├── douyin.py        # 抖音 provider：链接、页面、JSON 解析
│   │   ├── registry.py      # provider 注册、链接分发
│   │   └── __init__.py
│   ├── downloader.py        # httpx 流式下载、大小/内容/跳转校验
│   ├── ffmpeg.py            # FFmpeg / ffprobe 子进程封装
│   ├── audio_service.py     # 图文/视频处理策略、回退、落盘、写库
│   ├── errors.py            # 统一业务错误
│   ├── models.py            # Pydantic 请求/响应模型
│   ├── serializers.py       # 数据库记录 -> 前端 JSON
│   ├── storage.py           # MUSIC_DIR 路径安全解析
│   ├── logging_conf.py      # 日志初始化
│   └── static/
│       ├── index.html
│       ├── style.css
│       └── app.js
├── tests/
│   ├── test_api.py          # Range / HEAD / 删除 / 校验测试
│   ├── test_db.py           # platform 字段、旧库迁移、去重测试
│   ├── test_douyin.py       # 分享链接与页面 JSON 解析测试
│   ├── test_douyin_provider.py # 短链跳转 / iteminfo 兜底测试
│   ├── test_downloader.py   # 流式下载 / 大小 / HTML 校验测试
│   ├── test_registry.py     # provider 分发测试
│   └── test_service.py      # 图文直链、视频回退策略测试
├── data/                    # SQLite（运行时生成，.gitkeep 占位）
├── music/                   # MP3（运行时生成，.gitkeep 占位）
│   └── .tmp/                # 下载/转码临时文件（自动清理）
├── requirements.txt
├── requirements-dev.txt
├── pytest.ini
├── pyproject.toml           # Ruff 配置
├── .env.example
├── .gitignore
├── .dockerignore
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 处理流程

```text
分享文案
  │
  ├─ ProviderRegistry 按顺序询问 provider，拿到第一个支持的链接
  ├─ 当前抖音 provider：域名白名单 + 协议/端口/凭据校验
  ├─ 通用 RemoteURLValidator：DNS 公网地址校验
  ├─ 移动端 UA 请求，手动跟随最多 9 次跳转，每跳重新校验
  ├─ 抓取移动分享页 HTML
  ├─ 解析 _ROUTER_DATA / RENDER_DATA / __INITIAL_STATE__ ...
  │    └─ 失败时尝试 iteminfo 公开接口、iesdouyin 备用分享页
  ├─ 找到目标 media_id 对应的 item（不会误用“相关推荐”）
  ├─ 判断图文 / 视频
  │
  ├─ 图文：music.play_url.url_list → 下载 → 校验 MP3 → 落盘
  │        （正常路径不调用 FFmpeg；若直链实际是 AAC/M4A 才转码）
  │
  └─ 视频：先试音乐直链
           ├─ 下载成功 + ffprobe/ffmpeg 时长与视频接近 → 直接采用
           └─ 否则 FFmpeg 从 video.play_addr.url_list 抽轨
                ├─ 直接读取视频 URL（带 Referer/UA）
                └─ 失败则下载视频到临时目录，本地 FFmpeg 转码
  │
  ├─ 临时文件原子改名为 music/{media_id}_{uuid}.mp3
  ├─ 写入 SQLite（platform + completed / failed）
  └─ GET /audio/{id} 支持 Range 流式播放
```

### 为什么视频不总是直接使用音乐直链？

抖音的 `music.play_url` 在部分作品里是完整的原声混音，在另一些作品里只是背景音乐片段或整首歌。代码用 `ffprobe`（缺失时自动回退 `ffmpeg -i`）比较音频时长与视频时长：

- 音频明显短于视频 → 大概率只是 BGM，回退视频抽轨；
- 音频明显长于视频 → 大概率是完整歌曲而非作品混音，回退视频抽轨；
- ffprobe 和 ffmpeg 都无法探测音频时长 → 回退视频抽轨；
- 时长接近 → 采用音乐直链，避免不必要的视频下载。

这是启发式判断，不能保证 100% 正确，但比“无条件使用 music 直链”更符合“完整音轨”的目标。

### 数据表

`audios` 表包含：

```text
id, platform, aweme_id, title, author, source_url, file_path, file_size,
duration_seconds, created_at, status, error_message,
media_type, source_kind, updated_at
```

- `platform`: 来源平台，目前为 `douyin`，后续 provider 写入自己的名称。
- `aweme_id`: 保留原名以兼容旧库和原有验收项，实际语义是“平台侧媒体 ID”；新代码内部使用 `external_id/media_id`。
- 首次启动会自动为旧版抖音库补充 `platform` 列（默认 `douyin`），并创建 `(platform, aweme_id)` 索引。

- `status`: `processing` / `completed` / `failed`
- `media_type`: `image` / `video`
- `source_kind`: `music` / `music_transcoded` / `video_ffmpeg`
- 失败任务会留下 `failed` 记录和错误信息，便于排查；`/api/audios` 只返回 `completed`。
- 服务启动时会把上次进程遗留的 `processing` 记录标记为失败，避免永久卡住。
- 同一 `(platform, aweme_id)` 已有 `completed` 且文件存在时，会直接复用，不会重复下载。

### 扩展一个新平台

新增 provider 只需要实现 [app/providers/base.py](app/providers/base.py) 里的契约：

```python
from app.providers.base import BaseProvider, ParsedMedia


class ExampleProvider(BaseProvider):
    name = "example"
    display_name = "示例平台"

    def extract_url(self, share_text: str) -> str | None:
        # 从整段文案中找出自己的链接；找不到返回 None
        ...

    def matches(self, url: str) -> bool:
        # 纯本地判断域名/路径，不做网络请求
        ...

    async def parse(self, url: str) -> ParsedMedia:
        # 抓取页面/API，返回平台无关的 ParsedMedia
        return ParsedMedia(
            platform=self.name,
            external_id="...",
            title="...",
            author="...",
            source_url=url,
            final_url=url,
            media_type="video",          # image | video
            music_urls=(),
            video_urls=("https://...",), # 交给通用下载/FFmpeg 层
            duration_seconds=None,
            referer="https://example.com/",
            origin="https://example.com",
        )
```

然后在创建应用时注册：

```python
providers = ProviderRegistry([
    DouyinProvider(settings, client, validator),
    ExampleProvider(...),
])
```

存储、`source_kind`、Range 播放、删除、自动下一首和前端播放器不需要修改；前端会通过 `platform` 字段显示平台标签。新增平台若需要自己的请求头/Referer，可以像 `DOUYIN_REFERER` 一样增加 provider 专用配置，并在 `ParsedMedia.referer/origin` 中返回。

### 从旧版 `douyin-audio` 升级

目录名变更不影响数据。保留原来的 `music/` 和 `data/` 即可：

```bash
mv douyin-audio link-audio
cd link-audio
docker rm -f douyin-audio    # 如果旧容器仍在运行
docker compose up -d --build
```

首次启动会自动给旧 SQLite 表补充 `platform` 列（默认 `douyin`），已有记录不会丢失。旧浏览器里的“自动下一首”设置也会迁移到新键名。

---

## 本地运行

### 1. 环境要求

- Python 3.11+（推荐 3.11）
- FFmpeg（建议同时安装 ffprobe；部分静态构建只有 ffmpeg 也能工作）
  - 图文 MP3 直链路径不依赖 FFmpeg；
  - 视频抽轨、音视频时长探测、极少数非 MP3 音乐直链转码必须有 FFmpeg。

Ubuntu / Debian / WSL：

```bash
sudo apt update
sudo apt install -y ffmpeg
ffmpeg -version
ffprobe -version   # 可选；缺失时会用 ffmpeg -i 探测时长
# 确认含 MP3 编码器（输出中应出现 libmp3lame）
ffmpeg -hide_banner -encoders 2>/dev/null | grep libmp3lame
```

macOS：

```bash
brew install ffmpeg
```

Windows：

```powershell
winget install Gyan.FFmpeg
# 或从 https://www.gyan.dev/ffmpeg/builds/ 下载后把 bin 目录加入 PATH
ffmpeg -version
```

如果 FFmpeg 不在 PATH，可在 `.env` 设置：

```dotenv
FFMPEG_BIN=/absolute/path/to/ffmpeg
FFPROBE_BIN=/absolute/path/to/ffprobe
```

### 2. 安装 Python 依赖

```bash
cd link-audio
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Ubuntu 如果提示 `ensurepip is not available`：

```bash
sudo apt install -y python3-venv
```

### 3. 配置

```bash
cp .env.example .env
# 按需编辑 .env
```

默认：

- 音频目录：`./music`
- SQLite：`./data/music.db`

应用启动时会自动创建目录和数据库。

### 4. 启动

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

开发时自动重载：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

打开：

- 页面：<http://localhost:8000/>
- API 文档：<http://localhost:8000/docs>
- 健康检查：<http://localhost:8000/health>

### 5. 车机/手机访问

服务必须绑定 `0.0.0.0`，车机和服务器在同一个局域网：

```bash
hostname -I          # Linux / WSL，找到局域网 IP
```

在车机浏览器打开：

```text
http://<服务器局域网IP>:8000/
```

如果无法访问，依次检查：

1. 服务是否绑定 `0.0.0.0`，而不是 `127.0.0.1`；
2. 防火墙是否放行 8000（例如 `sudo ufw allow 8000/tcp`，只建议在可信局域网）；
3. 手机/车机是否连接同一个 Wi-Fi；
4. WSL2 默认 NAT 网络可能无法被局域网直接访问，推荐用 Docker Desktop 的端口映射，或在 Windows 上配置端口转发/镜像网络。

> 本项目没有登录认证，**不要直接暴露到公网**。需要远程访问时，请放在 Nginx/Caddy 后面并加 HTTPS + Basic Auth，或使用 Tailscale 等私有网络。

---

## Docker 运行

镜像内已安装 FFmpeg。

```bash
cd link-audio
cp .env.example .env

# 建议先创建目录，保证宿主机用户可写
mkdir -p music data
sudo chown -R "$(id -u):$(id -g)" music data

docker compose up -d --build
docker compose logs -f --tail=100
```

打开 <http://localhost:8000/>。

查看状态：

```bash
docker compose ps
curl -s http://localhost:8000/health
```

停止 / 重启 / 更新：

```bash
docker compose stop
docker compose restart
docker compose up -d --build
```

数据保存在宿主机的：

```text
./music/     MP3 文件
./data/      music.db（以及 SQLite WAL/SHM 文件）
```

### 容器用户与权限

`docker-compose.yml` 默认以宿主机 UID/GID `1000:1000` 运行，并把 `./music`、`./data` 挂载进去。若你的用户不是 1000，请在 `.env` 中设置：

```dotenv
PUID=你的uid
PGID=你的gid
```

查看：

```bash
id -u
id -g
```

若已经出现权限错误：

```bash
sudo chown -R "$(id -u):$(id -g)" music data
docker compose restart
```

### 为什么只跑一个 worker？

并发提取限制使用进程内 `asyncio.Semaphore`，SQLite 也适合单进程写入。`Dockerfile` 固定 `--workers 1`。如果你要横向扩容，需要把并发控制/数据库迁移到共享组件，否则每个 worker 都会有自己的并发上限。

---

## 环境变量

`.env` 由 pydantic-settings 自动读取。Docker Compose 也会用 `.env` 做变量插值。

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MUSIC_DIR` | `./music` | MP3 存储目录；Docker 内为 `/app/music` |
| `DB_PATH` | `./data/music.db` | SQLite 文件；Docker 内为 `/app/data/music.db` |
| `MAX_DOWNLOAD_MB` | `500` | 单个下载/转码输出上限（MB） |
| `MAX_CONCURRENT_EXTRACTIONS` | `2` | 单进程同时提取任务数 |
| `QUEUE_TIMEOUT_SECONDS` | `45` | 并发满时排队超时，超时返回 429 |
| `PAGE_TIMEOUT_SECONDS` | `20` | 请求分享页 HTML 的超时 |
| `DOWNLOAD_TIMEOUT_SECONDS` | `300` | 下载音频/视频的超时 |
| `FFMPEG_TIMEOUT_SECONDS` | `600` | FFmpeg 子进程超时 |
| `MAX_PAGE_BYTES` | `8388608` | 页面 HTML 最大读取体积 |
| `SSRF_DNS_CHECK` | `true` | 对页面/媒体域名做公网 IP 校验；关闭后仍保留各 provider 的域名白名单 |
| `USER_AGENT` | 移动端 Safari UA | 请求分享页面/媒体的 UA |
| `DOUYIN_REFERER` | `https://www.douyin.com/` | 请求页面和媒体的 Referer |
| `FFMPEG_BIN` | `ffmpeg` | FFmpeg 命令或绝对路径 |
| `FFPROBE_BIN` | `ffprobe` | ffprobe 命令或绝对路径 |
| `LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR/CRITICAL` |
| `APP_PORT` | `8000` | Docker Compose 宿主机端口 |
| `PUID` / `PGID` | `1000` | Docker Compose 运行用户 |
| `TZ` | `Asia/Shanghai` | 容器时区 |

> `SSRF_DNS_CHECK=false` 只建议在特殊代理/DNS 环境中用来排查问题。页面请求仍只允许当前 provider 的白名单域名，媒体 URL 也必须是 http/https 且不能带认证信息。

---

## API

错误统一格式：

```json
{
  "detail": {
    "code": "NO_AUDIO",
    "message": "未找到可用的音频或视频地址，作品可能已删除或设为私密"
  }
}
```

可能出现的业务错误码：

`INVALID_URL`、`UNSUPPORTED_DOMAIN`、`UNSAFE_URL`、`FETCH_FAILED`、`PARSE_FAILED`、`NO_AUDIO`、`DOWNLOAD_FAILED`、`FILE_TOO_LARGE`、`FFMPEG_FAILED`、`FFMPEG_MISSING`、`INTERNAL_ERROR`。

### POST `/api/extract`

请求：

```json
{
  "share_text": "8.25 复制打开抖音，看看【xxx的作品】…… https://v.douyin.com/xxxxxx/ ……"
}
```

响应（成功，含重复复用）：

```json
{
  "success": true,
  "duplicate": false,
  "data": {
    "id": 1,
    "platform": "douyin",
    "aweme_id": "7345678901234567890",
    "title": "作品标题",
    "author": "作者昵称",
    "source_url": "https://v.douyin.com/xxxxxx/",
    "audio_url": "/audio/1",
    "file_size": 1234567,
    "duration_seconds": 15.2,
    "created_at": "2026-11-01T10:00:00+00:00",
    "status": "completed",
    "media_type": "video",
    "source_kind": "video_ffmpeg"
  }
}
```

`duplicate=true` 表示同一 `(platform, aweme_id)` 已有完成记录且文件存在，直接返回旧记录。

### GET `/api/audios`

```json
{
  "items": [ { "...": "同上" } ],
  "total": 1
}
```

只返回 `completed` 的记录，按创建时间倒序。

### GET / HEAD `/audio/{id}`

- `audio/mpeg`
- `Accept-Ranges: bytes`
- 无 Range：`200` + 完整 `Content-Length`
- 有合法 Range：`206` + `Content-Range` + 对应 `Content-Length`
- 越界/格式错误：`416` + `Content-Range: bytes */总大小`
- `HEAD` 不返回响应体，但状态码和头与 GET 一致。
- 单区间 Range 支持 `bytes=start-end`、`bytes=start-`、`bytes=-suffix`。多区间请求返回 416（播放器通常不会发送多区间）。

### DELETE `/api/audios/{id}`

删除磁盘文件与 SQLite 记录，成功：

```json
{ "success": true, "id": 1, "message": "已删除" }
```

### GET `/health`

```json
{
  "status": "ok",
  "version": "1.1.0",
  "database": "ok",
  "music_dir": "/app/music",
  "music_dir_writable": true,
  "ffmpeg_available": true,
  "ffmpeg_path": "/usr/bin/ffmpeg",
  "ffprobe_available": true,
  "active_extractions": 0,
  "max_concurrent_extractions": 2,
  "providers": ["douyin"]
}
```

FFmpeg 缺失时 `status` 为 `degraded`，HTTP 仍返回 200（图文直链仍可工作）。Docker 健康检查只判断进程是否可响应。

### curl 示例

```bash
# 提取
curl -sS -X POST http://localhost:8000/api/extract \
  -H 'Content-Type: application/json' \
  -d '{"share_text":"复制打开抖音 https://v.douyin.com/xxxxxx/"}'

# 列表
curl -sS http://localhost:8000/api/audios

# 删除
curl -sS -X DELETE http://localhost:8000/api/audios/1
```

---

## Range 请求验证（重点）

假设已有 `id=1` 的音频。

### 1. 完整请求应为 200

```bash
curl -sS -D - -o /dev/null http://localhost:8000/audio/1
```

重点看：

```text
HTTP/1.1 200 OK
content-type: audio/mpeg
accept-ranges: bytes
content-length: <完整大小>
```

### 2. 带 Range 的 GET 应为 206

```bash
curl -sS -D - -o /dev/null \
  -H "Range: bytes=0-1023" \
  http://localhost:8000/audio/1
```

期望：

```text
HTTP/1.1 206 Partial Content
content-type: audio/mpeg
accept-ranges: bytes
content-range: bytes 0-1023/<总大小>
content-length: 1024
```

### 3. 带 Range 的 HEAD 也应为 206

```bash
curl -sS -I \
  -H "Range: bytes=0-1023" \
  http://localhost:8000/audio/1
```

期望同样是 `206`、`Content-Range`、`Accept-Ranges`、`Content-Length: 1024`，但没有响应体。

### 4. 只取最后 1 KB

```bash
curl -sS -D - -o /dev/null \
  -H "Range: bytes=-1024" \
  http://localhost:8000/audio/1
```

### 5. 越界应 416

```bash
curl -sS -D - -o /dev/null \
  -H "Range: bytes=999999999-" \
  http://localhost:8000/audio/1
```

期望：

```text
HTTP/1.1 416 Range Not Satisfiable
content-range: bytes */<总大小>
```

### 6. 用状态码快速判断

```bash
curl -s -o /dev/null -w 'no-range=%{http_code}\n' http://localhost:8000/audio/1
curl -s -o /dev/null -w 'range=%{http_code}\n' \
  -H 'Range: bytes=0-1023' http://localhost:8000/audio/1
```

应输出：

```text
no-range=200
range=206
```

也可以在浏览器 DevTools 的 Network 面板播放音频并拖动进度条，应看到 `/audio/1` 请求返回 `206`。如果一直只有 `200`，车机拖动进度条通常会失效。

---

## 测试

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q

# 可选：代码检查
ruff check app tests
ruff format --check app tests
```

当前测试套件共 37 项，覆盖：

- 分享文案中的链接提取、非抖音域名拒绝、裸域名补全；
- provider 注册/分发、旧库 `platform` 字段迁移、同 ID 跨平台隔离；
- 手动跟随短链、页面 JSON 网络层、iteminfo 兜底；
- 流式下载、跳转、HTML 拦截、大小限制；
- `_ROUTER_DATA`、`RENDER_DATA`、目标 `aweme_id` 选择、图文/视频分类；
- 图文直链不调用 FFmpeg；
- 视频音乐直链过短时回退 FFmpeg；
- 视频音乐直链完整时直接采用；
- 音乐下载失败、图文音乐失败时回退视频；
- `200/206/416`、HEAD、Content-Range、删除文件和数据库记录、参数校验。

测试使用临时目录、假 provider/假下载器/假 FFmpeg，不访问真实平台。

---

## 常见问题

### 1. 页面提示 `FFMPEG_MISSING`

服务器没有安装 FFmpeg，或 `FFMPEG_BIN` 路径错误。视频作品需要它；图文直链通常不需要。安装后重启服务：

```bash
ffmpeg -version
ffprobe -version   # 可选
```

Docker 镜像已经内置，若仍提示，检查是否用了旧的镜像，重新 `docker compose build --no-cache`。

### 2. `FETCH_FAILED` / 403 / 风控验证页

抖音可能对服务器 IP、请求频率或异常网络触发风控。可以：

- 稍后重试；
- 确认链接在手机浏览器中仍可打开；
- 换用家庭宽带服务器，避免公共云 IP；
- 查看服务端日志中的最终 URL 和状态码。

这不代表可以绕过验证码或登录，本项目不会实现绕过。

### 3. `PARSE_FAILED` / “页面结构可能已变化”

抖音前端结构会变化。解析器已经做了多种内嵌 JSON 格式和公开接口兜底，但仍可能需要更新 `app/providers/douyin.py`。日志会记录：

- 短链最终地址；
- 目标 `aweme_id`；
- 是否拿到页面 JSON；
- 是否尝试了 iteminfo / 备用分享页。

如果日志显示页面 JSON 只有 `loaderData` / 页面上下文，而没有 `item_list`、`videoInfoRes` 或 `aweme_detail`，通常说明新版移动分享页把作品数据改成了浏览器端接口请求。本项目按需求保持轻量，不默认引入 headless 浏览器或第三方签名服务；此时需要根据新的公开页面结构更新 `app/providers/douyin.py`。

把一段可复现的 HTML 保存到 `tests/` 作为 fixture，是后续修复最稳妥的方式。

### 4. `NO_AUDIO` / 未找到音频

常见原因：

- 作品已删除、私密、仅好友可见、地区限制；
- 页面只返回了图片信息，没有 `music.play_url`；
- 视频直链全部失效；
- 页面结构变化。

可以先用手机确认作品仍可播放。项目不实现登录态/私密作品解析。

### 5. 图文作品为什么也调用了 FFmpeg？

正常 MP3 直链不会。只有两种情况：

1. 图文没有可用音乐直链，代码退而使用视频地址抽轨；
2. 音乐直链返回的其实是 AAC/M4A 等格式，为保证落盘文件是真正 `.mp3`，才做转码。

日志中 `source_kind=music` 表示纯直接下载；`music_transcoded` 表示做了转码。

### 6. 车机拖不动进度条

按上面的 Range 验证步骤检查：

```bash
curl -s -o /dev/null -w '%{http_code}\n' \
  -H 'Range: bytes=0-1023' http://localhost:8000/audio/1
```

如果返回 206 但车机仍不能拖动，检查中间是否有反向代理/CDN 吃掉了 Range 或做了缓冲。Nginx 至少需要：

```nginx
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header Range $http_range;
proxy_set_header If-Range $http_if_range;
proxy_buffering off;
```

并确保代理没有把响应缓存成固定 200 全量。

### 7. 局域网/车机打不开页面

- 服务绑定 `0.0.0.0`；
- 使用服务器局域网 IP，不是 `localhost`；
- 关闭或放行防火墙；
- WSL2 需要端口映射或改用 Docker Desktop；
- 车机浏览器可能只支持 HTTP，若前面加了 HTTPS 自签证书可能被拒绝。

### 8. Docker 启动后权限错误 / 无法写 music

宿主机目录所有者与容器用户不一致。执行：

```bash
sudo chown -R "$(id -u):$(id -g)" music data
```

并在 `.env` 设置正确的 `PUID` / `PGID`。

### 9. 音乐直链下载成功但视频仍回退 FFmpeg

这是有意设计。视频作品会检查音乐时长是否接近视频时长；如果 `music.play_url` 只是 BGM 片段，或时长无法探测/明显不匹配，就回退视频抽轨。要接受音乐直链，需要能探测出与视频接近的时长。

### 10. 想清理失败记录

`/api/audios` 不显示失败记录。需要手动查看：

```bash
sqlite3 data/music.db "SELECT id, platform, aweme_id, status, error_message FROM audios WHERE status='failed';"
```

不要在服务运行时用其他进程长时间锁库；SQLite 已开启 WAL 和 busy timeout，但个人使用仍建议单实例。

### 11. 文件名/中文乱码

磁盘文件名只使用 `media_id + 随机十六进制`，不含中文。标题、作者作为 UTF-8 元数据保存在 SQLite，并显示在前端。MP3 内的 ID3 标签仅由 FFmpeg 转码路径写入。

---

## 安全说明与限制

已实现：

- 只允许当前 provider 的域名白名单作为入口，逐跳校验重定向；
- DNS 解析到私网/回环/链路本地等地址时拒绝（`SSRF_DNS_CHECK=true`）；
- 媒体 URL 只接受 http/https、标准端口、无认证信息；
- 最大 500 MB 下载/输出限制；
- 并发提取限制与队列超时；
- 所有外部请求设置超时；
- 临时文件失败清理；
- 数据库路径使用 ID 查询，文件名只允许 `MUSIC_DIR` 直接子项，防止路径穿越；
- 日志不打印签名 URL 的 query，只打印 scheme/host/path；
- 前端不使用 `innerHTML` 拼接标题，避免 XSS。

限制：

- 没有登录/多用户隔离，**只适合个人可信局域网**；
- 不做批量抓取、不绕过登录/验证码/付费；
- 抖音页面和接口可能随时变化；新版移动分享页可能把作品数据改为客户端请求，若页面 HTML 只有 `_ROUTER_DATA` 上下文而没有 `item_list`，需要根据新接口更新解析器；
- 音乐直链是否为“完整混音”是启发式判断；
- 单进程并发限制，不适合多 worker 横向扩展；
- 不建议把 `music/` 目录直接暴露为静态文件，请始终通过 `/audio/{id}` 访问；
- 仅处理你有权保存的内容。

---

## 数据备份与迁移

停止服务后备份：

```bash
tar czf link-audio-backup.tar.gz music data .env
```

或只备份音频和数据库：

```bash
sqlite3 data/music.db ".backup 'data/music-backup.db'"
cp -a music /path/to/backup/
```

恢复时保持 `music/` 与 `music.db` 中的文件名对应即可。数据库存的是文件名（例如 `7345..._a1b2c3.mp3`），不是绝对路径。

---

## 本地开发提示

- 改后端后 Uvicorn 自动重载，或手动重启。
- 改前端后刷新浏览器即可，静态文件不做构建。
- 如果要用真实链接做端到端测试，先在日志确认 `解析成功` 与 `type=image/video`，再确认 `source_kind=music/music_transcoded/video_ffmpeg`。
- 不建议把真实分享链接作为单元测试数据，链接会过期；页面 JSON 应保存为离线 fixture。

---

## License / 使用边界

本项目仅作为个人自用的技术示例。请自行确认内容版权、平台条款和当地法律；不要用它做批量采集、商业分发或绕过访问控制。
