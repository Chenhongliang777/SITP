# 足球舆情监测系统（CSL Sentinel）
本项目是一套面向中国足球协会的微博舆情自动化监测流水线，覆盖数据采集、清洗、去重、语义过滤、情感分析、主题聚类、方面级情感抽取、风险扫描、预警评分直至生成专业的 HTML 研判报告。

## 1. 项目结构
project_root/
├── login.py                  # 登录工具：获取并保存微博 Cookies
├── collector_backend.py      # 数据采集后端：基于 Playwright 的多链路抓取
├── launcher.py               # **流水线统一启动器**（核心入口）
├── time_cleaner.py           # 时间清洗：解析相对时间并过滤日期范围
├── deduper.py                # 去重：按 mid 和文本前缀去重
├── semantic_filter.py        # 语义过滤：基于 sentence‑transformers + LLM 的回退，过滤非足球内容
├── sentiment_model.py        # 情感分析：预训练模型（5 级情感）+ LLM 回退
├── topic_cluster.py          # 主题聚类：TF‑IDF + K‑Means + LLM 生成主题标签
├── absa_extractor.py         # 方面级情感抽取 (ABSA)：LLM 主路径 + 规则回退
├── risk_scanner.py           # 风险扫描：LLM 语义动态判定风险类别与等级
├── warner_score.py           # 预警评分：密度制加权评分，输出风险等级
├── report_html.py            # HTML 报告生成：可视化仪表板 + LLM 研判摘要 + 分级处置建议
├── embedder.py               # 工具库：Embedding 封装（远端 API + 本地哈希回退）
├── llm_client.py             # 工具库：LLM 调用封装（兼容 OpenAI 风格 API）
├── requirements.txt          # Python 依赖清单
├── .env.example              # 环境变量模板（需要自行创建 .env 文件）
├── data/                     # 运行产物存放目录（各类 JSON 和 HTML 文件）
├── reports/                  # 最终 HTML 报告输出目录
└── logs/                     # 采集日志目录

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
### 2.4 首次运行 semantic_filter.py 会自动下载约 130 MB 的语义模型（BAAI/bge‑small‑zh‑v1.

## 3. 配置
在项目根目录创建 .env 文件，内容如下：
    DEEPSEEK_API_KEY=sk-xxxxxxxxxxxxxxxxxxxxxxxx
    DEEPSEEK_API_URL=https://api.deepseek.com/chat/completions

系统以 DeepSeek 作为 LLM 后端，API Key 为必填项。如无 API Key，部分步骤（语义过滤模糊区域、情感回退、ABSA、风险扫描、报告摘要）会回退到规则路径，但效果会显著下降。

## 4. 启动方式
### 4.1 登录微博（首次/Token 过期时）
```
bash
python login.py
```
脚本会启动系统 Chrome，请手动扫码登录微博，然后在终端按回车保存登录态至 data/weibo_auth.json。采集步骤会自动加载该文件。

### 4.2 运行完整流水线
推荐方式 – 使用统一的启动器：
```
bash
python launcher.py \
    --keyword "中超" \
    --start-date 2026-04-28 \
    --end-date 2026-04-29 \
    --target-count 200
```
参数说明：

--keyword：搜索关键词（如 中超、国足）

--start-date、--end-date：监测日期范围 YYYY-MM-DD

--target-count：期望采集条数（默认 80）

--no-headless：采集时显示浏览器窗口（调试用）

--proxy：代理地址（如 http://127.0.0.1:7890）

--start-from：从指定步骤恢复运行（调试用，可选 collector、semantic_filter 等）

流水线将依次执行：

collector_backend.py → 输出 raw_<keyword>_* .json

time_cleaner.py → 输出 timecleaned_<keyword>_* .json

deduper.py → 输出 deduped_<keyword>_* .json

semantic_filter.py → 输出 filtered_<keyword>_* .json（及 rejected_* .json）

sentiment_model.py → 输出 sentiment_<keyword>_* .json

topic_cluster.py → 输出 topic_<keyword>_* .json

absa_extractor.py → 输出 absa_<keyword>_* .json

risk_scanner.py → 输出 risk_<keyword>_* .json

warner_score.py → 输出 warning_<keyword>_* .json

report_html.py → 输出 reports/report_<keyword>_* .html

最终用浏览器打开 HTML 即可查看图文并茂的监测报告（支持打印导出 PDF）。

## 5. 已知问题与优化方向
本项目已具备基本可用性，但仍存在以下待改善之处：

### 5.1 负面率数据缺失
warner_score.py 的元数据未计算 negative_rate，导致报告中“负面率”指标始终显示“暂无”。

修复：在 warner_score.py 的 meta 中添加 "negative_rate": round(negative_count/total, 4)。

### 5.2 代码冗余
absa_extractor.py、risk_scanner.py、report_html.py 等脚本独立实现了 DeepSeek API 调用逻辑，与已封装的 llm_client.py 重复。

建议：统一使用 llm_client.chat() 或 classify()，减少重复代码并增强错误处理。

### 5.3 流水线可视性差
各步骤输出仅以 print 形式打印，缺乏进度条或步骤耗时统计。

建议：集成 tqdm 或丰富的日志格式，便于长时间运行时掌握进度。

### 5.4 采集稳定性依赖微博页面结构
collector_backend.py 通过多种回退方式（API、移动端、本地缓存）增强了鲁棒性，但微博反爬策略仍可能导致采集失败或数据量不足。

相对时间解析可能产生偏差
time_cleaner.py 解析“刚刚”、“X 分钟前”等相对时间时，依赖从采集时刻推算，若采集速度慢可能导致大部分帖子时间落在同一时刻，削弱了时间趋势图的可信度。

建议：采集时直接提取微博的绝对时间戳（<a> 标签中常携带）。

### 5.5 ABSA 抽取错误
规则回退部分对部分目标词（如“本赛季”、“鲁莽”）做了不合理的抽取和情感判定，可能扭曲方面级矩阵。

建议：优化规则词典，或增强 LLM 输出后处理校验。

### 5.6 首次语义模型下载较慢
若网络不佳，sentence-transformers 下载模型可能超时。

建议：使用 HF_ENDPOINT=https://hf-mirror.com 环境变量加速。


