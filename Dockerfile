FROM python:3.12-alpine
ENV CONFIG_PATH=/etc/blaster.yml \
    METRICS_RESULT=/srv/metrics.dat \
    PYTHONUNBUFFERED=1

WORKDIR /mnt
COPY ./requirements.txt .
RUN pip3 install -r requirements.txt

WORKDIR /opt
COPY src/* ./
ENTRYPOINT [""]
CMD [""]