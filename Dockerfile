FROM python:3.12-slim

WORKDIR /app

# the dependencies first, so a change in the code does not reinstall them
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY pyproject.toml ./
COPY src/ src/
COPY tests/ tests/

# the program's output reaches the terminal as it is printed, not when the buffer fills
ENV PYTHONUNBUFFERED=1

# logs/ and reports/ are written under /app; mount them to keep the files
VOLUME ["/app/logs", "/app/reports"]

CMD ["python", "src/main.py"]
