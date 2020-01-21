import signal
import time

def handler(*args, **kwargs):
    raise RuntimeError

signal.signal(signal.SIGALRM, handler)
signal.alarm(3)
exec "time.sleep(5)"
signal.alarm(0)

