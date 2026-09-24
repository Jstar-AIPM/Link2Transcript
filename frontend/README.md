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
npm install                  # 首次
cp .env.example .env.local   # 首次（BACKEND_ORIGIN 默认已是本地后端）

# 日常使用（推荐）：生产模式，最稳
npm run build && npm start   # http://127.0.0.1:3100

# 改前端代码时：开发模式（有热更新）
npm run dev                  # http://127.0.0.1:3100
```

> ⚠️ **不要在前端服务运行时执行 `npm run build`**：`next dev` 与 `next build`
> 共用 `.next` 目录，构建会把正在运行的服务弄崩（真实踩过：页面突然打不开）。
> 需要重新构建时，先停掉服务，构建完再 `npm start`。

> 手机测试用局域网地址：`http://<这台电脑的局域网 IP>:3100`（需同一 Wi-Fi）。
> 开发模式下若页面加载不完整，说明该地址被当作跨来源拦了，把自己的地址加进
> `ALLOWED_DEV_ORIGINS` 即可：`ALLOWED_DEV_ORIGINS=192.168.1.23 npm run dev`。

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

## 两个真实踩过的坑

**1. 开发模式下 `127.0.0.1` 被判定为跨来源**
Next 16 默认拦截「跨来源」的开发资源（HMR、开发态 chunk），用 `127.0.0.1`
或局域网 IP 打开会加载不完整。已在 `next.config.ts` 的 `allowedDevOrigins`
里放行 `127.0.0.1` / `localhost` / 本机局域网 IP / `*.local`。
（生产模式没有这个限制，所以日常使用推荐 `npm start`。）

**2. 运行中执行 `npm run build` 会让服务崩掉**
`next dev` 与 `next build` 共用 `.next`，构建期间正在运行的服务会失效。

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

## 部署到 veFaaS（已上线，2026-09-24）

前后端是两个独立应用，复用同一个网关。**前端必须用 standalone 产物**，且产物要放在
一个不被忽略的目录里（否则打包会把产物内的 `node_modules` 一起排掉，函数启动时报
`Cannot find module 'next'`）。

```bash
# 1) 用线上后端地址构建（rewrites 在构建期固化，必须构建时注入）
cd frontend
BACKEND_ORIGIN="https://<后端访问地址>" npm run build

# 2) 组装独立产物目录（不进版本控制）
rm -rf artifact && mkdir -p artifact/.next
cp -R .next/standalone/. artifact/
cp -R .next/static artifact/.next/static

# 3) 部署（buildCommand 用 true：产物已在上一步构建好）
vefaas deploy --buildCommand "true" --outputPath "artifact" \
  --command "node server.js" --port 3000 --yes
```

注意事项：

- 部署命令里的 `BACKEND_ORIGIN` 必须是**线上后端地址**，否则同源代理会指向本机；
- **不要在 `.vefaasignore` 里排除 `node_modules`**：产物里的 `node_modules` 是运行必需的。
  （veFaaS CLI 会自己生成一个默认忽略文件，它不含 `node_modules`，可以保留。
  我们最初手写的那份含 `node_modules/`，是线上报 `Cannot find module 'next'` 的直接原因。）
- 若发布报 `Release is in rolling status`，先 `vefaas fn release-record status --id <函数ID>`
  等它结束，必要时 `vefaas api AbortRelease --FunctionId <函数ID>` 后再重试。
