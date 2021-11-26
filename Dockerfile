ARG TAG=""
ARG CPU=amd64

FROM docker.easydo.cn:5000/edo_base-$CPU:x-master

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
ARG TAG
ARG GIT_USER
ARG GIT_PWD

# 复制代码
WORKDIR /
RUN git clone https://$GIT_USER:$GIT_PWD@github.com/easydo-cn/sitebot.git &&\
    git clone https://$GIT_USER:$GIT_PWD@github.com/edo_client.git

# 安装sitebot依赖、edo_client
RUN pip2 install -r /sitebot/requirements.txt -i $PIP_INDEX_URL &&\
    cd /edo_client && python setup.py install &&\
    rm -rf /edo_client

RUN ln -s /sitebot/run /usr/local/bin/run && \
    chmod +x /usr/local/bin/run

WORKDIR /sitebot
ENTRYPOINT ["/usr/local/bin/run"]
CMD ["start"]
EXPOSE 4999
