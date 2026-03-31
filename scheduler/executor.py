from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
import os
import threading

IO_WORKERS = 10
CPU_WORKERS = max(1, int(os.cpu_count() / 2))

io_executor = ThreadPoolExecutor(max_workers=IO_WORKERS)
parse_executor = ProcessPoolExecutor(max_workers=CPU_WORKERS)

_parse_lock = threading.Lock()


def rebuild_parse_executor(app=None):
    """
    BrokenProcessPool 后重建 parse_executor。
    注意：这里重建的是本模块的全局 parse_executor，scheduler.py 通过 executor_mod.parse_executor 使用它。
    """
    global parse_executor
    with _parse_lock:
        try:
            parse_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            if app:
                app.logger.exception("关闭旧 parse_executor 失败（可忽略）")
        parse_executor = ProcessPoolExecutor(max_workers=CPU_WORKERS)
        if app:
            app.logger.warning("✅ parse_executor 已重建 max_workers=%s", CPU_WORKERS)
