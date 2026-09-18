from queue import Empty, Queue
from threading import Thread

from playwright.sync_api import Page


def wait_for_enter(page: Page, prompt: str):
    """Read terminal input off-thread while the main thread pumps Playwright events."""
    replies: Queue = Queue()

    def read_terminal():
        try:
            replies.put((input(prompt), None))
        except (EOFError, OSError) as exc:
            replies.put((None, exc))

    Thread(target=read_terminal, daemon=True).start()
    while True:
        try:
            reply, error = replies.get_nowait()
        except Empty:
            pages = [tab for tab in page.context.pages if not tab.is_closed()]
            if not pages:
                raise ValueError("Browser was closed before terminal input completed")
            # Playwright stays on the main thread; input() alone would suspend dispatch.
            pages[0].wait_for_timeout(100)
            continue
        if error is not None:
            raise error
        return reply
