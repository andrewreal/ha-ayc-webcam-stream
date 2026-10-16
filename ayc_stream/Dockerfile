ARG BUILD_FROM=ghcr.io/home-assistant/aarch64-base:3.20
FROM ${BUILD_FROM}

RUN apk add --no-cache ffmpeg python3 py3-pillow font-dejavu curl tzdata coreutils procps

COPY rootfs /
RUN chmod a+x /usr/bin/*.sh /usr/bin/update_caption.py

CMD [ "/usr/bin/run.sh" ]
