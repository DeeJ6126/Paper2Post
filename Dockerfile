# syntax=docker/dockerfile:1
# Paper2Post — AI 论文推文自动生成系统

FROM python:3.13-slim

# 无字节码、无缓冲、无 pip 缓存
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖以获得更好的层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制工程（.dockerignore 排除 vendor / outputs / 生成物 等）
COPY . .

# 镜像内使用 pip 安装的依赖；移除工作区自治的 vendor；
# 在镜像内生成示例论文，保证默认命令可跑
RUN rm -rf vendor outputs_test \
    && python scripts/make_sample_pdf.py

# 应用入口
ENTRYPOINT ["python", "paper2post.py"]
CMD ["--help"]
