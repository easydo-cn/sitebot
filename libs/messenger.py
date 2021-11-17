# coding: utf-8
import time
import json
from logging import (
    INFO as logging_INFO,
)
import threading
from urlparse import urlparse

import certifi
import paho.mqtt.client as mqtt
from edo_client import get_client
from edo_client.error import ApiError

import ui_client
from utils import (
    translate as _, get_editing_worker, get_logger,
)
from config import MSG_QOS, MSG_KEEPALIVE, COMMAND_CATEGORY
# from worker import get_worker_logger, get_worker_db

RECONNECT_DELAY = 10
MAX_FULL_FAIL = 10


class ZopenMQTTClient(mqtt.Client):
    '''Customized MQTT client'''
    zopen_topic_sys = 'msgcenter'

    def __init__(
        self, client_id='', clean_session=True, userdata=None,
        protocol=mqtt.MQTTv311, transport='websockets'
    ):
        super(ZopenMQTTClient, self).__init__(
            client_id=client_id, clean_session=clean_session,
            userdata=userdata, protocol=protocol, transport=transport
        )
        self.connected = False

    def setup_connection(self, host, port, topic, will, qos=MSG_QOS, ssl=False):
        """设置必要的连接参数"""
        self._zopen_host = host
        self._zopen_port = port
        self._zopen_qos = qos
        self._zopen_topic = topic
        self._zopen_ssl = bool(ssl)
        self._zopen_will = will

    def post_init(self):
        '''Post-init: to be called right after client inited'''
        if not all([
            self._zopen_host, self._zopen_port,
            self._zopen_topic, self._zopen_will
        ]):
            raise ValueError(
                u'Should invoke ZopenMQTTClient.setup_connection before connecting MQTT broker'
            )

        if self._zopen_ssl:
            self.tls_set(certifi.where())

        self.will_set(
		    topic=self.zopen_topic_sys,
		    payload=self._zopen_will,
		    qos=self._zopen_qos,
		    retain=False
		)

        self.connect(self._zopen_host, port=self._zopen_port, keepalive=30)
        self.subscribe(self._zopen_topic, qos=MSG_QOS)

    def on_connect(self, mqttc, userdata, flags, return_code):
        parent = userdata['ref']
        # 订阅自己的 topic
        parent.logger.debug(
            u'已经连接到 MQTT 服务器，返回码 %d，字符表示: %s，flags：%s',
            return_code, mqtt.connack_string(return_code), flags
        )
        self._zopen_subscribed_qos = -1
        self.connected = True

    def on_message(self, mqttc, userdata, mqtt_msg):
        try:
            payload = json.loads(mqtt_msg.payload)
            userdata['ref'].handle_json_message(
                userdata, payload,
                topic=mqtt_msg.topic
            )
        except:
            userdata['ref'].logger.info(
                u'在处理消息时发生异常，消息：%s',
                mqtt_msg.payload, exc_info=True
            )

    def on_subscribe(self, mqttc, userdata, mid, granted_qoses):
        # 发送上线消息
        parent = userdata['ref']

        parent.logger.debug(
            u'Granted QoS-es: %s for subscription: %s with mid of %d',
            granted_qoses, self._zopen_topic, mid
        )
        # Shrotcut
        FAIL = parent.SUBSCRIPTION_FAILED

        # Retry subscription for one time at max
        if granted_qoses[0] == FAIL:
            if self._zopen_subscribed_qos == FAIL:
                parent.logger.warn(
                    u'两次订阅 %s 失败，尝试重连', self._zopen_topic
                )
                parent.full_fail_count += 1
                parent.reapply_required = True
                # Parent will reinit this client and retry connection
                self.disconnect()
            else:
                result, _mid = self.subscribe(
                    self._zopen_topic, qos=self._zopen_qos
                )
                # Retry subscription one more time
                if result == mqtt.MQTT_ERR_SUCCESS:
                    self._zopen_subscribed_qos = FAIL
                else:
                    parent.logger.warn(
                        u'第二次订阅 %s 失败，尝试重连', self._zopen_topic
                    )
                    parent.full_fail_count += 1
                    parent.reapply_required = True
                    # Parent will reinit this client and retry connection
                    self.disconnect()
        else:
            self._zopen_subscribed_qos = granted_qoses[0]
            parent.send_connection_msg(True, userdata)

    def on_disconnect(self, mqttc, userdata, return_code):
        self.connected = False
        # 重新连接
        parent = userdata['ref']
        parent.logger.info(
            u'消息客户端已经断开连接，返回码：%d，字符表示: %s',
            return_code, mqtt.error_string(return_code)
        )
        parent.online = False
        parent.reapply_required = True


class Messaging(threading.Thread):
    SUBSCRIPTION_FAILED = 128
    action_translations = {
        'share': _('shared'),
        'edit': _('edited'),
        'new': _('created'),
        'upload': _('upload'),
        'comment': _('commented'),
        'remind': _('reminded'),
        'new_revision': _('updated revision'),
        'fix_revision': _('fixed revision'),
        'workflow_sign': _('signed workflow'),
        'publish': _('published'),
        'workflow_resign': _('resigned workflow'),
        'mention_in_comment': _('mentioned you in comment'),
        'mention_in_group': _('mentioned you in group'),
    }

    def __init__(
        self, oc_server, account, instance, token, pid,
        username=None, instance_name=None, instance_url=None, connection_id=None,
        clean_session=False, use_ssl=False, qos=MSG_QOS, keepalive=MSG_KEEPALIVE,
        stop_event=None, notification=False
    ):
        self.oc_server = oc_server
        self.account = account
        self.instance = instance
        self.token = token
        self.pid = pid
        super(Messaging, self).__init__()

        self.clean_session = clean_session
        self.use_ssl = use_ssl
        self.qos = qos
        self.keepalive = keepalive
        self.__notification = notification
        self.logger = get_logger('message|{}|{}'.format(self.pid, self.oc_server))

        self.last_sync = time.time()
        self.last_sync_from = None
        self.enabled = True
        self.current_conversation = None

        if not username:
            # FIXME Query user title from OC server
            pass

        self.username = username

        if not all([instance_name, instance_url]):
            # FIXME Query instance title and instance URL from OC server
            pass

        self.instance_name = instance_name
        self.instance_url = instance_url
        self.connection_id = connection_id

        self.stop_event = stop_event or threading.Event()

        self._online = False

        self.wo_client = None
        self.message_client = None
        self.client = None  # RTCClient

        # 完全重试的失败次数
        # 每次成功连接就归零，超过 10 次直接抛出最后一次的原始错误，任务停止
        self.full_fail_count = 0

        self.COMMAND_CHANNEL = '<>'.join([COMMAND_CATEGORY, self.pid])
        self.subscriptions = {}

        # Unread calculation
        self.notify_unreads = {}
        # init with all 0
        self.conversations = []

        self.reapply_required = False

        self.logger.debug(u'消息客户端初始化完成')
        self.previous_msg_errors = []

        self.logger.debug(u'消息线程初始化完成')
        self.__unread_count = 0

    @property
    def unread_count(self):
        return self.__unread_count

    @unread_count.setter
    def unread_count(self, count):
        self.__unread_count = count

    def toggle_notify(self, enable):
        """切换提醒"""
        self.__notification = enable

    def connect(self):
        # 启动消息线程
        if not self.is_alive():
            self.start()
            return False
        else:
            return True

    def disconnect(self):
        # 停止消息线程
        if self.is_alive():
            self.stop_event.set()

    @property
    def state(self):
        return "online" if self.online else "offline"

    def apply(self):
        '''
        申请连接，获取 MQTT 服务器信息，为 MQTT 连接做准备
        '''
        self.online = False
        CONNECT_FAILS = 0
        CONNECT_FAIL_LIMIT = 4
        DELAY_TIME = 10
        while 1:
            if self.stop_event.is_set():
                self.logger.debug(u'停止申请连接')
                return False

            try:
                if not all([self.wo_client, self.message_client]):
                    self.wo_client = get_client(
                        'workonline', self.oc_server,
                        self.account, self.instance, token=self.token
                    )
                    self.message_client = get_client(
                        'message', self.oc_server,
                        self.account, self.instance, token=self.token
                    )

                if CONNECT_FAILS > 0:
                    self.logger.info(u'第 %s 次重试连接申请', CONNECT_FAILS)
                else:
                    self.logger.debug(u'开始申请连接')
                connection_data = self.message_client.message.connect()
                break
            except ApiError as e:
                self.logger.warn(u'申请连接时出错', exc_info=True)
                if e.code == 401:
                    # Server might not be ready, wait for a few seconds
                    self.stop_event.wait(DELAY_TIME)
                    ui_client.message(
                        _('Messaging'),
                        _('Authentication failed') +
                        _('You should manually turn on messaging on {}: {}').format(  # noqa
                            self.instance_name,
                            self.instance_url
                        ),
                        type='info'
                    )
                    # token 错误，提示用户，并删除这个站点连接
                    ui_client._request_api(
                        '/admin/connections',
                        kw={'connection_id': self.connection_id, 'action': 'remove'},
                        internal=True
                    )
                    return False

                CONNECT_FAILS += 1
                if CONNECT_FAILS >= CONNECT_FAIL_LIMIT:
                    self.logger.error(
                        u'连续 %s 次申请连接失败, 耗时 %s 秒',
                        CONNECT_FAILS, CONNECT_FAILS * DELAY_TIME,
                        exc_info=True
                    )
                    return False
                self.stop_event.wait(DELAY_TIME)
            except:
                self.logger.warn(u'申请连接时出错', exc_info=True)
                self.stop_event.wait(DELAY_TIME)

        self.logger.debug(
            u'从消息中心获取到的连接凭证：%s',
            json.dumps(connection_data, indent=4)
        )
        exceeded_instances = connection_data.get('exceeded_instances', [])
        expired_instances = connection_data.get('expired_instances', [])
        # 第一次才提示，之后不提示
        if self.instance in exceeded_instances:
            msg_error = 'site_exceeded'
            if msg_error not in self.previous_msg_errors:
                ui_client.message(
                    _('Messaging'),
                    _('Failed to connect, online user count exceeded limit of site license')  # noqa
                )
                self.previous_msg_errors.append(msg_error)
            self.stop()
            return False
        if self.instance in expired_instances:
            msg_error = 'site_expired'
            if msg_error not in self.previous_msg_errors:
                ui_client.message(
                    _('Messaging'),
                    _('Failed to connect, license for this site has expired')
                )
                self.previous_msg_errors.append(msg_error)
            self.stop()
            return False

        self.use_ssl = connection_data.get('use_ssl', self.use_ssl)
        broker = connection_data['broker']
        if "://" not in broker:
            broker = "{}://{}".format("https" if self.use_ssl else "http", broker)
        parse_result = urlparse(broker)
        self.mqtt_host = parse_result.hostname
        self.mqtt_port = parse_result.port

        if not self.mqtt_port:
            self.mqtt_port = 443 if self.use_ssl else 80

        self.logger.debug(
            u'MQTT 服务器: %s, 端口: %s, %s使用 SSL',
            self.mqtt_host, self.mqtt_port,
            (u'' if self.use_ssl else u'不')
        )
        self.userdata = connection_data
        self.userdata.update({
            'user_id': self.pid,
            'account': self.account,
            'instance': self.instance,
        })
        return True

    def init_client(self):
        self.userdata.update({'ref': self})
        if self.client is None:
            self.client = ZopenMQTTClient(
                client_id=self.userdata['client_id'],
                userdata=self.userdata,
                clean_session=self.clean_session,
                protocol=mqtt.MQTTv31
            )
            self.logger.info(u'MQTT 客户端初始化完成')
        else:
            self.client.reinitialise(
                client_id=self.userdata['client_id'],
                userdata=self.userdata,
                clean_session=self.clean_session
            )
            self.logger.info(u'MQTT 客户端重置完成')
        self.client.setup_connection(
            qos=self.qos,
            topic=self.userdata['topics'][self.instance],
            ssl=self.use_ssl,
            will=json.dumps(
                self.gen_connection_msg(self.userdata, False)
            ),
            host=self.mqtt_host,
            port=self.mqtt_port
        )

    def run(self):
        RETRY_DELAY = 60  # 消息连接出错或失败，重试的时间间隔
        while 1:
            if self.stop_event.is_set():
                self.logger.debug(u'消息线程将停止')
                self.stop()
                break

            try:
                if not self.apply():
                    if self.stop_event.is_set():
                        self.logger.debug(u'消息线程将停止')
                        self.stop()
                        break
                    self.logger.warn(u'申请连接失败, 将在 60 秒后重试')
                    self.stop_event.wait(timeout=RETRY_DELAY)
                    continue
                self.reapply_required = False
            except:
                self.logger.warn(u'申请连接出错, 将在 60 秒后重试', exc_info=True)
                self.stop_event.wait(timeout=RETRY_DELAY)
                continue

            try:
                if self.client is None or not self.client.connected:
                    self.logger.debug(u'客户端未初始化或未连接')
                    # 有时需要重新申请
                    if self.reapply_required:
                        if not self.apply():
                            self.logger.warn(u'连接中断，并且重新申请失败，将在 60 秒后重试', exc_info=True)
                            self.stop_event.wait(timeout=RETRY_DELAY)
                            continue
                    self.reapply_required = False
                    self.init_client()
            except:
                self.online = False
                self.logger.error(
                    u'保持网络循环时出错, 已失败 %d 次',
                    self.full_fail_count, exc_info=True
                )
                if self.full_fail_count >= MAX_FULL_FAIL:
                    self.logger.debug(
                        u'错误次数超过 %s', MAX_FULL_FAIL, exc_info=True
                    )

                self.full_fail_count += 1
                self.logger.debug(
                    u'客户端初始化失败, 将在 %s 秒后重连', RECONNECT_DELAY
                )
                self.stop_event.wait(RECONNECT_DELAY)
            else:
                # We might be offline, and self.client.post_init will raise error
                try:
                    self.client.post_init()
                except:
                    self.logger.exception("post_init failed")
                    self.stop_event.wait(timeout=RETRY_DELAY)
                    continue
                else:
                    while not self.stop_event.is_set():
                        if self.reapply_required:
                            self.logger.warning(
                                u"需要重新申请连接，%d 秒后开始", RECONNECT_DELAY
                            )
                            self.stop_event.wait(timeout=RECONNECT_DELAY)
                            break
                        else:
                            self.client.loop(timeout=1.0)

                    if self.stop_event.is_set():
                        self.logger.debug(u'消息线程将停止')
                        self.stop()
                        break

    @property
    def online(self):
        return (
            self.client is not None and self.client.connected and self._online
        )

    @online.setter
    def online(self, online):
        self._online, online = online, self._online

    def stop(self):
        self.online = False
        self.reapply_required = True
        if getattr(self, 'client', None) is not None:
            # 似乎我们现在使用的 MQTT 服务端有 bug，导致以 QoS 为 1 第二次发送的连接消息
            # 无法收到 PUBACK，会使得 wait_for_publish 一直阻塞，使得消息线程无法结束
            # QoS 为 0 表示只发送一次
            msginfo = self.client.publish(
                topic=self.client.zopen_topic_sys,
                payload=json.dumps(
                    self.gen_connection_msg(self.userdata, self.online)
                ),
                qos=0, retain=False
            )
            msginfo.wait_for_publish()
            self.client.on_disconnect = lambda c, u, r: None
            self.client.disconnect()
            self.client = None

    def get_target_user(self, channel_name):
        '''
        从会话 channel 名字中获取对方名字
        '''
        target_user = None
        for i in channel_name.split('<>'):
            if i != self.pid:
                target_user = i
                break
        return target_user

    def check_unreads(self):
        '''上线后处理未读相关的事情'''
        # 查询未读数据
        unreads = self.message_client.message.unread_stat()
        commands = {}
        self.logger.debug(
            u'查询到的未读: \n\t%s',
            '\n\t'.join(['{}: {}'.format(*kv) for kv in unreads.items()])
        )
        # 更新未读数
        for channel in unreads.keys():
            channel_type, channel_name = channel.split(':')
            if ',' in channel_name:
                targets = channel_name.split(',')
            else:
                targets = channel_name.split('<>')
            # 通知频道
            if channel_type == 'notify':
                # 命令
                if targets[0] == COMMAND_CATEGORY:
                    commands.update({channel: unreads[channel]})
                    continue
                else:
                    # 普通通知
                    self.notify_unreads.update({
                        targets[0]: unreads[channel].get('count', 0)
                    })
            # 私聊
            elif channel_type == 'private':
                target_user = self.get_target_user(channel_name)
                if target_user is None:
                    self.logger.warn(
                        u'无法确定私聊未读的对方用户: %s | %s',
                        channel, unreads[channel]
                    )
                if unreads[channel].get('count', 0) > 0\
                        and target_user not in self.conversations:
                    self.conversations.append(target_user)
            # 群聊
            elif channel_type == 'group':
                if unreads[channel].get('count', 0) > 0\
                        and targets[0] not in self.conversations:
                    self.conversations.append(targets[0])
        # 更新菜单项
        self.update_unread_count()

        assert len(commands) in (0, 1, )
        # 如果有命令消息，查询出来
        for channel in commands:
            command_msgs = self.message_client.message.query(
                time_start=commands[channel]['time_start'],
                event_name=COMMAND_CATEGORY,
                channel_type=COMMAND_CATEGORY,
                channel_name=channel.split(':')[1]
            )
            for command_msg in command_msgs:
                self.handle_command_message(command_msg)

    def handle_json_message(self, userdata, msg, topic=None):
        '''处理 JSON 消息'''
        # 连接消息：
        # 上线消息：已经连接
        # 下线消息：断开重连 / 连接失败（没有许可）
        # 业务消息：消息提示
        if not msg.get('event_data', None) or not msg.get('event_name'):
            return
        if msg.get('user_id', userdata['user_id']) != userdata['user_id']:
            return

        if msg['event_name'] == 'connection':
            # 严格区分不属于这个客户端的连接消息
            # 以下两种连接消息都属于这个客户端：
            # 客户端 ID 严格匹配，或消息是对所有客户端群发的（没有客户端 ID）
            to_me = msg.get('client_id', userdata['client_id']) == userdata['client_id']
            self.logger.debug(u'收到发送给%s客户端的连接消息', u'当前' if to_me else u'其他')
            if to_me:
                return self.handle_connection_msg(msg)
        elif msg['event_name'] == 'mark_read':
            return self.handle_mark_read_message(msg)
        elif msg['event_name'] == 'notify':
            self.handle_notification(msg)
        elif msg['event_name'] == 'chat':
            self.handle_chat_message(msg)
        elif msg['event_name'] == 'command':
            # Double check to make sure this command was sent to current user
            event_data = msg['event_data']
            if isinstance(event_data['channel_name'], (list, )):
                channel_names = event_data['channel_name']
            else:
                channel_names = [event_data['channel_name']]
            if self.COMMAND_CHANNEL in channel_names:
                return self.handle_command_message(msg)
        elif msg['event_name'] == 'multiuser_edit':
            return self.handle_editing_message(msg)
        else:
            self.logger.debug("received %s message", msg['event_name'])

    def handle_editing_message(self, msg):
        '''
        处理共享编辑的版本更新消息
        通过调用 /internal/edit/update_revision，通知编辑任务去下载新版本
        '''
        # FIXME 暂时不开放共享编辑
        return
        event_data = msg['event_data']
        target_workerdb = get_editing_worker(uid=event_data['uid'])
        # debugging only
        self.logger.debug(u'找到对应正在运行的外部编辑任务: %s', target_workerdb)
        if target_workerdb is not None:
            target_workerdb['update'] = {
                'revision': event_data['revision'],
                'md5': event_data['md5'],
            }
            target_workerdb.sync()

    def handle_command_message(self, msg):
        '''处理指令消息'''
        # TODO 在 online_script 任务里使用代码签名来验证脚本
        now = time.time()
        self.logger.debug(u'指令消息：%s', msg)
        # 兼容 query 接口查询到的消息，以及实时通道推送的消息
        event_data = msg.get('event_data', msg)

        # 标记已读
        msg_time = msg.get('timestamp', time.time())
        self.message_client.message.mark_read(
            'notify', msg_time, category=COMMAND_CATEGORY
        )
        # 过期任务不执行
        # FIXME 服务器时区可能与本地时区不一致，服务端应当统一返回 UTC 时间戳，本地也需要使用 UTC 时间来计算时间戳
        command_ttl = event_data.pop('ttl', 0)
        command_expired = True
        if command_ttl == 0 or now <= msg_time + command_ttl:
            command_expired = False
        if command_expired:
            self.logger.info(
                u'指令消息已经过期 (过期时间戳 %s，当前 %s)', msg_time + command_ttl, now
            )
            return
        script_name = event_data.pop('script_name', None)
        if not script_name:
            self.logger.warn(u'指令消息不包含任何脚本：%s', msg)
            return

        # 创建任务
        from_user = event_data.pop('from', {})
        from_user_id = from_user.get('id', None)
        worker_info = {
            'name': 'online_script',
            'token': self.token,
            'pid': self.pid,
            'username': self.username,
            'message_server': self.message_client.api_host,
            'account': self.account,
            'instance': self.instance,
            'oc_server': event_data.get('oc_server', self.oc_server),
            'script_name': script_name,
            'timeout': event_data.pop('timeout', None),
            'callback_url': event_data.pop('callback_url', None),
            'error_callback_url': event_data.pop('error_callback_url', None),
            'return_script': event_data.pop('return_script', None),
            'return_params': json.dumps(event_data.pop('return_params', None)),
            'error_script': event_data.pop('error_script', None),
            'error_params': json.dumps(event_data.pop('error_params', None)),
            'progress_script': event_data.pop('progress_script', None),
            'progress_params': json.dumps(event_data.pop('progress_params', None)),
            'progress_level': json.dumps(event_data.pop('progress_level', None)),
            'report_to_pid': from_user_id,
            'args': json.dumps(event_data.pop('args', [])),
            'kw': json.dumps(event_data.pop('kw', {})),
        }
        response = ui_client.new_worker(worker_info).json()
        return response

    def handle_notification(self, msg):
        '''处理通知'''
        # 更新未读数并更新菜单项内容
        # NOTICE 现在命令频道不参与未读数计算
        event_data = msg['event_data']
        if isinstance(event_data['channel_name'], (list, )):
            channel_names = event_data['channel_name']
        else:
            channel_names = [event_data['channel_name']]
        category = None
        for channel_name in channel_names:
            if '<>{}'.format(self.pid) in channel_name:
                category = channel_name.split('<>')[0]
        if category is None or category == COMMAND_CATEGORY:
            self.logger.warn(u'category 为空的通知：%s', msg)
            return
        self.notify_unreads[category] = self.notify_unreads.get(
            category, 0
        ) + 1
        self.update_unread_count()

        context = event_data.get('context', {})
        attachments = event_data.get('attachments', [])
        action = self.action_translations.get(
            event_data.get('action'),
            event_data.get('action')
        )
        from_user = event_data.get('from').get('name')
        title = _('[{}][Notify] {} {}').format(
            self.instance_name, from_user, action
        )
        body = event_data.get('body')
        if context:
            body = (
                _('{}\nrelated: {}') if body else _('{}related: {}')
            ).format(
                body or '', context.get('title')
            )
        if attachments:
            body = (
                _('{}\nattachments: ') if body else _('{}attachments: ')
            ).format(body or '')
            for i in xrange(len(attachments)):
                if i < 3:
                    body = '{}\n  {}'.format(body, attachments[i].get('title'))
                else:
                    body = _('{}\n  {} items').format(body, len(attachments))
                    break
        if self.__notification:
            ui_client.message(title, body)

    def handle_chat_message(self, msg):
        '''处理聊天消息'''
        event_data = msg['event_data']
        from_user = event_data.get('from').get('name')
        from_user_id = event_data.get('from').get('id')
        # 自己发送的消息不做提醒
        if from_user_id == self.pid:
            return

        # 更新未读数
        target_user = self.get_target_user(event_data['channel_name'])
        if target_user and target_user not in self.conversations:
            self.conversations.append(target_user)
            self.update_unread_count()

        # 别的客户端聚焦在当前会话上，对于这个会话就无需提醒，对其余会话正常提醒
        # 此处逻辑存疑
        if not self.enabled:
            self.logger.debug(
                u'有其他客户端聚焦在聊天会话 %s 上，因此不进行提醒：%s',
                self.current_conversation, event_data
            )
            return

        context = event_data.get('context', {})
        if context:
            body = _('[Image] {}').format(context.get('title'))
        else:
            body = event_data.get('body')
        title = _('[{}][Chat] {} said').format(self.instance_name, from_user)
        if self.__notification:
            ui_client.message(title, body)

    def handle_mark_read_message(self, msg):
        '''处理已读消息'''
        now = time.time()
        event_data = msg.get('event_data', {})
        msg_time = event_data.get('timestamp', now)
        if now - msg_time > 60 or msg_time <= self.last_sync:
            self.logger.debug(u'忽略过期同步消息')
            return
        client_id = msg['client_id']
        channel_type = event_data['channel_type']
        channel_name = event_data['channel_name']

        if channel_type in ('notify',):
            # 通知已读
            self.notify_unreads.pop(channel_name, 0)
        elif channel_type in ('group', 'private'):
            # 消息已读
            if channel_name in self.conversations:
                self.conversations.remove(channel_name)

        self.logger.debug(
            u'通知统计: %s，聊天统计: %s',
            self.notify_unreads, self.conversations
        )
        self.update_unread_count()
        self.last_sync = msg_time
        self.last_sync_from = client_id
        self.logger.debug(
            u'已从客户端 %s 同步，消息提醒%s，当前会话：%s，同步时间：%s',
            self.last_sync_from, (u'启用' if self.enabled else u'禁用'),
            self.current_conversation, self.last_sync
        )

    def update_unread_count(self):
        self.unread_count = len(
            self.conversations
        ) + sum(self.notify_unreads.values())

    def _get_current_conversation(self, conversations):
        '''从会话列表中取出当前会话的 ID'''
        for conversation in conversations.values():
            if conversation.get('current', False):
                return conversation['channel']
        return None

    def handle_connection_msg(self, msg):
        '''
        处理其他客户端的连接消息
        包括：监听其他客户端的 last will，更新聚焦状态
        '''
        data = msg['event_data']
        to_me = msg.get('client_id', self.userdata['client_id'])

        if to_me:
            if data['status'] == 'online':
                self.online = self.client.connected = True
                self.logger.info(u'消息客户端上线')
                # 上线后查询未读，并更新菜单的未读数
                self.check_unreads()

            elif data['status'] == 'offline':
                self.logger.debug(u'消息客户端已经离线')
                self.online = self.client.connected = False
                self.reapply_required = True

            elif data['status'] == 'fail':
                self.logger.debug(u'消息客户端连接失败: %s', msg)
                self.stop()

            elif data['status'] == 'kill':
                self.logger.info(u'连接被服务端杀死: %s', msg)
                # 服务端杀死，就退出，不要重连，不然永远杀不死了
                self.stop_event.set()
        else:

            # 其他客户端下线，更新消息提醒设置
            to_client = msg.get('client_id')
            if data['status'] in ('offline', 'fail', 'refuse', 'kill', ):
                if self.last_sync_from == to_client and not self.enabled:
                    focused = data.get('focus', None)
                    maxmized = data.get('max', None)
                    if maxmized is not None and not maxmized:
                        focused = False
                    self.enabled = not focused
                    self.last_sync = time.time()
                    self.last_sync_from = None
                    self.current_conversation = self._get_current_conversation(
                        data.get('conversations', {})
                    ) or self.current_conversation
                    self.logger.debug(
                        u'消息提醒启用：%s，当前会话：%s，同步时间：%s，同步于：%s',
                        self.enabled, self.current_conversation,
                        self.last_sync, self.last_sync_from
                    )

    def gen_connection_msg(self, userdata, online):
        '''生成连接消息'''
        return {
            'event_name': 'connection',
            'account': userdata['account'],
            'client_id': userdata['client_id'],
            'user_id': userdata['user_id'],
            'instance': userdata['instance'],
            'event_data': {
                'status': 'online' if online else 'offline',
                'instances': userdata['topics'].keys(),
                'timestamp': time.time(),
                'appname': 'Sitebot',
            }
        }

    def send_connection_msg(self, online, userdata=None):
        '''发送连接消息'''
        self.client.publish(
            topic=self.client.zopen_topic_sys,
            payload=json.dumps(
                self.gen_connection_msg(
                    self.userdata, online
                )
            ),
            qos=MSG_QOS, retain=False
        )
        self.logger.debug(u'%s消息已经发送', u'上线' if online else u'下线')
