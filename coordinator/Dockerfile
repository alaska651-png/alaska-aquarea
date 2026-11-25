FROM python:3.14-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir aiohttp==3.14.5
RUN apt-get update && apt-get install --no-install-recommends -y tzdata
COPY engine.py daemon.py supervise.py health.py mode_policy.py ./
CMD ["python","supervise.py"]
