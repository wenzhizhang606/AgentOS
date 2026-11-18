FROM python:3.12-slim-bookworm AS build

WORKDIR /build
COPY pyproject.toml LICENSE ./
COPY src/ ./src/
ARG PIP_INDEX_URL=https://pypi.org/simple
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN groupadd --gid 10001 agentos && useradd --uid 10001 --gid 10001 --no-create-home agentos
COPY --from=build /install/ /usr/local/
COPY deploy/migrate.py ./deploy/migrate.py
COPY migrations/mysql/ ./migrations/mysql/
COPY examples/m1/policy.txt ./examples/m1/policy.txt

USER agentos
EXPOSE 8000
CMD ["agentos", "api", "--host", "0.0.0.0", "--port", "8000"]
