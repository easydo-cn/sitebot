# 站点机器人 #

## 什么是站点机器人？ ##

运行在EasyDo站点远端的任务执行机器人，一方面和站点建立连接，接受站点下达的脚本运行任务，另外一方面可以主机建立连接，在主机运行脚本


## 特色 ##

* 不需要在远端主机安装任何专有软件，只需要标准的python以及ssh即可
* 使用Fabric运行脚本，更灵活的控制
* 容易维护，免管理
    * 没有数据库
    * 傻终端，脚本统一从站点下载
* 提供监控后台
    * 任务重做
    * 日志查看
* 提供任务锁， 更好的任务调度
