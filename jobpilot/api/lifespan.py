"""Application lifespan: start the background loops (workers, mail, outbox, bots) and stop them in order."""

import asyncio


async def lifespan(app):
    service = app.state.service
    config, db = service.config, service.db
    bot = None
    scheduler_task = None
    if config.enable_workers:
        from ..workers import start, monitor_scheduler

        await start(service)
        scheduler_task = asyncio.create_task(monitor_scheduler(service))
    # The bot starts once its token and user ID are set, also when they are saved in Setup > Connections later.
    bot = asyncio.create_task(keep_telegram(service))
    from ..telegram_workers import poll_all

    worker_bots = asyncio.create_task(poll_all(service))
    mail_task = asyncio.create_task(service.mail.poll())
    tab_task = None
    if config.enable_workers and config.browser_cdp_url and config.tab_cleanup_seconds:
        from ..operations.close_tabs import watch as watch_tabs

        tab_task = asyncio.create_task(watch_tabs(db, config.browser_cdp_url, config.tab_cleanup_seconds))
    from ..outbox import poll as poll_outbox

    service.outbox_running = True
    outbox_task = asyncio.create_task(poll_outbox(service))
    load_task = None
    if config.enable_workers:
        from ..host_health import watch as watch_host

        # Samples how late the event loop runs; the gates in host_health read it (docs/forensics, 4.2).
        load_task = asyncio.create_task(watch_host(db))
    yield
    if config.enable_workers and config.shutdown_drain_seconds:
        # First of all, while the workers, outbox and mail poller still run: a stop must not cut a commit.
        await service.drain(config.shutdown_drain_seconds)
    if load_task:
        load_task.cancel()
        await asyncio.gather(load_task, return_exceptions=True)
    outbox_task.cancel()
    await asyncio.gather(outbox_task, return_exceptions=True)
    service.outbox_running = False
    if scheduler_task:
        scheduler_task.cancel()
        await asyncio.gather(scheduler_task, return_exceptions=True)
    mail_task.cancel()
    await asyncio.gather(mail_task, return_exceptions=True)
    if tab_task:
        tab_task.cancel()
        await asyncio.gather(tab_task, return_exceptions=True)
    if bot:
        bot.cancel()
        await asyncio.gather(bot, return_exceptions=True)
    worker_bots.cancel()
    await asyncio.gather(worker_bots, return_exceptions=True)
    from ..desktop_cdp import disconnect_browsers

    await disconnect_browsers()
    if config.enable_workers:
        from ..workers import stop

        await asyncio.to_thread(stop)
    pending_notifications = list(service.notification_tasks)
    for task in pending_notifications:
        task.cancel()
    await asyncio.gather(*pending_notifications, return_exceptions=True)
    db.engine.dispose()


async def keep_telegram(service, interval=15):
    """Run the Telegram bot whenever it is configured; restart it if it stops."""
    from ..telegram import configured, poll

    task = None
    try:
        while True:
            if (task is None or task.done()) and configured(service.config):
                task = asyncio.create_task(poll(service))
            await asyncio.sleep(interval)
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
