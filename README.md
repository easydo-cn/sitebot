# 站点机器人 #

## 站点机器人是什么？ ##

很多事情在浏览器里不能做，比如编辑本地文件，或是上传文件夹、上传非常大的文件等等。
站点机器人就是为了将云端（浏览器）和本地打通的一个工具。

本质上，站点机器人是一个安装在用户机器上的服务器。因为安装在用户机器上，所以可以访问和操作本地的数据。
浏览器中的页面通过与这个服务器的 API 进行通信，就可以实现很多浏览器里做不到的事情。

## 项目结构 ##

* 程序入口文件是 `main.py`，在其中初始化服务器和托盘图标
* `worker.py` 是任务管理模块
* 实际执行具体工作的 worker 存放在 `workers` 目录中，模块名与主函数名相同
* worker 模块最终都会被导入到顶层使用，所以可以直接在 worker 中导入顶层的模块
* worker 中用到的代码较多的类或者模块可以存放在顶层目录中，例如 `editor.py` 是外部编辑器。
* `tests` 目录中存放的是单元测试代码，使用 `nosetests -w tests` 来运行测试

## 国际化 ##

* 需要更新字符串时请使用
```python
pybabel extract -F babel.cfg --no-location -o translations\messages.pot --sort-output --omit-header .
```
命令抽取字符串（选项含义：去掉字符串来源注释并将输出排序）
* 然后使用 `pybabel update -i translations\messages.pot -d translations` 更新 po 文件
* 最后翻译 po 文件并提交修改
* 在打包时会自动调用 `pybabel compile -d translations` 编译 po 文件到 mo 格式，开发中需要测试国际化时请手工执行此命令

## worker 模块的编写 ##

### worker ###

一个 worker 就是执行一个具体任务的模块，存放在 `workers` 目录中。
以文件下载 worker 为例，模块存放在 `workers/download.py`，主函数是同名的 `download`：
每个 worker 的主函数都会被映射到站点机器人服务器的 `/worker/new/{worker 名字}` 这个接口上。
带参数请求这个接口就可以新建相应的任务，任务添加后会自动开始运行。

为了不阻塞服务器，任务新建成功之后（即 worker 相关的数据存储好之后）会马上返回简单的信息，包括任务 ID、任务当前是否在运行等状态。

每个 worker 在运行时，是把主函数作为一个独立的进程来执行的。

### worker 的数据存储 ###

每个 worker 在启动时由任务管理模块从对应的 workerdb 中读取参数值，传递给 worker 的主函数。
worker 也可以往自己的 workerdb 中写入其他的数据。workerdb 是在顶层目录的 `workerdb.py` 模块中定义的。

从任务管理模块导入 `get_worker_db` 就可以用于获取对应 ID 的 workerdb：

```python
from worker import get_worker_db
worker_db = get_worker_db(id)

# workerdb 继承于 dict 类，就像字典那样操作就可以
for k in worker_db.keys():
    print '{}: {}'.format(k, worker_db[k])

# 往里面写入数据
worker_db['test'] = id
# 调用 sync 才能持久化（保存到文件）
worker_db.sync()
```

每个 worker 对应的 workerdb 会按 ID存放在 `APP_DATA/workers` 中，例如 ID 为 1 的 worker 存放在 `APP_DATA/workers/1.db`。
目前 workerdb 使用的是 JSON 序列化/反序列化。

### worker 的调试 ###

`utils` 模块中包含了一些实用的工具函数，其中 `get_worker_logger` 可以按 ID 获取 woker 的日志记录器：

```python
from utils import get_worker_logger

# worker 日志使用轮换日志处理器，限制文件大小默认为 100Kb，可以手动指定这个大小
# 超过限制后只保留一个备份文件。
logger = get_worker_logger(id, size=1000)
logger.debug(u'测试日志: %d', id)
```

worker 的日志文件按 ID 存放在 `APP_DATA/logs/worker_{ID}.log` 文件中。
如果以源码状态运行站点机器人的话，所有日志都会同时输出到日志文件和终端里。


```python
# encoding: utf-8

import os
import sys

import ui_client
from utils import get_wo_client, is_valid_dir
from worker import register_worker


# 定义主函数
# 任务管理模块会对各个 worker 进行唯一签名计算，用于防止同一个 worker 同时运行多次
# 所有非可选参数都会参与这个 worker 的唯一签名计算，例如一个下载 worker 的签名如下
# download(1, server=http://wo-api.everydo.cn, oc_server=http://oc-api.everydo.cn, account=zopen, instance=default, token=3e2954f2a1df19b00000000000000000, uid=[u'789596436'], path=[u'D:\\\u65b0\u5efa\u6587\u4ef6\u5939'])
def download(worker_id, server, oc_server,
             account, instance, token, uid,
             path, revisions=None, pid=None,
             pipe=None):
    '''下载'''
    # Doc string 会作为任务名字，请精简描述，不要换行

    ...
    具体业务逻辑
    ...

    # 可以返回一些可以被 JSON 序列化的信息，例如下载的文件列表
    return downloaded

# 将主函数注册到任务管理模块中
register_worker(download)


# 可以在交互模式下测试一下主函数（不是必须的）
def test():
    '''
    Standalone test case.
    需要一个有效的 worker 数据库
    这个测试还不完善（会引发 HTTP 异常）
    '''
    pass

if __name__ == '__main__':
    test()

```


注册 worker 之后，就可以通过 `/worker/new/{worker 名字}` 这个接口来调用了。
