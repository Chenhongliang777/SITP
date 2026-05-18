# 足球舆情监测系统（CSL Sentinel）封装 Todo List

> 目标：Windows 单安装包（内置语义模型 + Chromium）+ 桌面 GUI + 局域网手机 H5（方案 A）。  
> PC 与手机均可：填关键词 / 日期 / 条数 / 高效模式 → 开始监测 → 看进度与日志 → 打开报告。  
> 大模型设置：API Key + 模型预设（DeepSeek 默认 + 自定义 Base URL/Model）。  
> 微博登录在 Windows 端完成；手机不下发 Playwright 登录。

---

## 阶段 0：约定与目录

| # | 任务 | 说明 |
|---|------|------|
| 0.1 | 确定技术栈 | 桌面：**CustomTkinter**（已选定 ✅）；远程：**FastAPI** + 静态 H5；打包：**PyInstaller** |
| 0.2 | 规划目录结构 | 如 `weibo-collector/app/`：`paths.py`、`config.py`、`pipeline_runner.py`、`llm_settings.py`；`app/gui/`；`app/web/`（API + 静态页） |
| 0.3 | 定义「应用数据根目录」 | 开发态 = `weibo-collector/`；打包态 = exe 同级的 `data/`、`reports/`、`.env`、`models/`、`browsers/` |
| 0.4 | 编写《封装与局域网访问说明》提纲 | 安装、首次配置 API、PC 登录、GUI 使用、手机连 WiFi 地址、防火墙端口 |

---

## 阶段 1：基础设施（GUI / Web 共用）

| # | 任务 | 说明 | M1 |
|---|------|------|-----|
| 1.1 | 实现 `paths.py` | `get_app_root()`、`get_data_dir()`、`get_reports_dir()`、`get_env_path()`；兼容 `sys.frozen` / PyInstaller | ✅ |
| 1.2 | 实现 `config.py` | 读写 `.env`；校验 API Key；启动时 `load_dotenv` 指向应用目录 | ✅ |
| 1.3 | 实现 `llm_settings.py` | 预设：DeepSeek（默认 URL + `deepseek-chat`）、自定义；字段：API Key、Base URL、Model；保存/加载；与 `utils/llm_client.py` 对齐（可保留 `DEEPSEEK_*` 别名或统一 `LLM_*` + 兼容读取） | ✅ |
| 1.4 | 小改 `utils/llm_client.py` | 从统一配置读入；错误提示改为「缺少 API Key」 | ✅ |
| 1.5 | 小改 `launcher.py` | `check_env()` 改为检查 API Key 即可；暴露可编程入口 `run_pipeline(...)`（见 1.6） | ✅ |
| 1.6 | 实现 `pipeline_runner.py` | 封装四步：`collector` → `preprocess` → `analysis_chain` → `report_html`；参数：keyword、日期、target_count、efficient 等；回调：`on_step_start`、`on_step_done`、`on_log`、`on_error`；**全局单任务锁** | ✅ |
| 1.7 | 改造 `login.py` | `app/weibo_login.py` + GUI 按钮确认；CLI 仍可用 `login.py` / `launcher.py login` | ✅ |
| 1.8 | 改造语义模型路径 | 打包后 `HF_HOME` / 模型目录指向内置 `models/`（如 `BAAI/bge-small-zh-v1.5`） | ⏳ 阶段 6 |
| 1.9 | 报告工具 | `app/reports_util.py`：`find_latest_report`、`open_report` | ✅ |

---

## 阶段 2：Windows 桌面 GUI（本机完整操作）

| # | 任务 | 说明 | M1 |
|---|------|------|-----|
| 2.1 | 主窗口框架 | 多页/多 Tab：**登录** \| **监测任务** \| **设置** | ✅ |
| 2.2 | **设置**页 | API Key（密码框）；模型预设（DeepSeek / 自定义）；自定义：Base URL、Model；保存写 `.env` | ✅ |
| 2.3 | **登录**页 | 状态：已登录 / 未登录；「打开微博登录窗口」「确认已登录并保存」 | ✅ |
| 2.4 | **监测任务**页 | 关键词、开始/结束日期、目标条数、高效模式（默认关）；「开始监测」；运行中禁用表单 | ✅ |
| 2.5 | 进度与日志 | 四段进度（0/25/50/75/100）+ 当前步骤文案；可滚动日志 | ✅ |
| 2.6 | 完成态 | 成功：自动打开 report + 提示路径；失败：错误摘要；「打开报告目录」 | ✅ |
| 2.7 | 后台线程 | 子线程跑 `pipeline_runner`，UI 不卡死；取消任务（v1 可选） | ✅ |
| 2.8 | 启动检查 | API Key、登录态提示；不强制每次登录 | ✅ |
| 2.9 | 入口 `gui_main.py` | `python gui_main.py` 启动 GUI | ✅ |

---

## 阶段 3：局域网 Web（手机 H5，方案 A）

| # | 任务 | 说明 |
|---|------|------|
| 3.1 | Web 框架 | **FastAPI** + 与 `pipeline_runner` 同进程 |
| 3.2 | REST API | `GET /api/status`；`POST /api/run`；`GET/PUT /api/settings`；`POST /api/login/start`、`POST /api/login/confirm`；`GET /api/report/latest` |
| 3.3 | 与 GUI 互斥 | 共用单任务锁；运行中返回 409 |
| 3.4 | 静态 H5 | 设置、登录状态、任务表单、进度、日志（轮询或 SSE）、报告链接 |
| 3.5 | 报告访问 | 安全提供 `reports/` 下 HTML（防路径穿越） |
| 3.6 | 绑定与发现 | 默认 `0.0.0.0:8765`（可配置）；GUI 显示 `http://本机IP:端口` + 可选二维码 |
| 3.7 | 简易鉴权 | 局域网 token / PIN（v1 可简化） |
| 3.8 | `web_server.py` | GUI 开关「允许局域网访问」后台起服务 |
| 3.9 | 防火墙提示 | 首次开启：允许专用网络入站 |

---

## 阶段 4：登录分工（方案 A）

| # | 任务 | 说明 |
|---|------|------|
| 4.1 | 用户文档与 UI 文案 | 微博扫码登录在 **Windows** 完成；手机下任务与看报告 |
| 4.2 | H5 登录区 | 显示 PC 登录状态；未登录提示在电脑上操作 |
| 4.3 | `GET /api/login/status` | 供 H5 轮询 `weibo_auth.json` 是否有效 |

---

## 阶段 5：联调与测试（开发态）

| # | 任务 | 说明 |
|---|------|------|
| 5.1 | GUI 全流程 | 设置 API → 登录 → 小 target-count + efficient 跑通 → 自动打开 report |
| 5.2 | Web 全流程 | 手机浏览器访问局域网 IP → 下任务 → 进度 → 报告 |
| 5.3 | 互斥 | GUI 运行中 Web 拒绝；反之亦然 |
| 5.4 | 异常路径 | 无 API、未登录、流水线失败提示一致 |
| 5.5 | 单元测试 | `paths`、`llm_settings`、`pipeline_runner` 状态机（可 mock） |

---

## 阶段 6：打包（内置模型 + Chromium）

| # | 任务 | 说明 |
|---|------|------|
| 6.1 | 构建脚本 | 预下载 bge 到 `vendor/models/`；`playwright install chromium` 到 `vendor/browsers/` |
| 6.2 | 启动环境变量 | `PLAYWRIGHT_BROWSERS_PATH`、`HF_HOME`、`TRANSFORMERS_OFFLINE` 等 |
| 6.3 | PyInstaller spec | 入口、隐式导入、资源列表 |
| 6.4 | 安装器 | Inno Setup / NSIS：快捷方式、卸载策略 |
| 6.5 | 打包机实测 | 无 Python 环境：GUI、Web、登录、完整流水线 |
| 6.6 | 更新 README | 体积、仅 Windows、手机 WiFi 访问、API 预设 |

---

## 阶段 7：文档与交付

| # | 任务 | 说明 |
|---|------|------|
| 7.1 | 用户手册（非技术） | 安装 → API → PC 登录 → GUI / 手机 H5 |
| 7.2 | 开发者说明 | 预设模型列表、端口、构建命令 |
| 7.3 | v1 明确不做 | 手机 exe、公有云多租户、手机端微博登录 |

---

## 依赖关系

```text
阶段0 → 阶段1（paths / config / llm / login / pipeline）
              ├→ 阶段2（GUI）
              └→ 阶段3（Web）→ 阶段4
阶段2 + 阶段3 → 阶段5（联调）
阶段1 + 阶段5 → 阶段6（打包）
阶段6 → 阶段7（文档）
```

---

## 里程碑

| 里程碑 | 范围 | 可交付 |
|--------|------|--------|
| **M1** | 阶段 1 + 阶段 2 | 开发机 GUI 全流程（**代码已就绪，待本机跑通验收**） |
| **M2** | 阶段 3 + 4 + 5.2 | 手机局域网 H5 可用 |
| **M3** | 阶段 6 | Windows 安装包 |

---

## 工作量粗估（单人）

| 阶段 | 约 |
|------|-----|
| 1 基础设施 | 3～5 天 |
| 2 GUI | 4～6 天 |
| 3 Web + H5 | 4～6 天 |
| 5 联调 | 2～3 天 |
| 6 打包 | 4～7 天 |
| **合计** | **约 3～4 周** |

---

## 已确认的产品决策

- **方案 A**：重计算在 Windows PC；手机浏览器访问局域网 Web，非公有云部署。
- **双端下任务**：GUI 与 H5 均可填参并开始；共用 `pipeline_runner`，互斥运行。
- **进度展示**：四段进度 + 当前步骤文字 + 可滚动日志（不做到子步骤百分比）。
- **打包**：内置语义模型 + Chromium；接受大体积与杀毒误报可能。
- **大模型**：API Key + 预设（DeepSeek 默认）+ 自定义 Base URL/Model；改动集中在配置层，不大改各分析模块。
- **移动端**：不提供 Android/iOS 安装包；v1 不做手机端微博 Cookie 登录。
