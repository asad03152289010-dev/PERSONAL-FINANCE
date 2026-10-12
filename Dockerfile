FROM python:3.12-slim
WORKDIR /app
RUN useradd -u 10001 -m appuser
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py index.html ./
COPY static ./static
RUN mkdir /data && chown appuser:appuser /data
USER appuser
ENV APP_ENV=production DATABASE_PATH=/data/finance.db PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn app:application --workers 1 --bind 0.0.0.0:${PORT} --access-logfile -"]
