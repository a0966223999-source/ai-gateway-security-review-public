"""Distinguish an actual OS maintenance signal from unexpected cancellation."""
import asyncio
import signal


async def run_until_signalled(serve, args):
    task = asyncio.create_task(serve(args))
    loop = asyncio.get_running_loop()
    received = None

    def stop(signum):
        nonlocal received
        if received is None:
            received = signum
            task.cancel()

    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop, signum)
    try:
        await task
    except asyncio.CancelledError:
        if received is None:
            raise
        # serve's finally blocks have finished before reporting maintenance.
        return 128 + int(received)
    finally:
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)
    return 0
