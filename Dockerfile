FROM python:3.11-slim
WORKDIR /app
COPY requirements.text requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "robot_trading_bei-2.py"]
