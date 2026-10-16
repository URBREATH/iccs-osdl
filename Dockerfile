FROM python:3.11-slim

WORKDIR /app

COPY . /app
COPY requirements.txt .
ENV PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir -r requirements.txt
EXPOSE 8600

# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8600", "--reload"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8600", "--proxy-headers"]