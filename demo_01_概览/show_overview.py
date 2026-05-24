"""
demo_01 概览 — 打印10步流水线全景图 + 每步数据契约
运行: python show_overview.py
"""
import json
import sys
from pathlib import Path

# Windows 编码兼容
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PIPELINE_FILE = Path(__file__).parent / "pipeline.json"


def main():
    with open(PIPELINE_FILE, "r", encoding="utf-8") as f:
        config = json.load(f)

    pipeline = config["pipeline"]

    print("\n" + "=" * 72)
    print("   AI测试平台 -- 10步流水线全景图")
    print("=" * 72)

    # ASCII 流水线
    print("\n  ", end="")
    for i, step in enumerate(pipeline):
        if i == 0:
            print("(1)(2)上传+解析", end="")
        elif i == 1:
            continue
        else:
            print("  -->  ", end="")
            num = step["step"]
            icons = ["(3)", "(4)", "(5)", "(6)", "(7)", "(8)", "(9)", "(10)"]
            icon = icons[i-2] if i-2 < len(icons) else f"({num})"
            print(f"{icon}{step['name']}", end="")
    print("\n")

    # 详细表格
    print(f"  {'步':<6} {'讲次':<6} {'Demo目录':<24} {'输入':<30} {'输出':<30}")
    print(f"  {'-'*6} {'-'*6} {'-'*24} {'-'*30} {'-'*30}")

    for step in pipeline:
        if step["step"] == 2:
            continue
        num = step["step"]
        icon_map = {1: "(1)(2)", 3: "(3)", 4: "(4)", 5: "(5)", 6: "(6)", 7: "(7)", 8: "(8)", 9: "(9)", 10: "(10)"}
        icon = icon_map.get(num, str(num))
        print(f"  {icon:<6} 第{step['lecture']:>2}讲  {step['demo']:<24} {step['input']:<30} {step['output']:<30}")

    # 依赖链路说明
    print(f"\n  {'-' * 68}")
    print("  核心依赖链: login -> createDevice -> getDevice/updateDevice/deleteDevice")
    print("  每个 demo 独立可跑: cd demo_XX && python <entry_file>")
    print("  一键全链路: cd demo_11_端到端 && python run_pipeline.py")
    print(f"  {'-' * 68}\n")


if __name__ == "__main__":
    main()
