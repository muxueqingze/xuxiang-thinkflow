"""
ThinkFlow 启动入口（全局命令入口）

通过 thinkflow.cmd 调用，可在任意工作区启动。
"""
import sys
import os

# Windows 控制台默认可能是 GBK；项目内统一为 UTF-8，避免测试和 UI 输出炸掉。
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

# 把项目目录加入 path，让相对导入能工作
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PROJECT_DIR)

from src.cli import main

if __name__ == "__main__":
    main()
