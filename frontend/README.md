# 逐字稿提取器｜正式前端

Next.js（App Router）+ TypeScript（strict）+ Tailwind CSS 的单页工具界面。
后端是项目根目录的 FastAPI 服务，本前端**不修改后端接口**。

## 启动

```bash
# 1) 先启动后端（项目根目录）
cd ..
.venv/bin/uvicorn backend.app.main:app --port 8000

# 2) 再启动前端
cd frontend
npm install          # 首次
cp .env.example .env.local   # 首次（BACKEND_ORIGIN 默认已是本地后端）
npm run dev          # http://127.0.0.1:3100（端口固定为 3100，见下方说明）
```

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `BACKEND_ORIGIN` | 后端地址，**只在服务端使用**。Next 通过 `rewrites` 把浏览器发往同源 `/api/:path*` 的请求转发到这里，因此不需要 CORS，下载链接也不用改写。生产部署时改这一处即可。 |

## 质量命令

```bash
npm run typecheck   # tsc --noEmit
npm run lint        # eslint
npm run test        # vitest（含与真实后端响应的契约测试）
npm run build       # 生产构建
npm run verify      # 上面四个依次执行（提交前跑这个）
```

## 目录约定

```text
src/app/          路由与服务端外壳（页面尽量薄）
src/components/   纯展示与交互组件（不直接发请求）
src/lib/          接口客户端、运行时校验（zod）、格式化
src/styles/       设计变量（颜色的唯一来源）
tests/            组件与契约测试；fixtures 是真实后端响应样本
```

## 端口为什么是 3100

`next dev` 默认用 3000，但本机同目录下还有其它前端工程（例如
`个人项目/03 AI 用户洞察分析器/frontend`）也在用 3000。**真实踩过**：
我们自己的 dev server 退出后，另一个项目接走了 3000，
于是打开 `127.0.0.1:3000/tasks/...` 看到的是**别人家项目的 404 页面**
（提示「链接不存在」），会让人误以为链接失效。

因此 `package.json` 里把本前端的端口固定为 **3100**（dev 与 start 一致），
避免和同机其它项目互相抢占。

## 一个必须记住的约束（别再踩）

**上传不能走 `rewrites` 代理**：Next 的代理层会把请求体读进内存，默认上限 **10 MB**，
而本项目上传上限是 2048 MB（真实的 13 MB 文件就会 500）。
因此上传单独由 `src/app/api/v1/tasks/route.ts` **流式转发**（不缓冲、不占内存），
其余小体积 JSON 接口继续走 `next.config.ts` 的 rewrites。
`tests/upload-proxy.test.ts` 锁住了这个契约，改动前先看它。

## 设计基调

另外两个已踩过的坑也记在这里：

1. **访问不存在的任务**：后端返回 `TASK_NOT_FOUND` 时，页面显示「没有找到这个任务」+
   返回首页入口（不会无限重试）；未知路径显示中文 404 页；
2. **首页有「继续查看上次任务」**：靠 localStorage 记住最近一个任务
   （本阶段按决策不做历史任务列表）。

颜色/圆角/间距/字号**只能**使用 `src/styles/tokens.css` 里的语义变量，
页面里不写十六进制值。完整规则与参考来源见
`开发文档/第四阶段设计基调_视觉参考.md`。
