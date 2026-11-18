#!/bin/sh
# The official MySQL entrypoint runs this hook on first initialization.
# Use a subshell because the hook may be sourced or executed.
(
    set -eu
    : "${AGENTOS_MYSQL_PASSWORD:?Missing runtime database password}"
    password_hex=$(printf '%s' "$AGENTOS_MYSQL_PASSWORD" | od -An -v -tx1 | tr -d '[:space:]')
    MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql --protocol=socket --user=root <<SQL
SET SESSION sql_log_bin=0;
SET SESSION sql_mode='';
SET @ddl=CONCAT('CREATE USER ''agentos_app''@''%'' IDENTIFIED BY ',
                QUOTE(CONVERT(UNHEX('$password_hex') USING utf8mb4)));
PREPARE create_app FROM @ddl;
EXECUTE create_app;
DEALLOCATE PREPARE create_app;
GRANT SELECT, INSERT, UPDATE ON agentos.* TO 'agentos_app'@'%';
SQL
)
