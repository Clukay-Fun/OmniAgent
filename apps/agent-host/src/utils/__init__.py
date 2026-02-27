"""
描述: 通用工具函数子包。
主要功能:
    - 聚合日志、配置、文件锁等基础工具
    - 为业务模块提供共享底层能力
"""

# region 导入模块
import logging
import os
import fcntl
# endregion

# region 日志配置
def setup_logging():
    """
    配置日志记录器

    功能:
        - 设置日志级别为 DEBUG
        - 创建一个控制台处理器并设置格式
        - 将处理器添加到根记录器
    """
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
# endregion

# region 文件锁
class FileLock:
    """
    文件锁类，用于在多进程环境中对文件进行加锁操作

    功能:
        - 初始化时打开文件
        - 提供 acquire 方法用于加锁
        - 提供 release 方法用于解锁
        - 支持上下文管理协议
    """
    def __init__(self, filename):
        self.filename = filename
        self.file = open(filename, 'a')

    def acquire(self):
        fcntl.flock(self.file, fcntl.LOCK_EX)

    def release(self):
        fcntl.flock(self.file, fcntl.LOCK_UN)

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.release()
        self.file.close()
# endregion

# region 配置工具
def load_config(config_path):
    """
    从指定路径加载配置文件

    功能:
        - 检查配置文件是否存在
        - 读取配置文件内容
        - 返回配置字典
    """
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file {config_path} not found")
    with open(config_path, 'r') as file:
        config_content = file.read()
    # 假设配置文件是 JSON 格式
    import json
    return json.loads(config_content)
# endregion
