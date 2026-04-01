def start_scheduler(app):
    global _scheduler_started
    from scheduler.scheduler import _scheduler_loop

    with _scheduler_lock:
        if _scheduler_started:
            return

        def runner():
            with app.app_context():
                _scheduler_loop()

        t = threading.Thread(target=runner, daemon=True)
        t.start()
        _scheduler_started = True
