# neochumby-classic in a container: the renderer and the web page (docker-compose.yml).
FROM python:3.12-slim
COPY . /src
RUN pip install --no-cache-dir "/src[heif]" && rm -rf /src
# The container's own paths (docker-compose.yml binds them), and its own network: 0.0.0.0
# here is the container's, and compose publishes it on one host address. HOME is /tmp, the
# only writable place outside /state with a read-only root.
ENV NEOCHUMBY_CLASSIC_STATE=/state \
    NEOCHUMBY_CLASSIC_OUT=/state/out \
    NEOCHUMBY_CLASSIC_ADMIN=/run/neochumbyd/admin.sock \
    NEOCHUMBY_CLASSIC_TREE=/tree \
    NEOCHUMBY_CLASSIC_ASSETS=/assets \
    NEOCHUMBY_CLASSIC_WEB=0.0.0.0:8080 \
    HOME=/tmp
EXPOSE 8080
CMD ["neochumby-classic-renderer"]
