#!/bin/bash
docker compose --env-file .env.train --project-name guaia-threads-collector-train "$@"
