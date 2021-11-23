FROM python:2.7.15
ARG PIP_INDEX_URL=https://mirrors.ustc.edu.cn/pypi/web/simple
ARG PIP_TRUSTED_HOST=mirrors.aliyun.com

RUN apt update && \
    apt-get install -y --no-install-recommends gcc && \
    apt clean

RUN pip install --trusted-host $PIP_TRUSTED_HOST --index-url $PIP_INDEX_URL --upgrade pip

COPY gitlab/sitebot/requirements.txt /
RUN pip wheel --wheel-dir=/root/wheels -i $PIP_INDEX_URL -r /requirements.txt

COPY gitlab/sitebot/docker/requirements.txt /sitebot_requirements.txt
RUN pip wheel --wheel-dir=/root/wheels -i $PIP_INDEX_URL -r /sitebot_requirements.txt

# --no-index, --find-links 参数指定从本地安装
RUN pip install --ignore-installed --no-index --find-links=/root/wheels -r requirements.txt
RUN pip install --ignore-installed --no-index --find-links=/root/wheels -r /sitebot_requirements.txt
RUN pip install --trusted-host $PIP_TRUSTED_HOST --index-url $PIP_INDEX_URL pyOpenSSL

COPY gitlab/edo_client /edo_client

RUN cd /edo_client && \
    python setup.py install && \
    rm -rf /edo_client

COPY gitlab/sitebot /sitebot
RUN mv /sitebot/docker/sitebot.py /sitebot.py && \
    mv /sitebot/docker/run /usr/local/bin/run && \
    chmod +x /usr/local/bin/run

WORKDIR /sitebot
ENTRYPOINT ["/usr/local/bin/run"]
CMD ["start"]
EXPOSE 4999
