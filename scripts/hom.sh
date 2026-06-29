#!/bin/bash
docker compose --env-file .env.hom --project-name guaia-threads-collector-hom "$@"
