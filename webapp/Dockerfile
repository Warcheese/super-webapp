FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SUPER_DATA_DIR=/data
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 1000 --create-home super
COPY . .
RUN mkdir -p /data /app/staticfiles && chown -R super:super /data /app/staticfiles
USER super
EXPOSE 8000
CMD ["sh", "entrypoint.sh"]
