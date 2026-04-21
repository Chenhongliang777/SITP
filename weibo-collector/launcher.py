"""
龙虾智能体统一调度器
一键顺序执行：感知层 -> 研判层 -> 预警层 -> 可视化报告
"""

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).parent.resolve()
COLLECTOR_DIR = PROJECT_DIR / "collector"
DATA_DIR = PROJECT_DIR / "data"
PYTHON = sys.executable  # 使用当前解释器


def run_step(name: str, script: str, args: list, cwd: Path = PROJECT_DIR) -> bool:
    """执行单步，失败返回 False"""
    print("\n" + "=" * 60)
    print(f"步骤: {name}")
    print("=" * 60)
    
    cmd = [PYTHON, str(script)] + args
    print(f"执行: {' '.join(cmd)}")
    
    try:
        result = subprocess.run(cmd, cwd=cwd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n{name} 执行失败 (exit code {e.returncode})")
        return False
    except Exception as e:
        print(f"\n{name} 异常: {e}")
        return False


def find_latest(pattern: str) -> Path:
    """查找 data 目录下最新匹配文件"""
    candidates = sorted(DATA_DIR.glob(pattern), key=lambda x: x.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    return candidates[0]


def main():
    parser = argparse.ArgumentParser(description="龙虾舆情智能体一键启动器")
    parser.add_argument("--keyword", required=True, help="搜索关键词")
    parser.add_argument("--start-date", required=True, help="开始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="结束日期 YYYY-MM-DD")
    parser.add_argument("--target-count", type=int, default=150, help="目标采集数量")
    parser.add_argument("--proxy", type=str, default=None, help="代理地址")
    parser.add_argument("--skip-collect", action="store_true", help="跳过采集，使用已有最新数据")
    parser.add_argument("--headless", action="store_true", default=True, help="无头模式（默认）")
    parser.add_argument("--no-headless", dest="headless", action="store_false", help="显示浏览器窗口")
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("龙虾智能体全栈流水线启动")
    print("=" * 60)
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"项目目录: {PROJECT_DIR}")
    print(f"数据目录: {DATA_DIR}")
    
    # ========== 步骤 1: 感知层 ==========
    if not args.skip_collect:
        collect_args = [
            "--keyword", args.keyword,
            "--start-date", args.start_date,
            "--end-date", args.end_date,
            "--target-count", str(args.target_count),
        ]
        if args.proxy:
            collect_args.extend(["--proxy", args.proxy])
        
        if not run_step("感知层采集", COLLECTOR_DIR / "weibo_collector.py", collect_args):
            sys.exit(1)
    else:
        print("\n跳过采集，使用已有数据")
    
    # 查找最新感知层数据
    data_file = find_latest("*_*_*_*条.json")
    if not data_file:
        print("未找到感知层数据文件")
        sys.exit(1)
    print(f"\n使用感知数据: {data_file.name}")
    
    # ========== 步骤 2: 研判层 ==========
    if not run_step("研判层分析", COLLECTOR_DIR / "analyzer.py", [str(data_file)]):
        sys.exit(1)
    
    judgment_file = find_latest("judgment_*.json")
    if not judgment_file:
        print("未找到研判层输出")
        sys.exit(1)
    print(f"\n使用研判数据: {judgment_file.name}")
    
    # ========== 步骤 3: 预警层 ==========
    if not run_step("预警层评估", COLLECTOR_DIR / "warner.py", [str(judgment_file)]):
        sys.exit(1)
    
    warning_file = find_latest("warning_*.json")
    if not warning_file:
        print("未找到预警层输出")
        sys.exit(1)
    print(f"\n使用预警数据: {warning_file.name}")
    
    # ========== 步骤 4: 可视化报告 ==========
    if not run_step("可视化报告", COLLECTOR_DIR / "report_generator.py", [str(judgment_file), str(warning_file)]):
        sys.exit(1)
    
    # ========== 完成 ==========
    print("\n" + "=" * 60)
    print("全栈流水线执行完毕！")
    print("=" * 60)
    print(f"感知数据: {data_file.name}")
    print(f"研判结果: {judgment_file.name}")
    print(f"预警结果: {warning_file.name}")
    print(f"可视化报告: reports/report_{args.keyword}_*.html")
    print("=" * 60)


if __name__ == "__main__":
    main()