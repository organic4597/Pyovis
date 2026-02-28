FROM python:3.11-slim

RUN pip install --no-cache-dir \
    requests \
    pydantic \
    fastapi \
    httpx \
    numpy \
    pillow \
    matplotlib \
    pandas \
    scipy \
    pygame \
    pytest \
    colorama \
    click \
    rich

RUN useradd -m -u 1000 sandbox
WORKDIR /workspace
USER sandbox

CMD ["python"]
