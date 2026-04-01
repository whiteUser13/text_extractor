from collections import defaultdict
from threading import Lock

# 每个任务最多 5个任务，总共十个
TASK_RUNNING = defaultdict(int)
TASK_MAX_RUNNING = 5

_state_lock = Lock()

def can_run(task_id: str) -> bool:
    with _state_lock:
        return TASK_RUNNING[task_id] < TASK_MAX_RUNNING

def mark_start(task_id: str):
    with _state_lock:
        TASK_RUNNING[task_id] += 1

def mark_done(task_id: str):
    with _state_lock:
        TASK_RUNNING[task_id] -= 1
        if TASK_RUNNING[task_id] <= 0:
            del TASK_RUNNING[task_id]
