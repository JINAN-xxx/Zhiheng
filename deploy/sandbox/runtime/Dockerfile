ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
USER 0:0
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG APT_MIRROR=https://deb.debian.org
ENV PATH=/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    EXECD_ISOLATION_CONFIG=/opt/zhiheng/isolation.toml
RUN (getent group 10001 || groupadd --gid 10001 workspace) \
    && (getent passwd 10001 || useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent workspace) \
    && mkdir -p /workspace /run/zhiheng /opt/zhiheng \
    && chmod 0755 /workspace /var/tmp \
    && chmod 0700 /run/zhiheng
RUN sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=2 update \
    && apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=2 install --yes --no-install-recommends libreoffice-writer libreoffice-calc fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/zhiheng/requirements.txt
RUN /usr/local/bin/python -m pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r /opt/zhiheng/requirements.txt
COPY runtime_runner.py environment_runner.py /opt/zhiheng/
COPY isolation.toml /opt/zhiheng/isolation.toml
RUN chmod 0555 /opt/zhiheng/*.py
WORKDIR /workspace
CMD ["tail", "-f", "/dev/null"]
