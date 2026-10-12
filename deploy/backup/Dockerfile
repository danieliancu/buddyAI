# Backup container: pg_dump (same PostgreSQL major as the database) + age (encryption) + rclone (off-site).
# Build context: this directory (deploy/backup). See deploy/README.md section 8.
FROM postgres:16-alpine

RUN apk add --no-cache age rclone

COPY backup.sh run.sh restore.sh /usr/local/bin/
# Strip CR in case the scripts were checked out with Windows line endings.
RUN sed -i 's/\r$//' /usr/local/bin/backup.sh /usr/local/bin/run.sh /usr/local/bin/restore.sh \
 && chmod 0755 /usr/local/bin/backup.sh /usr/local/bin/run.sh /usr/local/bin/restore.sh

# Not a database server: no postgres entrypoint. `docker run <image> restore.sh ...` also works.
ENTRYPOINT []
CMD ["/usr/local/bin/run.sh"]
