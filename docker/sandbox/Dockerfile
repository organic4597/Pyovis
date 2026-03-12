FROM python:3.11-slim

# 시스템 패키지: Xvfb (가상 디스플레이) + OpenGL 런타임
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb \
    xauth \
    libgl1 \
    libgl1-mesa-dri \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libx11-6 \
    && rm -rf /var/lib/apt/lists/*

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
    PyOpenGL \
    PyOpenGL_accelerate \
    pytest \
    colorama \
    click \
    rich

RUN useradd -m -u 1000 sandbox
WORKDIR /workspace
USER sandbox

CMD ["python"]
