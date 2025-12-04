"""
日志记录模块
提供统一的日志记录功能，自动生成带时间戳前缀的日志文件
"""
import os
import logging
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler

# 全局日志文件路径
_log_file_path = None
_logger = None
_log_lock = threading.Lock()
_stream_to_terminal = True

def init_logger(stream_to_terminal=True, log_dir="logs"):
    """
    初始化日志记录器
    
    Args:
        stream_to_terminal: 是否输出到终端
        log_dir: 日志文件目录
    """
    global _log_file_path, _logger, _stream_to_terminal
    
    _stream_to_terminal = stream_to_terminal
    
    # 创建日志目录
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 生成带时间戳的日志文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_filename = f"{timestamp}_log.txt"
    _log_file_path = os.path.join(log_dir, log_filename)
    
    # 创建日志记录器
    _logger = logging.getLogger('gongwei_logger')
    _logger.setLevel(logging.DEBUG)
    
    # 清除已有的处理器
    _logger.handlers.clear()
    
    # 文件处理器
    file_handler = RotatingFileHandler(
        _log_file_path,
        maxBytes=10*1024*1024,  # 10MB
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setLevel(logging.DEBUG)
    
    # 控制台处理器（根据配置）
    console_handler = None
    if stream_to_terminal:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
    
    # 格式化器
    formatter = logging.Formatter(
        '[%(asctime)s] [%(levelname)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    if console_handler:
        console_handler.setFormatter(formatter)
    
    # 添加处理器
    _logger.addHandler(file_handler)
    if console_handler:
        _logger.addHandler(console_handler)
    
    return _logger

def get_logger():
    """获取日志记录器实例"""
    global _logger
    if _logger is None:
        init_logger()
    return _logger

def get_log_file_path():
    """获取当前日志文件路径"""
    return _log_file_path

def log_info(message):
    """记录信息日志"""
    with _log_lock:
        logger = get_logger()
        logger.info(message)
        if _stream_to_terminal:
            print(message)

def log_debug(message):
    """记录调试日志"""
    with _log_lock:
        logger = get_logger()
        logger.debug(message)
        if _stream_to_terminal:
            print(message)

def log_warning(message):
    """记录警告日志"""
    with _log_lock:
        logger = get_logger()
        logger.warning(message)
        if _stream_to_terminal:
            print(message)

def log_error(message):
    """记录错误日志"""
    with _log_lock:
        logger = get_logger()
        logger.error(message)
        if _stream_to_terminal:
            print(message)

def log_stream(content, end='', flush=False):
    """
    记录流式输出内容（不换行）
    
    Args:
        content: 要记录的内容
        end: 结束字符（默认不换行）
        flush: 是否立即刷新
    """
    with _log_lock:
        logger = get_logger()
        # 流式内容记录为 DEBUG 级别，不带时间戳前缀
        if logger.handlers:
            for handler in logger.handlers:
                if isinstance(handler, RotatingFileHandler):
                    # 直接写入文件，不通过格式化器
                    handler.stream.write(content)
                    if flush:
                        handler.stream.flush()
        
        # 终端输出（如果启用）
        if _stream_to_terminal:
            print(content, end=end, flush=flush)

def log_stream_line(content):
    """记录流式输出的一行内容（带换行）"""
    with _log_lock:
        logger = get_logger()
        logger.debug(content)
        if _stream_to_terminal:
            print(content)

