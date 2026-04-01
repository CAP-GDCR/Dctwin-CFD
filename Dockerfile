FROM python:3.10 as builder

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on

RUN pip install poetry
COPY . /opt/src
WORKDIR /opt/src
RUN poetry build

FROM python:3.10

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on

COPY --from=builder /opt/src/dist/*.whl /opt/dist/
RUN apt-get update && apt-get upgrade -y && \
    apt-get install cmake build-essential pigz libglx-mesa0 -y && \
    pip install /opt/dist/*.whl && \
    apt-get purge build-essential cmake -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/app
