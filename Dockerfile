FROM python:3.12-slim

# LibreOffice for Word doc conversion
RUN apt-get update && \
    apt-get install -y --no-install-recommends libreoffice-writer && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY label_printer.py web.py entrypoint.py config.json ./
COPY static/ ./static/

EXPOSE 5555

CMD ["python", "-u", "entrypoint.py"]
