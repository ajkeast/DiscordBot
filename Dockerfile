FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Non-root runtime user. Production compose adds gid 1000 (group_add) so this
# user can read the minecraft_data volume (owned 1000:1000, root mode 750).
RUN useradd --create-home --uid 10001 botuser

COPY . .
RUN chown -R botuser:botuser /app

USER botuser

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg

CMD ["python", "bot.py"]
