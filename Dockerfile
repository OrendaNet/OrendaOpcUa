FROM python:3.12-slim-bookworm
LABEL org.opencontainers.image.source="https://github.com/OrendaNet/OrendaOpcUa"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=3101
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --prefer-binary -r requirements.txt && install -d -o 1000 -g 1000 /data
COPY opcua_box opcua_box
COPY public public
COPY tools/box_scrape.py tools/box_scrape.py
USER 1000:1000
EXPOSE 3101
CMD ["python", "-m", "opcua_box.web"]
