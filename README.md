# 逐字稿提取器

当前实现 PRD 的第一阶段：把本地音频或视频转换为可预览、可下载的逐字稿。

支持格式：

- 音频：MP3、M4A、WAV
- 视频：MP4、MOV
- 输出：Markdown、TXT

当前阶段不包含 B 站等链接解析、正式前端、用户系统、LLM 清洗或云端部署。

## 工作流程

```text
上传文件
→ 校验扩展名、大小和真实媒体流
→ 视频使用 FFmpeg 提取 16 kHz 单声道音频
→ faster-whisper 转写
→ 生成 Markdown、TXT 和结构化结果
→ 页面预览或下载
```

任务通过 UUID 管理，每个任务使用独立 JSON 文件持久化。任务记录采用原子替换写入。服务重启后，已完成或失败的任务仍可查询；重启时尚未完成的任务会明确标记为 `TASK_INTERRUPTED`，用户可以重新上传，不会显示为成功。

验收页面会显示任务已用时间和媒体总时长，并持续提示任务仍在运行。当前任务 ID 保存在浏览器本地存储中，刷新页面后会自动恢复查询；短暂网络中断会自动重试。

## 环境要求

- macOS（当前开发环境）
- Python 3.11.x
- 项目依赖中自带的 FFmpeg 可执行文件；无需修改系统级 Homebrew 环境
- 首次真实转写时可访问 Hugging Face，以下载配置的 Whisper 模型

媒体内容检查使用 PyAV（FFmpeg 库绑定），视频音轨提取使用项目锁定的 FFmpeg 二进制，因此不会依赖用户机器上是否恰好安装了系统级 FFmpeg。

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
MAX_UPLOAD_MB=500
TASK_MAX_WORKERS=1
```

第一阶段明确采用单服务进程、单任务执行线程。启动 Uvicorn 时不要增加 `--workers`；多个服务进程共享同一 `data/` 目录不在本阶段支持范围内。

第一次验证或设备性能有限时，可以暂时把 `WHISPER_MODEL` 改为 `tiny`；正式验收应记录实际使用的模型和效果。

## 启动

```bash
.venv/bin/uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：<http://127.0.0.1:8000>

API 文档：<http://127.0.0.1:8000/docs>

服务启动时会检查数据目录写入权限、FFmpeg 可执行文件以及 faster-whisper 运行依赖。模型权重采用延迟加载：第一次转写时才从本地缓存读取或下载，避免每次启动都加载数百 MB 模型。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/tasks` | 上传一个文件并创建任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 查询任务状态 |
| `GET` | `/api/v1/tasks/{task_id}/result` | 获取结构化逐字稿 |
| `GET` | `/api/v1/tasks/{task_id}/download/markdown` | 下载 Markdown |
| `GET` | `/api/v1/tasks/{task_id}/download/txt` | 下载 TXT |

任务状态：

```text
音频：pending → validating → transcribing → exporting → succeeded
视频：pending → validating → extracting_audio → transcribing → exporting → succeeded
任何处理中状态 → failed
```

## 测试

运行离线自动化测试：

```bash
.venv/bin/pytest
```

测试覆盖：

- 支持及不支持的文件格式；
- 上传大小限制；
- 合法与非法状态流转；
- 任务 JSON 持久化和重启恢复；
- 视频处理链路编排；
- FFmpeg 提取、Whisper 转写和文件导出失败路径；
- UTF-8 Markdown/TXT 导出；
- 统一错误响应。

## 真实验收

准备一段 1～5 分钟中文音频和视频，分别执行：

1. 上传文件并观察真实阶段状态；
2. 确认视频任务出现“正在提取音频”；
3. 确认最终逐字稿基本可读且没有乱码；
4. 分别下载 Markdown 和 TXT 并打开检查；
5. 记录文件格式、大小、时长、模型、转写耗时和明显错字；
6. 上传损坏文件，确认任务进入 `failed`，且页面不显示程序堆栈。

运行数据集中存放在 `data/`，不会提交到 Git。日志只记录任务 ID、阶段和错误类型，不记录完整音视频或逐字稿正文。

## 文件保留与清理

第一阶段用于本地单用户验收，默认保留：

- `data/uploads/{task_id}/`：原始上传文件；
- `data/audio/{task_id}/`：视频提取的中间音频；
- `data/outputs/{task_id}/`：逐字稿与结构化结果；
- `data/tasks/{task_id}.json`：任务状态。

当前不自动删除这些文件，避免调试和验收证据丢失。需要释放空间时，应先停止服务并确认对应结果已下载，再按一个明确的 `task_id` 同时清理上述四处数据；不要直接删除整个 `data/`。进入上线阶段前必须增加可配置保留期限、自动清理和对象存储策略。
