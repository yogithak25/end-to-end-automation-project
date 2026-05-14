FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN apt-get update && apt-get install -y procps
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "main.py"]
