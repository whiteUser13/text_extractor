# scheduler/task_scheduler.py
import time
import threading
from concurrent.futures.process import BrokenProcessPool
from queue import Queue, Empty
from .executor import io_executor
from . import executor as executor_mod
from flask import current_app
from .download import update_download_task_status,get_sorted_pending_download_task,download_file_task
from .parse import update_parse_task_status,get_sorted_pending_parse_task,update_parse_task_msg

from .state import can_run, mark_start, mark_done
from .parse_port_state import release_port,get_parse_url,acquire_port
from utils import util_parse,task_folder_utils,file_utils
from pipeline import pipeline_manager as pm  # auto 分支新增



_scheduler_started = False
_scheduler_lock = threading.Lock()




def _run_task(task, executor, task_func):
    print("进入_run_task")
    task_id = task["task_id"]
    def wrapper():
        try:
            print("执行下载任务")
            task_func(task)
        finally:
            print("结束")
            mark_done(task_id)
    mark_start(task_id)
    executor.submit(wrapper)


def parse_task_entry(task, task_dirs, parse_json_path):
    task_id = task["task_id"]
    parse_id = task["parse_id"]
    file_name = task["file_name"]
    parse_style = task["select_type"]

    try:
        mineru_url, port = get_parse_url()
        result = util_parse.select_parse_style(
            parse_style,
            file_name,
            task_dirs,
            mineru_url,
            task_id
        )
    except Exception as e:
        result = {"success": False, "msg": f"解析过程出错 {e}"}
    finally:
        release_port(port)

    if result.get("success"):
        update_parse_task_msg(
            parse_json_path,
            task_id,
            parse_id,
            "parsed",
            result.get("msg", "解析成功"),
            result.get("used", "unknown")
        )
    else:
        update_parse_task_msg(
            parse_json_path,
            task_id,
            parse_id,
            "error",
            result.get("msg", "解析失败"),
            parse_style
        )
    print("文件名",file_name)
    return task_id
# def on_parse_done(fut, task_id):
#     try:
#         fut.result()  # 如有异常，这里会抛
#     except Exception as e:
#         current_app.logger.exception(
#             f"解析任务 {task_id} 进程异常"
#         )
#     finally:
#         mark_done(task_id)
#         print(f"✅ 解析任务 {task_id} 已完成")



def on_parse_done(fut, task_id,parse_id, parse_style, parse_json_path, app):
    """
    回调：必须用 app.app_context()，不能用 current_app（否则 Working outside of application context）
    同时捕获 BrokenProcessPool 并重建进程池。
    """
    with app.app_context():
        try:
            fut.result()  # 抛出子进程异常
        except BrokenProcessPool:
            app.logger.exception("解析任务 %s 失败：BrokenProcessPool（子进程崩溃/被杀）", task_id)
            # 重建 executor.py 里的全局 parse_executor
            # ✅ 子进程崩了，parse_task_entry 很可能没写状态，这里补写 error
            update_parse_task_msg(
                parse_json_path,
                task_id,
                parse_id,
                "error",
                "BrokenProcessPool: worker process crashed or was killed (likely OOM).",
                parse_style
            )

            executor_mod.rebuild_parse_executor(app)

        except Exception as e:
            app.logger.exception(f"解析任务 %s 进程异常{e}", task_id)
            # ✅ 其它异常同样补写 error（防止 parse_task_entry 未写入）
            update_parse_task_msg(
                parse_json_path,
                task_id,
                parse_id,
                "error",
                f"Parse failed in callback: {e}",
                parse_style
            )
        finally:
            mark_done(task_id)
            app.logger.info("✅ 解析任务 %s 已完成（已标记 done）", task_id)
def _scheduler_loop(app):
    """
    调度策略：
    1. 只有一类任务 → 全力跑这一类
    2. 两类都有 → 交替调度（公平）
    """
    app.logger.info("✅ 任务调度线程已启动")
    while True:
        try:
            parse_tasks = get_sorted_pending_parse_task()
            download_tasks = get_sorted_pending_download_task()  # 你已有：download / parse / user_id
            tasks = parse_tasks + download_tasks
            print("tasks",tasks)
            for task in tasks:
                task_id = task["task_id"]
                task_type = task["type"]
                #  任务并发限制
                if not can_run(task_id):
                    continue
                if task_type == "download":
                    area_code = task["area_code"]
                    update_download_task_status(task["task_id"], area_code,"downloading")
                    _run_task(task, io_executor, download_file_task)
                elif task_type == "parse":
                    parse_id = task["parse_id"]
                    task_dirs = task_folder_utils.get_task_dirs(task_id)
                    parse_json_path = file_utils.get_parse_task_json_path()

                    update_parse_task_status(task_id, parse_id, "parsing")
                    mark_start(task_id)

                    future = executor_mod.parse_executor.submit(
                        parse_task_entry,
                        task,
                        task_dirs,
                        parse_json_path
                    )

                    future.add_done_callback(
                        lambda f,
                               tid=task_id,
                               pid=parse_id,
                               ps="未知",
                               pjp=parse_json_path,
                               app=app: on_parse_done(f, tid, pid, ps, pjp, app)
                    )

            # auto 分支新增：pipeline 任务调度
            _dispatch_pipeline_jobs(app)

        except Exception as e:
            app.logger.info(f"任务线程异常:{e}")
        time.sleep(5)




def _dispatch_pipeline_jobs(app):
    """
    auto 分支新增：从 pipeline_tasks.json 取出 pending 任务，
    提交到 parse_executor（ProcessPool）执行完整流水线。
    task_dirs 在主线程（app context 内）预先计算后传入子进程，避免子进程访问 Flask current_app。
    每次最多拉取 5 条。
    """
    try:
        pending_jobs = pm.get_pending_jobs(limit=5)
        for job in pending_jobs:
            task_id = job["task_id"]
            job_id = job["job_id"]
            if not can_run(task_id):
                continue

            # 在 app context 内计算任务目录，供子进程使用
            task_dirs = task_folder_utils.get_task_dirs(task_id)

            pm.mark_running(task_id, job_id)
            mark_start(task_id)

            from pipeline.runner import run_pipeline_job
            future = executor_mod.parse_executor.submit(run_pipeline_job, job, task_dirs)

            future.add_done_callback(
                lambda f, tid=task_id, jid=job_id, a=app: _on_pipeline_done(f, tid, jid, a)
            )
    except Exception as exc:
        app.logger.warning(f"[pipeline] dispatch 异常: {exc}")


def _on_pipeline_done(fut, task_id: str, job_id: str, app):
    """pipeline job 完成回调"""
    with app.app_context():
        try:
            fut.result()
        except BrokenProcessPool:
            app.logger.exception("[pipeline] BrokenProcessPool, job=%s", job_id)
            pm.mark_failed(task_id, job_id, "BrokenProcessPool: worker crashed")
            executor_mod.rebuild_parse_executor(app)
        except Exception as exc:
            app.logger.exception("[pipeline] job %s 异常: %s", job_id, exc)
            pm.mark_failed(task_id, job_id, str(exc))
        finally:
            mark_done(task_id)


def start_scheduler(app):
    """保证调度线程只启动一次"""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return

        # auto 分支新增：启动时恢复崩溃前 running 的 pipeline job
        try:
            pm.recover_stale_running_jobs()
        except Exception as exc:
            app.logger.warning(f"[pipeline] 崩溃恢复失败（可忽略）: {exc}")

        def runner():
            with app.app_context():
                try:
                    _scheduler_loop(app)
                except Exception as e:
                    app.logger.exception("Scheduler crashed")
        t = threading.Thread(target=runner, daemon=True)
        t.start()
        _scheduler_started = True
