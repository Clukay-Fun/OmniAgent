"""
描述: Feishu Agent 开发启动脚本
主要功能:
    - 自动监控 src 和 config 目录变更
    - 支持热重载 (Hot Reload)
    - 兼容 Windows/Linux 环境
"""
import asyncio
import os
import subprocess
import sys
import time

# Windows 兼容性
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# region 热重载逻辑
def run_scenario_precheck() -> bool:
    """可选的场景回归预检查。"""
    enabled = os.getenv("AGENT_SCENARIO_CHECK", "0").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        return True

    print("🧪 运行场景预检查: tests/scenarios/runner.py")
    extra_args = os.getenv("AGENT_SCENARIO_CHECK_ARGS", "").strip()
    command = [sys.executable, "tests/scenarios/runner.py"]
    if extra_args:
        command.extend(extra_args.split())

    result = subprocess.run(
        command,
        cwd=os.path.dirname(os.path.abspath(__file__)),
        check=False,
    )
    if result.returncode != 0:
        print("❌ 场景预检查失败，已停止启动。")
        return False
    print("✅ 场景预检查通过。")
    return True


def run_with_reload():
    """
    启动热重载开发服务器
    
    依赖:
        - watchdog (文件监控)
    """
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("安装 watchdog: pip install watchdog")
        print("回退到普通模式...")
        run_normal()
        return

    class ReloadHandler(FileSystemEventHandler):
        """文件变更事件处理器"""
        def __init__(self, process_starter):
            self.process_starter = process_starter
            self.process = None
            self.last_reload = 0

        def start_process(self):
            """启动或重启子进程"""
            if self.process:
                self.process.terminate()
                self.process.wait()
            print("\n🔄 启动服务...")
            self.process = self.process_starter()

        def on_modified(self, event):
            """处理文件修改事件 (带防抖)"""
            if event.is_directory:
                return
            src_path = str(event.src_path)
            if not src_path.endswith('.py') and not src_path.endswith('.yaml'):
                return
            # 防抖：500ms 内不重复重载
            now = time.time()
            if now - self.last_reload < 0.5:
                return
            self.last_reload = now
            print(f"\n📝 检测到文件变化: {src_path}")
            self.start_process()

    def start_server():
        return subprocess.Popen(
            [sys.executable, "run_server.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

    handler = ReloadHandler(start_server)
    observer = Observer()
    
    # 监控 src 目录和 config 目录
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config")
    
    if os.path.exists(src_path):
        observer.schedule(handler, src_path, recursive=True)
    if os.path.exists(config_path):
        observer.schedule(handler, config_path, recursive=True)
    
    print("🚀 Feishu Agent 开发模式")
    print("📁 监控目录: src/, config/")
    print("⏹️  按 Ctrl+C 停止\n")

    if not run_scenario_precheck():
        return
    
    handler.start_process()
    observer.start()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n⏹️  停止服务...")
        observer.stop()
        if handler.process:
            handler.process.terminate()
    observer.join()
# endregion


# region 普通启动模式
def run_normal():
    """
    及普通模式启动 (无热重载)
    
    适用:
        - 生产环境
        - watchdog 未安装时降级
    """
    from dotenv import load_dotenv
    load_dotenv()

    if not run_scenario_precheck():
        return
    
    from a2wsgi import ASGIMiddleware
    from waitress import serve
    from src.main import app
    
    wsgi_app = ASGIMiddleware(app)
    print("Starting Feishu Agent on http://0.0.0.0:8088")
    print("Press Ctrl+C to stop")
    serve(wsgi_app, host="0.0.0.0", port=8088, threads=4)


if __name__ == "__main__":
    run_with_reload()
# endregion
