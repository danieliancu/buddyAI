# PostgreSQL 16 (the same official Alpine image as before) plus the pgvector extension, for long-term memory.
#
# Same base image, same major version, same musl C library: the existing data volume is used as it is (no
# dump/restore, no reindex). The Debian-based pgvector/pgvector image is deliberately not used: switching
# C library can change text sort order and silently corrupt text indexes.
#
# Installing the extension files changes nothing in the database; `CREATE EXTENSION vector` is run by
# migration 0022 when the server starts. Before that, going back to `image: postgres:16-alpine` is safe;
# after it, keep this image (the memory_embeddings table needs the extension).
FROM postgres:16-alpine

ARG PGVECTOR_VERSION=0.8.7

# The JIT bitcode needs the clang/llvm version PostgreSQL itself was built with: read it from its build settings.
RUN set -eux; \
    llvm="$(sed -n 's/^CLANG = clang-//p' /usr/local/lib/postgresql/pgxs/src/Makefile.global)"; \
    apk add --no-cache --virtual .build-deps build-base "clang${llvm}" "llvm${llvm}" git; \
    git clone --depth 1 --branch "v${PGVECTOR_VERSION}" https://github.com/pgvector/pgvector.git /tmp/pgvector; \
    cd /tmp/pgvector; \
    make OPTFLAGS=""; \
    make install; \
    cd /; \
    rm -rf /tmp/pgvector; \
    apk del .build-deps
