FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY admin_roles.py /app/admin_roles.py
COPY auth_helpers.py /app/auth_helpers.py
COPY telegram_notifications.py /app/telegram_notifications.py
COPY worker.py /app/worker.py

CMD ["python", "worker.py"]
