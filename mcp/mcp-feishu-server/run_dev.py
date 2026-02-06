"""
描述: MCP Server 开发启动脚本
主要功能:
    - 自动监控 src 目录文件变更
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
            if not event.src_path.endswith('.py') and not event.src_path.endswith('.yaml'):
                return
            # 防抖：500ms 内不重复重载
            now = time.time()
            if now - self.last_reload < 0.5:
                return
            self.last_reload = now
            print(f"\n📝 检测到文件变化: {event.src_path}")
            self.start_process()

    def start_server():
        return subprocess.Popen(
            [sys.executable, "run_server.py"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

    handler = ReloadHandler(start_server)
    observer = Observer()
    
    # 监控 src 目录
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
    
    if os.path.exists(src_path):
        observer.schedule(handler, src_path, recursive=True)
    
    # 监控当前目录的 yaml 文件
    current_path = os.path.dirname(os.path.abspath(__file__))
    observer.schedule(handler, current_path, recursive=False)
    
    print("🚀 MCP Feishu Server 开发模式")
    print("📁 监控目录: src/")
    print("⏹️  按 Ctrl+C 停止\n")
    
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
    
    from a2wsgi import ASGIMiddleware
    from waitress import serve
    from src.main import app
    
    wsgi_app = ASGIMiddleware(app)
    print("Starting MCP Feishu Server on http://0.0.0.0:8081")
    print("Press Ctrl+C to stop")
    serve(wsgi_app, host="0.0.0.0", port=8081, threads=4)


if __name__ == "__main__":
    run_with_reload()
# endregion
