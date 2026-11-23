FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 10001 botuser

COPY . .
RUN chown -R botuser:botuser /app

USER botuser

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

CMD ["python", "bot.py"]
