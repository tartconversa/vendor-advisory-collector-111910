FROM mcr.microsoft.com/playwright/python:v1.56.0-noble@sha256:a7f6cf3ae520c9d670ad956572c13747ed5abdbba5123a01526f873ed1662528
WORKDIR /workspace
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY source ./source
COPY tests ./tests
EXPOSE 8765
CMD ["python3", "-m", "source.app", "--host", "0.0.0.0", "--port", "8765"]
