ARG PYTHON_IMAGE=python:3.12-slim@sha256:5e2bdb1b0d542d5039cffb8181c65930093949194a65c365dfb1b0fe13a4fa5b
FROM ${PYTHON_IMAGE}
USER 0:0
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG APT_MIRROR=https://deb.debian.org
ENV PATH=/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    EXECD_ISOLATION_CONFIG=/opt/zhiheng/isolation.toml
RUN (getent group 10001 || /usr/sbin/groupadd --gid 10001 workspace) \
    && (getent passwd 10001 || /usr/sbin/useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent workspace) \
    && mkdir -p /workspace /run/zhiheng /opt/zhiheng \
    && chmod 0755 /workspace /var/tmp \
    && chmod 0700 /run/zhiheng
RUN sed -i "s|http://deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=2 update \
    && apt-get -o Acquire::https::Timeout=30 -o Acquire::Retries=2 install --yes --no-install-recommends libreoffice-writer libreoffice-calc fonts-noto-cjk ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.in requirements.lock /opt/zhiheng/
RUN /usr/local/bin/python -m pip install --no-cache-dir --timeout 60 --retries 3 --require-hashes --only-binary=:all: --index-url ${PIP_INDEX_URL} -r /opt/zhiheng/requirements.lock
COPY verify_runtime.py /opt/zhiheng/verify_runtime.py
RUN /usr/local/bin/python -I /opt/zhiheng/verify_runtime.py --manifest /opt/zhiheng/runtime-manifest.json
COPY runtime_runner.py environment_runner.py /opt/zhiheng/
COPY isolation.toml /opt/zhiheng/isolation.toml
RUN chmod 0555 /opt/zhiheng/*.py
WORKDIR /workspace
CMD ["tail", "-f", "/dev/null"]
