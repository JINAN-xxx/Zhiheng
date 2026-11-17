ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}
USER 0:0
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PATH=/opt/opensandbox-manager/bin:/usr/local/bin:/usr/bin:/bin \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN /usr/local/bin/python -m venv /opt/opensandbox-manager
COPY requirements.txt /opt/manager-requirements.txt
RUN python -m pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r /opt/manager-requirements.txt
COPY launch.py healthcheck.py /opt/
CMD ["python", "/opt/launch.py"]
