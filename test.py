import signal
import time

def time_limit(set_time):
    def wraps(func):
        def handler(*args, **kwargs):
            raise RuntimeError()

        def deco(*args, **kwargs):
            signal.signal(signal.SIGALRM, handler)
            signal.alarm(set_time)
            res = func(*args, **kwargs)
            signal.alarm(0)
            return res
        return deco
    return wraps

@time_limit(3)
def test():
    print 'a'
    time.sleep(4)
    print 'b'

test()
