# 项目当前情况  

-目前代码为一个验证成功的 MVP（最小可行产品）：
-感知层能采到真实微博数据
-研判层能读取并输出五维分析
-预警层能基于规则评分并调用 LLM（存疑）
-可视化生成器可以基于研判层和预警层产出生成可视的HTML报告
-调度器能一键串联

## 现有问题
但仍存在许多问题：
1.感知层
1.1登录持久化问题
当前的爬虫需要先登录微博以获得Cookie，但微博的登录Cookie似乎不是永久的。有没有永久化的手段？
如果无法永久化，该如何简化登录？当前必须使用本地已有的Chrome实现扫码登录，如何优化为摆脱本地约束？
1.2数据清洗智能化问题
当前爬取数据的清洗依赖内置字典，不够准确，能不能调用大语言模型，对爬取的JSON数据做清洗？
2.研判层
| 数据采集     | ❌ **很多数据在爬取时未展开博文，且数据清洗效果很差**   |
| 时间解析     | ❌ **只识别了两个时间段，与实际情况不符**   |
| 主题聚类     | ✅ 正常           |
| **情感判定** | ❌ **严重漏判**     |
| **时序突变** | ❌ **阈值过高**     |
| **预警评分** | ❌ **过于保守**     |
| 可视化报告    | ⚠️ 能生成，但基于失真数据 |

## 如何启动项目
所有实现均存放在“weibo-collector"中
将终端切换到weibo-collector目录后，使用
"python launcher.py --keyword "中超" --start-date "2026-04-19" --end-date "2026-04-20" --target-count 50"可以直接运行所有模块得到最终可视化文件。（keyword/start-date/end-date/target-count 均可自定义，符合格式即可)
如果想单独运行某一模块：
感知层：“python collector/weibo_collector.py --keyword "中超" --start-date "2026-04-20" --end-date "2026-04-21" --target-count 150”
研判层：“ python collector/analyzer.py”
预警层：“ python collector/warner.py”
报告生成：“python collector/report_generator.py”

