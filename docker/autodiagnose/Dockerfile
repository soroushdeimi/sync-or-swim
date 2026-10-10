FROM docker:28.5.1-cli

RUN apk add --no-cache python3 py3-yaml

COPY webhook.py /usr/local/bin/webhook.py
RUN chmod 0755 /usr/local/bin/webhook.py

ENTRYPOINT ["python3", "/usr/local/bin/webhook.py"]
