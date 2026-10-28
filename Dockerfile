# neochumby-classic in a container: the renderer and the web page (docker-compose.yml).
FROM python:3.12-slim
COPY . /src
RUN pip install --no-cache-dir "/src[heif]" && rm -rf /src
ENV NEOCHUMBY_CLASSIC_STATE=/state \
    NEOCHUMBY_CLASSIC_OUT=/state/out \
    NEOCHUMBY_CLASSIC_ADMIN=/neochumbyd/admin.sock \
    NEOCHUMBY_CLASSIC_TREE=/neochumbyd/tree \
    NEOCHUMBY_CLASSIC_ASSETS=/assets \
    NEOCHUMBY_CLASSIC_WEB=0.0.0.0:8080
EXPOSE 8080
CMD ["neochumby-classic-renderer"]
