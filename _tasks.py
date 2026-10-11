"""Completion ownership that survives executor submission raising after enqueue."""


def result_owned(ticket):
    """Turn a stored background BaseException into an ordinary cleanup error.

    Main-thread interrupts outside this receipt lookup retain their normal
    semantics; only an exception already recorded by invoke is converted.
    """
    error = ticket.exception()
    if error is not None and not isinstance(error, Exception):
        raise RuntimeError('Background task raised ' + type(error).__name__ + ': ' + str(error)) from error
    return ticket.result()


def submit_owned(executor, ticket, function, *args):
    """The caller publishes ticket before entering this function."""
    call = [function, args]

    def discard_cancelled(done):
        if done.cancelled():
            # The executor may retain an unreturned queued wrapper. Release its
            # large inputs without reaching into the executor's private queue.
            call.clear()

    ticket.add_done_callback(discard_cancelled)

    def invoke():
        # A queued wrapper can outlive a thrown submit without entering work.
        if not ticket.set_running_or_notify_cancel():
            return
        operation, arguments = call
        call.clear()
        try:
            result = operation(*arguments)
        except BaseException as exc:
            ticket.set_exception(exc)
        else:
            ticket.set_result(result)

    try:
        executor.submit(invoke)
    except BaseException:
        # If already running, the same ticket continues observing completion.
        ticket.cancel()
        raise
