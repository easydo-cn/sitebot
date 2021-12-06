ARG TAG=""
ARG CPU=amd64

FROM docker.easydo.cn:5000/edo_base-$CPU:x-master

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 复制代码
WORKDIR /
ADD src /sitebot
ADD run run

# 安装sitebot依赖、edo_client
RUN pip2 install -r /sitebot/requirements.txt -i $PIP_INDEX_URL

RUN ln -s /run /usr/local/bin/run && \
    chmod +x /usr/local/bin/run

WORKDIR /sitebot
ENTRYPOINT ["/usr/local/bin/run"]
CMD ["start"]
EXPOSE 4999
