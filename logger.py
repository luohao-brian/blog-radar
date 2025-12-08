import logging
import os
import datetime
import sys

# Default Logs Directory
LOGS_DIR = "logs"

# Global FileHandler to ensure all logs go to the same file per session
_FILE_HANDLER = None
_CONSOLE_HANDLER = None
_ROOT_LOGGER_CONFIGURED = False

def setup_logging(name: str = "blog_radar", log_to_file: bool = True) -> logging.Logger:
    """
    配置并返回一个 Logger 实例。
    如果这是第一次调用，它将配置 Root Logger 的 Handlers。
    后续调用将只获取指定名称的 Logger。
    
    Args:
        name: Logger 的名称
        log_to_file: 是否将日志输出到文件
    
    Returns:
        配置好的 logging.Logger 对象
    """
    global _FILE_HANDLER, _CONSOLE_HANDLER, _ROOT_LOGGER_CONFIGURED
    
    # 获取指定名称的 Logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG) # 所有 Logger 实例都设置为 DEBUG，通过 Handler 过滤级别

    # 只配置 Root Logger 一次
    if not _ROOT_LOGGER_CONFIGURED:
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.DEBUG) # Root Logger 捕获所有消息

        # 清除 Root Logger 现有的 handlers，防止重复添加
        if root_logger.hasHandlers():
            root_logger.handlers.clear()

        # Formatter 配置
        console_formatter = logging.Formatter("%(levelname)s: %(message)s")
        file_formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )

        # 1. Console Handler (for Root Logger)
        _CONSOLE_HANDLER = logging.StreamHandler(sys.stdout)
        _CONSOLE_HANDLER.setFormatter(console_formatter)
        _CONSOLE_HANDLER.setLevel(logging.INFO) # 控制台只输出 INFO 及以上
        root_logger.addHandler(_CONSOLE_HANDLER)

        # 2. File Handler (for Root Logger)
        if log_to_file:
            os.makedirs(LOGS_DIR, exist_ok=True)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            log_filename = f"{name.replace('.', '_')}_{timestamp}.log"
            log_file_path = os.path.join(LOGS_DIR, log_filename)
            
            try:
                _FILE_HANDLER = logging.FileHandler(log_file_path, encoding="utf-8")
                _FILE_HANDLER.setFormatter(file_formatter)
                _FILE_HANDLER.setLevel(logging.DEBUG) # 文件记录 DEBUG 及以上
                root_logger.addHandler(_FILE_HANDLER)
                logger.info(f"所有日志将记录到文件: {log_file_path}") # 这条日志会显示第一次调用者的name
            except Exception as e:
                print(f"无法创建日志文件: {e}")
        
        _ROOT_LOGGER_CONFIGURED = True
    
    return logger
