ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

# 安装必要工具
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
      nginx \
      supervisor \
      gettext-base \
      ffmpeg \
      fontconfig \
      fonts-noto-cjk \
      fonts-liberation && \
    rm -rf /var/lib/apt/lists/*

# 创建工作目录
WORKDIR /app

ARG FNOS_URL
ARG RUN_AND_UPDATE_WEB=false
ENV FNOS_URL=${FNOS_URL}
ENV RUN_AND_UPDATE_WEB=${RUN_AND_UPDATE_WEB}
ENV TZ=Asia/Shanghai
ENV PYTHONPATH=/app
VOLUME ["/app/data"]

# 复制并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制 Flask 应用及静态资源
COPY . .
RUN test -s /app/dist/index.html

# 复制 nginx 和 supervisor 配置
COPY config/nginx.conf.template /etc/nginx/nginx.conf.template
COPY config/supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# 复制启动脚本
COPY config/entrypoint.sh /entrypoint.sh
RUN sed -i 's/\r$//' /entrypoint.sh && chmod +x /entrypoint.sh
# 暴露 HTTP 端口
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1/api/getFnUrl', timeout=3).read()" || exit 1

# 启动 supervisor（它会同时拉起 nginx 和 gunicorn）
ENTRYPOINT ["/entrypoint.sh"]

CMD ["supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
