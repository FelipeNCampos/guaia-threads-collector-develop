#!/bin/bash
docker compose --env-file .env.prod --project-name guaia-threads-collector-prod "$@"
