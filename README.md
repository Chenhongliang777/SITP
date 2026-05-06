# 足球舆情监测系统（CSL Sentinel）
本项目是一套面向中国足球协会的微博舆情自动化监测流水线，覆盖数据采集、清洗、去重、语义过滤、情感分析、主题聚类、方面级情感抽取、风险扫描、预警评分直至生成专业的 HTML 研判报告。

## 1. 项目结构（`weibo-collector/` 为可运行工程根目录）
```text
weibo-collector/
├── launcher.py              # 统一启动器 + `python launcher.py login` 保存微博登录态
├── collector_backend.py     # 采集：Playwright + 多链路抓取
├── preprocess.py            # 预处理：时间清洗 + 去重（逻辑内联于本文件，无独立 time_cleaner/deduper）
├── analysis_chain.py        # 分析链：语义过滤 → 情感 → 主题 → ABSA → 风险 → 预警（单进程串联）
├── report_html.py           # HTML 研判报告
├── semantic_filter.py       # 语义过滤（sentence-transformers，可选 LLM 灰区）
├── sentiment_model.py       # 情感（预训练模型 + 可选 LLM 回退）
├── topic_cluster.py         # 主题聚类（TF-IDF + K-Means，可选 LLM 簇名）
├── absa_extractor.py        # 方面级情感（可选 LLM）
├── risk_scanner.py          # 风险扫描（可选 LLM）
├── warner_score.py          # 预警评分（无 LLM）
├── requirements.txt
├── .env                     # 需自建（含 DEEPSEEK_API_KEY 等）
├── utils/
│   ├── llm_client.py        # LLM HTTP 封装（OpenAI 兼容）
│   ├── runtime.py           # 并发、批大小等运行时参数
│   └── embedder.py          # 远端向量（可选，主流程语义模块用本地模型）
├── data/                    # 中间 JSON 与登录态
├── reports/                 # 最终 HTML
└── logs/                    # 采集日志
```
## 2. 环境与依赖
### 2.1 Python 3.10+
### 2.2 依赖库安装：
```
bash
pip install -r requirements.txt
```
### 2.3 Playwright 浏览器驱动（仅采集步骤需要）：
```
bash
python -m playwright install --with-deps chromium
```
### 2.4 首次运行分析链中的语义过滤会自动下载语义模型（默认 `BAAI/bge-small-zh-v1.5`，体积约百余 MB）。

## 3. 配置
在 **`weibo-collector/`** 目录下创建 `.env` 文件，内容示例：
    DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
    DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions

系统以 DeepSeek 作为 LLM 后端。使用 **`launcher.py` 时必须在 `.env` 中配置 `DEEPSEEK_API_KEY`**（启动器会校验）；若单独调试子脚本，部分模块在无 Key 时会走规则/本地模型回退，效果可能下降。详见下文「高效模式」与各模块 `--help`。

## 4. 启动方式
### 4.1 登录微博（首次/Token 过期时）

```bash
python launcher.py login
```

脚本会启动系统 Chrome，请手动扫码登录微博，然后在终端按回车保存登录态至 `data/weibo_auth.json`。采集步骤会自动加载该文件。

### 4.2 运行完整流水线（推荐：`launcher.py`）

在 **`weibo-collector/`** 目录下执行：

```bash
python launcher.py \
    --keyword "中超" \
    --start-date 2026-04-28 \
    --end-date 2026-04-29 \
    --target-count 200
```

**常用参数**

| 参数 | 含义 |
|------|------|
| `--keyword` | 搜索关键词（如 中超、国足） |
| `--start-date` / `--end-date` | 监测日期范围 `YYYY-MM-DD` |
| `--target-count` | 目标采集条数（默认 80，越小通常越快） |
| `--no-headless` | 采集时显示浏览器窗口（调试用） |
| `--proxy` | 代理地址，如 `http://127.0.0.1:7890` |
| `--fast-collect` | 缩短采集中的滚动/翻页等待（略增风控概率） |
| `--turbo-collect` | 再加速：更短滚动/翻页等待（**不含** m 站接口；默认仅 s.weibo 高级搜索 + 页内 ajax）；**风控风险高于** `--fast-collect`，建议与 `--fast-collect` 同开 |
| `--start-from` | 从某步接着跑，见下表 |
| `--llm-workers` | 仍使用 LLM 的环节之 HTTP 并发上限（默认 6） |

**采集提速说明（默认不走 m 站）**：检索以 **`s.weibo.com` 高级搜索页** 为主，监听同页 **`/ajax/statuses/`** XHR 解析正文（比纯 DOM 快）；滚动采用「快速滚到底」策略，并用 `wait_for_response` 减少固定盲等。每页 **只注册一次** response 监听，避免重复监听拖慢速度。  
若仍希望使用 **m.weibo.cn 接口** 作补充，设置环境变量 **`WEIBO_USE_MOBILE_API=1`**（可选 `WEIBO_MOBILE_MAX_PAGES`，上限 30）。另有 **`WEIBO_MAX_DOM_PAGES`**：高级搜索最多翻页数（默认 30）。

**`--start-from` 可选值**（与 `launcher.py` 内 `STEPS` 一致）：`collector` → `preprocess` → `analysis_chain` → `report_html`。从非第一步开始时，请确保 `data/`（或 `reports/`）中已有与 `--keyword` 匹配的上游产物。

**默认四步流水线（子进程顺序）**

| 步骤名 | 脚本 | 主要产物 | 说明 |
|--------|------|----------|------|
| 1. `collector` | `collector_backend.py` | `data/raw_<keyword>_*.json` | Playwright 采集原始微博 |
| 2. `preprocess` | `preprocess.py` | `data/timecleaned_*`、`data/deduped_*`（启动器校验 **deduped**） | 时间清洗 + 去重，单进程合并 |
| 3. `analysis_chain` | `analysis_chain.py` | 链内依次写出 `filtered_*`、`sentiment_*`、`topic_*`、`absa_*`、`risk_*`，终点 **`warning_*`** | 语义过滤 → 情感 → 主题 → ABSA → 风险 → 预警 |
| 4. `report_html` | `report_html.py` | `reports/report_<keyword>_*.html` | 读取最新 `warning_*` 与 `risk_*` 生成报告 |

链内各阶段仍会落盘中间 JSON，便于调试与复现；**并非**早期「一步起一个独立 Python 进程」的 11 段串联，但模块文件名与上表逻辑一一对应。

最终用浏览器打开 `reports/` 下最新 HTML 即可查看报告（支持打印导出 PDF）。

### 4.3 高效模式（省时间 / 省 LLM 调用）

在**可接受略降质量**的前提下加快运行、减少 DeepSeek 调用，可用下面几类参数；其中 **`--efficient` 为「一键」开关**。

| 类型 | 参数 | 作用 |
|------|------|------|
| **一键高效** | `--efficient` | 等价于同时指定：`--no-llm`、`--tfidf-topic-only`、`--rule-absa`、`--rule-risk-only`、`--rule-report-only`（在 `parse_args` 之后生效，会覆盖这五项的「未开启」状态） |
| 采集加速 | `--fast-collect` | 缩短页面等待（略增风控概率）；**不包含**在 `--efficient` 内，需另加 |
| 减数据量 | `--target-count N` | 将 N 调小（如 80～120） |
| 并发（仍有 LLM 时） | `--llm-workers K` | 情感等环节若仍触发 LLM 时的 HTTP 并发（默认 6） |
| 关闭语义灰区 LLM | `--no-llm` | 模糊区域不用 LLM，改用启发式阈值（已含于 `--efficient`） |
| 语义灰区「严弃」 | `--semantic-gray-reject` | 相似度在正负阈值之间的帖子**一律丢弃**（不调 LLM、不用启发式捞回）；**不包含**在 `--efficient` 内，按需叠加 |
| 关闭情感 LLM 回退 | `--no-sentiment-llm-fallback` | 预训练模型失败或单条推理失败时**不**调 LLM，用默认中性兜底；**不包含**在 `--efficient` 内，按需叠加 |
| 关闭主题簇 LLM 命名 | `--tfidf-topic-only` | 仅用 TF-IDF 作簇标签（已含于 `--efficient`） |
| 关闭 ABSA 的 LLM | `--rule-absa` | 仅用规则 / jieba（已含于 `--efficient`） |
| 关闭风险扫描 LLM | `--rule-risk-only` | 仅用规则（已含于 `--efficient`） |
| 关闭报告内 LLM 摘要 | `--rule-report-only` | 报告研判改为规则模板（已含于 `--efficient`） |

**示例（推荐：一键高效 + 采集加速）：**

```bash
python launcher.py \
    --keyword "中超" \
    --start-date 2026-04-28 \
    --end-date 2026-04-29 \
    --target-count 120 \
    --efficient \
    --fast-collect \
    --llm-workers 10
```

**更省 LLM 的叠加示例**（在 `--efficient` 基础上再关掉情感回退、语义严弃）：

```bash
python launcher.py --keyword "中超" --start-date 2026-04-28 --end-date 2026-04-29 \
    --efficient --semantic-gray-reject --no-sentiment-llm-fallback
```

**说明**：开启 `--efficient` 且未加 `--no-sentiment-llm-fallback` 时，**情感仍以本地模型为主**；仅当批量/单条推理失败时仍可能触发 **LLM 回退**。超长文本已在 `sentiment_model.py` 中按模型上限截断，以降低失败率。环境变量 `SEMANTIC_ENCODE_BATCH`、`SENTIMENT_BATCH`（见 `utils/runtime.py`）可微调本地批大小，可与上述参数叠加。

## 5. 已知问题与优化方向
本项目已具备基本可用性，但仍存在以下待改善之处：

### 5.1 负面率数据缺失
warner_score.py 的元数据未计算 negative_rate，导致报告中“负面率”指标始终显示“暂无”。

修复：在 warner_score.py 的 meta 中添加 "negative_rate": round(negative_count/total, 4)。

### 5.2 代码冗余（现状与后续方向）

**现状（与早期版本相比已有收敛）**  
HTTP 层已主要通过 `weibo-collector/utils/llm_client.py`（如 `try_llm_client`、`chat_json`、`chat_text`）访问兼容 OpenAI 风格的接口；`semantic_filter.py`、`sentiment_model.py`、`topic_cluster.py`、`absa_extractor.py`、`risk_scanner.py`、`report_html.py` 等均通过该封装发起调用，**不再在各脚本内重复维护一套独立的 DeepSeek `requests` 拼接逻辑**。

**仍存在的冗余**  
与业务强相关的 **提示词模板、JSON 解析与字段校验、失败后的规则回退** 仍分散在各模块，属于「逻辑样板」层面的重复；若启用远端向量，`utils/embedder.py` 中另有请求与环境变量读取，可与 LLM 配置进一步统一。

**后续可选优化**  
提炼少量公共辅助函数（例如统一的「短回复 JSON 任务」封装），或扩展 `llm_client` 的薄封装，以减少各文件中的重复样板代码。

**哪些环节可以放弃 LLM（换效率、略降质量）**  
以下均对应 `python launcher.py …` 的开关；关闭后该环节不再发起对应 LLM 请求，而走规则、TF-IDF 或本地模型路径（详见各脚本 `--help`）。与 **`--fast-collect`、`--target-count`、`--llm-workers` 的组合用法** 见 **§4.3 高效模式**。

| 环节 | 作用说明 | 启动参数 |
|------|----------|----------|
| 语义过滤（相似度灰区） | 向量模型不确定时，不再调用 LLM 判定是否足球相关，改为阈值启发式 | `--no-llm` |
| 语义过滤（灰区严弃） | 灰区一律判非足球并丢弃（不调 LLM、不用启发式捞回） | `--semantic-gray-reject` |
| 主题聚类（簇命名） | 簇标签仅用 TF-IDF 关键词，不调用 LLM 生成主题名 | `--tfidf-topic-only` |
| 方面级情感（ABSA） | 仅用规则 / jieba 路径，不调用 LLM 抽取 | `--rule-absa` |
| 风险扫描 | 仅用规则与硬规则，不调用 LLM 判定风险类别与等级 | `--rule-risk-only` |
| HTML 报告（研判摘要等） | 报告中的 LLM 研判摘要等改为规则模板，不调用 LLM | `--rule-report-only` |
| 情感分析（失败回退） | 模型加载失败或单条推理失败时**不**调用 LLM，使用默认中性兜底 | `--no-sentiment-llm-fallback` |
| 一键关闭多环节 LLM | 等价于同时开启上表中 `--no-llm` 与三项 `rule-*` 及 `--tfidf-topic-only` | `--efficient` |

**说明**：情感在默认配置下以 **本地预训练模型为主**；失败时是否走 LLM 由 **`--no-sentiment-llm-fallback`** 控制（详见 **§4.3**）。

采集（`collector_backend.py`）与纯数值步骤（如 `warner_score.py`）**不涉及 LLM**。

### 5.3 流水线可视性差
各步骤输出仅以 print 形式打印，缺乏进度条或步骤耗时统计。

建议：集成 tqdm 或丰富的日志格式，便于长时间运行时掌握进度。

### 5.4 采集稳定性依赖微博页面结构
collector_backend.py 通过多种回退方式（API、移动端、本地缓存）增强了鲁棒性，但微博反爬策略仍可能导致采集失败或数据量不足。

相对时间解析可能产生偏差  
`preprocess.py` 内联的时间解析（原 time_cleaner 逻辑）在解析「刚刚」「X 分钟前」等相对时间时，依赖从预处理运行时刻推算；若与采集时刻偏差大，可能削弱时间趋势图的可信度。

建议：采集时直接提取微博的绝对时间戳（<a> 标签中常携带）。

### 5.5 ABSA 抽取错误
规则回退部分对部分目标词（如“本赛季”、“鲁莽”）做了不合理的抽取和情感判定，可能扭曲方面级矩阵。

建议：优化规则词典，或增强 LLM 输出后处理校验。

### 5.6 首次语义模型下载较慢
若网络不佳，sentence-transformers 下载模型可能超时。

建议：使用 HF_ENDPOINT=https://hf-mirror.com 环境变量加速。


