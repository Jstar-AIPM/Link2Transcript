# 逐字稿提取器

把本地音视频文件或 B 站视频链接，转成可预览、可下载的逐字稿。

| 输入 | 支持 |
| --- | --- |
| 本地音频 | MP3、M4A、WAV |
| 本地视频 | MP4、MOV |
| 链接 | B 站视频链接（`BV` 号、`av` 号、`b23.tv` 短链，单个分 P） |
| 输出 | Markdown、TXT、结构化 JSON |

当前不包含抖音 / 小红书 / YouTube、正式前端、用户系统、LLM 清洗或云端部署。

## 工作流程

```text
本地文件
→ 校验扩展名、大小和真实媒体结构
→ 视频使用 FFmpeg 提取 16 kHz 单声道音频
→ faster-whisper 转写
→ 生成 Markdown、TXT 和结构化结果

B 站链接
→ URL 白名单校验（非 B 站直接拒绝）
→ yt-dlp 解析视频信息，并检查字幕
→ 有可用字幕：按「人工字幕 > AI 字幕」提取并标准化（不调用语音识别）
→ 无可用字幕：只下载音频轨 → FFmpeg 统一格式 → faster-whisper 转写
→ 复用同一套导出结构
```

任务通过 UUID 管理，每个任务使用独立 JSON 文件持久化，采用原子替换写入。服务重启后，已完成或失败的任务仍可查询；重启时尚未完成的任务会明确标记为 `TASK_INTERRUPTED`，用户可以重新提交，不会显示为成功。

任务记录带 `schema_version`。当前版本为 2；读取旧版本（v1）记录时缺失字段使用默认值补齐，再次写入时自动升级为 v2，**历史任务不会失效**。

验收页面会显示任务已用时间、媒体总时长和预计剩余时间，并持续提示任务仍在运行。当前任务 ID 保存在浏览器本地存储中，刷新页面后会自动恢复查询；短暂网络中断会自动重试。

## 环境要求

- macOS（当前开发环境）
- Python 3.11.x
- 项目依赖中自带的 FFmpeg 可执行文件；无需修改系统级 Homebrew 环境
- 首次真实转写时可访问 Hugging Face，以下载配置的 Whisper 模型
- 链接链路需要能访问 B 站

媒体内容检查使用 PyAV（FFmpeg 库绑定），视频音轨提取与音频格式统一使用项目锁定的 FFmpeg 二进制，因此不依赖用户机器上是否恰好安装了系统级 FFmpeg。链接解析使用 `yt-dlp`（版本已精确锁定在 `pyproject.toml`）。

## 安装

项目使用 `uv.lock` 固定依赖：

```bash
uv sync --python 3.11 --extra dev
cp .env.example .env
```

默认配置适合 CPU 验证：

```env
WHISPER_MODEL=small
WHISPER_DEVICE=cpu
WHISPER_COMPUTE_TYPE=int8
MAX_UPLOAD_MB=2048
MAX_MEDIA_MINUTES=180
MAX_DOWNLOAD_MB=1024
TASK_MAX_WORKERS=1
```

第一阶段起就明确采用单服务进程、单任务执行线程。启动 Uvicorn 时不要增加 `--workers`；多个服务进程共享同一 `data/` 目录不在当前支持范围内。

第一次验证或设备性能有限时，可以暂时把 `WHISPER_MODEL` 改为 `tiny`；正式验收应记录实际使用的模型和效果。

### B 站字幕需要登录态

**这一点很关键**：B 站的字幕接口要求登录才能返回字幕内容。未登录时 `yt-dlp` 只会返回弹幕（`danmaku`），并且会输出提示 `Subtitles are only available when logged in`。

我们**不会**把弹幕当字幕使用。所以：

- **未配置 Cookie**：B 站链接一律走语音转写（功能正常，只是更慢）；
- **配置 Cookie 后**：有字幕的视频会走字幕提取，速度快很多。

配置方式（可选，但这是启用字幕路径的实际情况）：

```env
BILIBILI_COOKIE=SESSDATA=xxxxxx; bili_jct=xxxxxx
```

从浏览器开发者工具中复制 Cookie 请求头里的字段即可。该值只从后端环境变量读取，不会进入前端、日志或 Git。Cookie 会过期，失效后自动降级为语音转写，不影响任务成功。

## 启动

```bash
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：<http://127.0.0.1:8000>

API 文档：<http://127.0.0.1:8000/docs>

服务启动时会检查数据目录写入权限、FFmpeg 可执行文件、faster-whisper 运行依赖以及 `yt-dlp` 是否可用。模型权重采用延迟加载：第一次转写时才从本地缓存读取或下载，避免每次启动都加载数百 MB 模型。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/tasks` | 上传一个文件并创建任务 |
| `POST` | `/api/v1/tasks/from-url` | 提交一个 B 站链接并创建任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 查询任务状态 |
| `GET` | `/api/v1/tasks/{task_id}/result` | 获取结构化逐字稿 |
| `GET` | `/api/v1/tasks/{task_id}/download/markdown` | 下载 Markdown |
| `GET` | `/api/v1/tasks/{task_id}/download/txt` | 下载 TXT |

提交链接：

```bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks/from-url \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://www.bilibili.com/video/BV..."}'
```

任务状态：

```text
本地音频：pending → validating → transcribing → exporting → succeeded
本地视频：pending → validating → extracting_audio → transcribing → exporting → succeeded
B站有字幕：pending → checking_subtitle → exporting → succeeded
B站无字幕：pending → checking_subtitle → downloading_audio → transcribing → exporting → succeeded
任何处理中状态 → failed
```

链接类错误在**创建任务之前**就会返回（非法链接不会留下失败记录）；时长、可用性、多 P 等需要联网判断的错误，会在 `checking_subtitle` 阶段让任务进入 `failed`，并给出中文说明。

## 测试

运行离线自动化测试（不访问网络）：

```bash
.venv/bin/pytest
```

测试覆盖：

- 支持及不支持的文件格式、上传大小限制；
- URL 白名单、短链跳转后的二次校验、非 B 站与非法链接拒绝；
- 多 P 拒绝、超长时长闸门、下载体积闸门与失败清理；
- 字幕优先级（人工 > AI）、弹幕排除、字幕解析失败降级；
- **有字幕时断言不调用语音识别**；
- 任务状态流转、JSON 持久化与重启恢复；
- 视频处理链路编排、音频提取 / 转写 / 导出失败路径；
- schema v1 历史记录可读、可下载、写入时升级为 v2；
- 所有用户可见错误文案均为中文且不含技术术语。

## 真实验收

### 本地文件

准备一段 1～5 分钟中文音频和视频，分别执行：

1. 上传文件并观察真实阶段状态；
2. 确认视频任务出现「正在提取音频」；
3. 确认最终逐字稿基本可读且没有乱码；
4. 分别下载 Markdown 和 TXT 并打开检查；
5. 记录文件格式、大小、时长、模型、转写耗时和明显错字；
6. 上传损坏文件，确认任务进入 `failed`，且页面不显示程序堆栈。

### B 站链接

1. 有字幕视频（需先配置 Cookie）：应显示「正在检查视频字幕」，处理方式为「字幕提取」，页面不出现下载音频阶段；
2. 无字幕视频：应显示「未发现可用字幕，正在下载音频」，处理方式为「语音转写」；
3. 无效链接 / 非 B 站链接：提交时立即出现中文提示，且不产生任务记录；
4. 多 P 链接：提示「当前只支持单个视频，请粘贴某一个分集（分 P）的链接」；
5. 超过 180 分钟的视频：在下载前就被拒绝。

## 长任务与已知限制

- **转写耗时与音频时长成正比**。CPU 上约为 3–4 倍实时：3 小时音频约需 45–60 分钟。这是本机算力的客观结果，不是卡死；页面会显示已用时间和预计剩余时间。
- 时长上限默认 180 分钟，在**下载之前**用元信息判断，因此超长视频不会浪费下载流量。
- 链接链路**只下载音频轨**，不下载视频。3 小时内容通常只有 100–250 MB。
- **画面内烧录的字幕（硬字幕）不支持**：那需要 OCR，与「字幕提取」不是同一能力。
- **多 P / 合集不支持**：一个任务只产出一份逐字稿。
- **中文识别可能夹杂繁体字或同音错字**：这是 Whisper 模型本身的限制，当前版本不做自动纠正。
- 分片转写、进度百分比、取消任务、转写过程实时呈现**不在当前版本范围内**，已记录在《后续路线图与待办清单》。

运行数据集中存放在 `data/`，不会提交到 Git。日志只记录任务 ID、阶段、错误类型和耗时，不记录完整音视频、逐字稿正文或 Cookie。

## 文件保留与清理

当前默认保留：

- `data/uploads/{task_id}/`：原始上传文件；
- `data/downloads/{task_id}/`：从链接下载的音频轨；
- `data/audio/{task_id}/`：统一格式后的中间音频；
- `data/outputs/{task_id}/`：逐字稿与结构化结果；
- `data/tasks/{task_id}.json`：任务状态。

**当前不自动删除任何文件**，避免调试和验收证据丢失。需要释放空间时，应先停止服务并确认对应结果已下载，再按一个明确的 `task_id` 同时清理上述五处数据；不要直接删除整个 `data/`。链接类任务失败时，已下载的音频会被自动清理。

进入上线阶段前必须增加可配置保留期限、自动清理和对象存储策略 —— 已记录在《后续路线图与待办清单》。

## 相关文档

- `开发文档/逐字稿提取器_V1_PRD.md`：产品需求与阶段划分；
- `开发文档/逐字稿提取器_技术适配声明.md`：技术选型与暂缓项；
- `开发文档/第一阶段技术开发文档_本地文件核心链路.md`：第一阶段设计与验收；
- `开发文档/第二阶段技术开发文档_B站链接链路.md`：第二阶段设计与验收；
- `开发文档/后续路线图与待办清单.md`：**所有后置项的集中登记处**；
- `skill/SKILL.md`：逐字稿提取工作流的说明层。
