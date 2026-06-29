#!/bin/bash
docker compose --env-file .env.dev --project-name guaia-threads-collector-dev "$@"
