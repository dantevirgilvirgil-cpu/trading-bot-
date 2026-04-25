FROM python:3.11-slim
WORKDIR /app
COPY requirements.text requirements.text
RUN pip install --no-cache-dir -r requirements.text
COPY . .
CMD ["python", "main-1.py"]
